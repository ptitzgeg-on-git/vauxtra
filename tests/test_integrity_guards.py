"""The database was trusted to say no, and it never did.

Three holes, all of the same family -- the API acted first and let SQLite sort it out
afterwards:

- deleting a provider blanked every service that used it, without a word. The frontend had
  always sent `?force=true` and always known how to render a `detail.services` list; the API
  had no `force` parameter and no 409, so that dialog was unreachable code.
- both service write endpoints called the proxy and the DNS provider *before* inserting the
  row. Foreign keys are on, so one unknown `tag_ids` entry raised `IntegrityError` after the
  hostname had been published: the transaction rolled back and the live route survived with
  nothing in the database describing it.
- nothing stopped two services from claiming one hostname. They push over each other, and
  the drift check then reports whichever lost as permanently wrong.
- and the check that closed that last hole was only as fresh as the moment it ran. Both
  service write endpoints, and both rename endpoints, read the name, did the work, then
  wrote it: a second operator taking the name inside that window left the UNIQUE index as
  the only thing that still knew, and it answered 500 -- after the hostname had been
  published on the providers.
"""

import ast
import inspect
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import environments as environments_api
from app.api import providers as providers_api
from app.api import services as services_api
from app.api import tags as tags_api
from app.api import webhooks as webhooks_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _SilentProvider:
    """Accepts everything, and remembers that it was asked."""

    def __init__(self, calls: list):
        self._calls = calls

    def find_best_certificate(self, _domain):
        return None

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._calls.append(("create_host", domain))
        return {"id": 4242}

    def update_host(self, host_id, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._calls.append(("update_host", host_id, domain))
        return True

    def delete_host(self, host_id):
        self._calls.append(("delete_host", host_id))
        return True

    def list_rewrites(self):
        return []

    def add_rewrite(self, domain, ip):
        self._calls.append(("add_rewrite", domain, ip))
        return True

    def delete_rewrite(self, domain, ip):
        self._calls.append(("delete_rewrite", domain, ip))
        return True


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "integrity.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _logs(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [r["message"] for r in rows]

    def _seed_providers(self) -> None:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def _seed_service(self, subdomain: str = "app", **cols) -> int:
        fields = {
            "subdomain": subdomain,
            "domain": "example.com",
            "target_ip": "10.0.0.9",
            "target_port": 8080,
            "expose_mode": "proxy_dns",
            "proxy_provider_id": 2,
            "dns_provider_id": 3,
            "dns_ip": "198.51.100.7",
        }
        fields.update(cols)
        names = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        conn = models.get_db()
        cur = conn.execute(f"INSERT INTO services ({names}) VALUES ({marks})", tuple(fields.values()))
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def _service_body(self, **overrides) -> services_api.ServiceIn:
        payload = {
            "subdomain": "app",
            "domain": "example.com",
            "target_ip": "10.0.0.10",
            "target_port": 8080,
            "proxy_provider_id": 2,
            "dns_ip": "198.51.100.7",
        }
        payload.update(overrides)
        return services_api.ServiceIn(**payload)

    def _seed_webhook(self, name: str = "on-call", **cols) -> int:
        fields = {
            "name": name,
            "url": "json://hook.test/x",
            "enabled": 1,
            "scope_type": "provider",
            "scope_ref_id": 2,
        }
        fields.update(cols)
        names = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        conn = models.get_db()
        cur = conn.execute(f"INSERT INTO webhooks ({names}) VALUES ({marks})", tuple(fields.values()))
        wid = cur.lastrowid
        conn.commit()
        conn.close()
        return wid


    def _seed_template(self, name: str = "standard", **cols) -> int:
        fields = {
            "name": name,
            "forward_scheme": "http",
            "target_port": 8080,
            "expose_mode": "proxy_dns",
            "proxy_provider_id": 2,
            "tag_ids_json": "[]",
        }
        fields.update(cols)
        names = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        conn = models.get_db()
        cur = conn.execute(
            f"INSERT INTO service_templates ({names}) VALUES ({marks})", tuple(fields.values())
        )
        tid = cur.lastrowid
        conn.commit()
        conn.close()
        return tid


# ─────────────────────────────────────────────────────────────────────────────────────
# Deleting a provider that services still use
# ─────────────────────────────────────────────────────────────────────────────────────
class DeleteProviderAsksFirstTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        p = patch.object(providers_api, "require_auth_or_setup", lambda _req, scope=None: None)
        p.start()
        self.addCleanup(p.stop)
        self._seed_providers()

    def test_an_unused_provider_is_deleted_without_a_question(self) -> None:
        result = providers_api.delete_provider(3, _request("DELETE"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["unlinked_services"], [])
        conn = models.get_db()
        self.assertIsNone(conn.execute("SELECT 1 FROM providers WHERE id=3").fetchone())
        conn.close()

    def test_a_used_provider_is_refused_with_the_list_the_ui_renders(self) -> None:
        sid = self._seed_service()

        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(2, _request("DELETE"))

        self.assertEqual(caught.exception.status_code, 409)
        detail = caught.exception.detail
        self.assertIn("message", detail)
        self.assertEqual([s["id"] for s in detail["services"]], [sid])
        self.assertEqual(detail["services"][0]["fqdn"], "app.example.com")
        self.assertIn("proxy", detail["services"][0]["roles"])
        conn = models.get_db()
        self.assertIsNotNone(conn.execute("SELECT 1 FROM providers WHERE id=2").fetchone())
        conn.close()

    def test_a_push_target_alone_is_enough_to_refuse(self) -> None:
        """The extra targets live in their own table and cascade away just as silently."""
        sid = self._seed_service(proxy_provider_id=None, dns_provider_id=None)
        conn = models.get_db()
        conn.execute(
            "INSERT INTO service_push_targets (service_id, provider_id, role) VALUES (?,2,'proxy')",
            (sid,),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(2, _request("DELETE"))

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["services"][0]["roles"], ["extra proxy"])

    def test_force_deletes_and_says_what_it_unlinked(self) -> None:
        sid = self._seed_service()

        result = providers_api.delete_provider(2, _request("DELETE"), force=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["unlinked_services"], [sid])
        conn = models.get_db()
        self.assertIsNone(conn.execute("SELECT 1 FROM providers WHERE id=2").fetchone())
        row = conn.execute("SELECT proxy_provider_id FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        self.assertIsNone(row["proxy_provider_id"], "the FK still blanks the link, as designed")
        self.assertTrue(
            any("unlinked" in m and "app.example.com" in m for m in self._logs()),
            self._logs(),
        )

    def test_an_unknown_provider_is_still_a_404(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(999, _request("DELETE"))
        self.assertEqual(caught.exception.status_code, 404)

    def test_every_reference_the_schema_declares_is_asked_about(self) -> None:
        """The gate. A column added to the schema that points at a provider is a thing the
        deletion silently takes with it, and the two readers below are the only things that
        can put it in front of the operator first.

        This is how `service_templates` was missed: the table was added with three provider
        columns, `ON DELETE SET NULL` like the ones on `services`, and nothing swept the
        delete path for it. A provider only a template named was deleted with no question
        asked at all, and the template came back with an empty provider field.

        Reading the FK declarations rather than a hand-kept list is the point: a fourth
        table with a provider column fails here the day it is written, not the day somebody
        deletes a provider.
        """
        conn = models.get_db()
        tables = [
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r["name"].startswith("sqlite_")
        ]
        declared = {
            (table, fk["from"])
            for table in tables
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})")
            if fk["table"] == "providers"
        }
        conn.close()
        self.assertTrue(declared, "no provider reference found at all; this test has no subject")

        source = (
            inspect.getsource(providers_api._provider_dependents)
            + inspect.getsource(providers_api._provider_template_dependents)
            + inspect.getsource(providers_api._provider_webhook_dependents)
        )
        unread = sorted(
            f"{table}.{column}"
            for table, column in declared
            if column not in source or table not in source
        )
        self.assertEqual(
            unread,
            [],
            "these columns point at a provider and nothing asks the operator about them "
            "before the deletion blanks them",
        )

    def test_a_provider_only_a_template_names_is_refused_too(self) -> None:
        """No services at all. This used to fall straight through to the DELETE."""
        tid = self._seed_template()

        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(2, _request("DELETE"))

        self.assertEqual(caught.exception.status_code, 409)
        detail = caught.exception.detail
        self.assertEqual(detail["services"], [], "nothing is published; the list is empty")
        self.assertEqual([d["id"] for d in detail["templates"]], [tid])
        self.assertEqual(detail["templates"][0]["roles"], ["proxy"])
        self.assertIn("standard", detail["message"])
        self.assertNotIn(
            "withdraw=true",
            detail["message"],
            "a template publishes nothing, so there is no record to withdraw",
        )
        conn = models.get_db()
        self.assertIsNotNone(conn.execute("SELECT 1 FROM providers WHERE id=2").fetchone())
        conn.close()

    def test_a_template_is_named_alongside_the_services(self) -> None:
        """Both halves in one answer, and the service language untouched by the template."""
        sid = self._seed_service()
        tid = self._seed_template(dns_provider_id=2)

        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(2, _request("DELETE"))

        detail = caught.exception.detail
        self.assertEqual([s["id"] for s in detail["services"]], [sid])
        self.assertEqual([d["id"] for d in detail["templates"]], [tid])
        self.assertEqual(sorted(detail["templates"][0]["roles"]), ["dns", "proxy"])
        self.assertIn("withdraw=true", detail["message"], "the services half is unchanged")

    def test_force_says_which_templates_it_blanked_and_logs_them(self) -> None:
        tid = self._seed_template()

        result = providers_api.delete_provider(2, _request("DELETE"), force=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["unlinked_templates"], [tid])
        conn = models.get_db()
        row = conn.execute(
            "SELECT proxy_provider_id FROM service_templates WHERE id=?", (tid,)
        ).fetchone()
        conn.close()
        self.assertIsNone(row["proxy_provider_id"], "the FK blanks it, which is the whole point")
        self.assertTrue(
            any("standard" in m and "service template" in m for m in self._logs()),
            self._logs(),
        )

    def test_a_provider_only_a_webhook_watches_is_refused_too(self) -> None:
        """No services, no templates. The eighth reference, and the only undeclared one.

        `webhooks.scope_ref_id` holds a provider id when `scope_type` says 'provider', and
        the column carries no foreign key. So the deletion did not blank it, did not cascade
        it and did not mention it: the row stayed exactly as the operator left it, pointing
        at an id that no longer existed, and `_service_matches_scope` answered False from
        then on for every service there is.
        """
        wid = self._seed_webhook()

        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(2, _request("DELETE"))

        self.assertEqual(caught.exception.status_code, 409)
        detail = caught.exception.detail
        self.assertEqual(detail["services"], [], "nothing is published; the list is empty")
        self.assertEqual(detail["templates"], [])
        self.assertEqual([d["id"] for d in detail["webhooks"]], [wid])
        self.assertIn("on-call", detail["message"])
        self.assertNotIn(
            "withdraw=true",
            detail["message"],
            "a webhook publishes nothing, so there is no record to withdraw",
        )
        conn = models.get_db()
        self.assertIsNotNone(conn.execute("SELECT 1 FROM providers WHERE id=2").fetchone())
        conn.close()

    def test_a_webhook_is_named_alongside_the_services(self) -> None:
        """Three kinds of dependent in one answer, each with its own language."""
        sid = self._seed_service()
        tid = self._seed_template(dns_provider_id=2)
        wid = self._seed_webhook()

        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_provider(2, _request("DELETE"))

        detail = caught.exception.detail
        self.assertEqual([s["id"] for s in detail["services"]], [sid])
        self.assertEqual([d["id"] for d in detail["templates"]], [tid])
        self.assertEqual([d["id"] for d in detail["webhooks"]], [wid])
        self.assertIn("withdraw=true", detail["message"], "the services half is unchanged")
        self.assertIn("service template", detail["message"])
        self.assertIn("notification webhook", detail["message"])

    def test_a_webhook_watching_another_provider_is_none_of_this_deletion_business(self) -> None:
        """The negative half of the same question, without which the census could be a
        constant: a webhook watching provider 3 must not appear when provider 2 goes."""
        self._seed_webhook(name="other-provider", scope_ref_id=3)

        result = providers_api.delete_provider(2, _request("DELETE"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["orphaned_webhooks"], [])
        self.assertFalse([m for m in self._logs() if "webhook" in m.lower()], self._logs())

    def test_a_webhook_scoped_to_everything_is_not_a_dependent_either(self) -> None:
        """`scope_type='all'` means the webhook never named a provider. `scope_ref_id` is
        NULL there, and a census that read the column without reading the type beside it
        would have counted it as soon as one provider happened to carry a matching id."""
        self._seed_webhook(name="everything", scope_type="all", scope_ref_id=None)

        result = providers_api.delete_provider(2, _request("DELETE"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["orphaned_webhooks"], [])

    def test_force_says_which_webhooks_it_orphaned_and_logs_them(self) -> None:
        """The journal is the only trace. Nothing else in the instance changes at all."""
        wid = self._seed_webhook()

        result = providers_api.delete_provider(2, _request("DELETE"), force=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["orphaned_webhooks"], [wid])
        self.assertTrue(
            any("on-call" in m and "notification webhook" in m for m in self._logs()),
            self._logs(),
        )

    def test_the_deletion_leaves_the_webhook_switched_on_which_is_why_it_is_said(self) -> None:
        """The measurement the whole change rests on.

        A service loses its provider link and the Services table shows the gap. A template
        loses its choice and the field comes back empty. A webhook loses nothing visible:
        same name, same URL, same enabled flag, same `scope_ref_id`. It reads as configured
        and armed, and it can never match again. If this ever starts failing because the
        deletion switches it off, the sentence and the journal line both have to be
        rewritten: they promise the operator that nothing here was turned off for them.
        """
        wid = self._seed_webhook()

        providers_api.delete_provider(2, _request("DELETE"), force=True)

        conn = models.get_db()
        row = conn.execute(
            "SELECT enabled, scope_type, scope_ref_id FROM webhooks WHERE id=?", (wid,)
        ).fetchone()
        self.assertIsNone(conn.execute("SELECT 1 FROM providers WHERE id=2").fetchone())
        conn.close()
        self.assertIsNotNone(row, "no foreign key, so nothing cascaded it away")
        self.assertEqual(row["enabled"], 1, "still on")
        self.assertEqual(row["scope_type"], "provider")
        self.assertEqual(row["scope_ref_id"], 2, "still pointing at the provider that is gone")

    def test_a_column_named_like_a_provider_link_is_asked_about_declared_or_not(self) -> None:
        """The companion to the FK sweep above, for the half that sweep cannot see.

        `PRAGMA foreign_key_list` answers about declared references, and the column that
        started this is not one: `webhooks.scope_ref_id` is a bare INTEGER that holds a
        provider id only when the word beside it says so. The FK sweep was complete and
        still missed it, which is exactly why this test sits next to it instead of inside.

        What is decidable without a declaration is the name. A column called
        `*_provider_id` holds a provider id whether or not anybody wrote REFERENCES beside
        it, and on the day one is added without that clause the schema stops cleaning up
        after the deletion and this is the only thing left that will say so.
        """
        conn = models.get_db()
        named = set()
        for table in [
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r["name"].startswith("sqlite_")
        ]:
            for col in conn.execute(f"PRAGMA table_info({table})"):
                if col["name"].endswith("_provider_id"):
                    named.add((table, col["name"]))
        conn.close()
        self.assertTrue(named, "no provider-shaped column found at all; this test has no subject")

        source = (
            inspect.getsource(providers_api._provider_dependents)
            + inspect.getsource(providers_api._provider_template_dependents)
            + inspect.getsource(providers_api._provider_webhook_dependents)
        )
        unread = sorted(
            f"{table}.{column}"
            for table, column in named
            if column not in source or table not in source
        )
        self.assertEqual(
            unread,
            [],
            "these columns are named after a provider link and nothing asks the operator "
            "about them before the deletion",
        )

    def test_everything_a_webhook_can_be_scoped_to_is_asked_about_when_it_goes(self) -> None:
        """The gate that generalises, because the naming rule above could not.

        A webhook points at things through one untyped column and a word beside it. The word
        is the whole list -- `_WEBHOOK_SCOPE_TYPES` -- and every entry in it names a table
        whose rows can be deleted. A scope added to that set is a new way to leave a webhook
        aimed at an id that is gone, and it fails here until the delete path of the thing it
        names asks the question too.
        """
        from app.api.webhooks import _WEBHOOK_SCOPE_TYPES

        # `all` points at nothing, so nothing can be deleted out from under it.
        targets = sorted(_WEBHOOK_SCOPE_TYPES - {"all"})
        readers = {
            "provider": inspect.getsource(providers_api._provider_webhook_dependents),
            "service": inspect.getsource(services_api._webhooks_scoped_to),
        }
        self.assertEqual(
            sorted(readers),
            targets,
            "a webhook scope whose target can be deleted with nothing asking about it",
        )
        for scope, src in readers.items():
            self.assertIn(f"scope_type = '{scope}'", src, f"{scope}: reads the wrong scope")

    def test_a_provider_nothing_names_is_still_deleted_without_a_question(self) -> None:
        """The new branch must not turn every deletion into a dialog."""
        self._seed_template(proxy_provider_id=None, dns_provider_id=None)

        result = providers_api.delete_provider(2, _request("DELETE"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["unlinked_templates"], [])


# ─────────────────────────────────────────────────────────────────────────────────────
# An id that points at nothing must be caught before a provider is touched
# ─────────────────────────────────────────────────────────────────────────────────────
class WriteEndpointsValidateReferencesFirstTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        self.calls: list = []
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: _SilentProvider(self.calls)),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patchers)])
        self._seed_providers()

    def _body(self, **overrides) -> services_api.ServiceIn:
        payload = {
            "subdomain": "new",
            "domain": "example.com",
            "target_ip": "10.0.0.10",
            "target_port": 8080,
            "proxy_provider_id": 2,
            "dns_ip": "198.51.100.7",
        }
        payload.update(overrides)
        return services_api.ServiceIn(**payload)

    def test_an_unknown_tag_is_a_400_and_no_host_was_created(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._body(tag_ids=[77]))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("tag 77", caught.exception.detail)
        self.assertEqual(self.calls, [], "the proxy must not have been called at all")
        conn = models.get_db()
        self.assertIsNone(conn.execute("SELECT 1 FROM services").fetchone())
        conn.close()

    def test_an_unknown_environment_is_named(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._body(environment_ids=[5]))
        self.assertIn("environment 5", caught.exception.detail)
        self.assertEqual(self.calls, [])

    def test_an_unknown_primary_provider_is_named(self) -> None:
        """This one used to reach `INSERT` and die there, on a foreign key."""
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._body(proxy_provider_id=404))
        self.assertIn("provider 404", caught.exception.detail)
        self.assertEqual(self.calls, [])

    def test_an_unknown_extra_push_target_is_named(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._body(extra_dns_provider_ids=[91]))
        self.assertIn("provider 91", caught.exception.detail)
        self.assertEqual(self.calls, [])

    def test_every_unknown_id_is_listed_at_once(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._body(tag_ids=[7], environment_ids=[8]))
        self.assertIn("tag 7", caught.exception.detail)
        self.assertIn("environment 8", caught.exception.detail)

    def test_known_ids_go_through(self) -> None:
        conn = models.get_db()
        conn.execute("INSERT INTO tags (id, name, color) VALUES (1, 'prod', '#fff')")
        conn.commit()
        conn.close()

        result = services_api.add_service(_request(), self._body(tag_ids=[1]))

        self.assertEqual(result.status_code, 201)
        self.assertIn(("create_host", "new.example.com"), self.calls)

    def test_update_refuses_before_moving_the_hostname(self) -> None:
        sid = self._seed_service()
        self.calls.clear()

        with self.assertRaises(HTTPException) as caught:
            services_api.update_service(
                sid, _request("PUT"), self._body(subdomain="moved", tag_ids=[77])
            )

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.calls, [], "nothing may be reconfigured on a refused payload")
        conn = models.get_db()
        row = conn.execute("SELECT subdomain FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        self.assertEqual(row["subdomain"], "app")


# ─────────────────────────────────────────────────────────────────────────────────────
# A webhook armed at an id nothing answers to
# ─────────────────────────────────────────────────────────────────────────────────────
class WebhookScopeNamesSomethingThatExistsTests(_IsolatedDB):
    """`scope_ref_id` carries no foreign key, so nothing but the route itself said no.

    The two delete paths above warn when a deletion leaves a webhook aimed at nothing: the
    row outlives its target, `_service_matches_scope` answers False from then on, and
    Settings goes on showing it as enabled. Reaching that same state through the write
    endpoint took one wrong number, and produced no warning anywhere.
    """

    def setUp(self) -> None:
        super().setUp()
        self._patch = patch.object(webhooks_api, "require_auth", lambda _req, scope=None: None)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self._seed_providers()

    def _create(self, **overrides) -> dict:
        body = {"name": "on-call", "url": "json://hook.test/x"}
        body.update(overrides)
        return webhooks_api.add_webhook(_request(), webhooks_api.WebhookIn(**body))

    def _stored(self, wid: int) -> dict:
        conn = models.get_db()
        row = conn.execute(
            "SELECT scope_type, scope_ref_id, enabled FROM webhooks WHERE id=?", (wid,)
        ).fetchone()
        conn.close()
        return dict(row)

    def test_a_service_scope_naming_no_service_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self._create(scope_type="service", scope_ref_id=99999)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("service 99999", caught.exception.detail)
        conn = models.get_db()
        self.assertIsNone(conn.execute("SELECT 1 FROM webhooks").fetchone())
        conn.close()

    def test_a_provider_scope_naming_no_provider_is_refused(self) -> None:
        """One id space, two meanings: a service id under `provider` names nothing at all."""
        sid = self._seed_service()

        with self.assertRaises(HTTPException) as caught:
            self._create(scope_type="provider", scope_ref_id=sid)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn(f"provider {sid}", caught.exception.detail)

    def test_an_update_onto_a_target_that_is_not_there_keeps_the_old_one(self) -> None:
        created = self._create(scope_type="provider", scope_ref_id=2)

        with self.assertRaises(HTTPException) as caught:
            webhooks_api.update_webhook(
                created["id"], _request("PUT"), webhooks_api.WebhookUpdateIn(scope_ref_id=404)
            )

        self.assertIn("provider 404", caught.exception.detail)
        self.assertEqual(self._stored(created["id"])["scope_ref_id"], 2)

    def test_changing_the_word_alone_does_not_carry_the_id_across(self) -> None:
        """Service 1 and provider 1 are unrelated rows, and the scope used to move between."""
        sid = self._seed_service()
        created = self._create(scope_type="service", scope_ref_id=sid)

        with self.assertRaises(HTTPException) as caught:
            webhooks_api.update_webhook(
                created["id"],
                _request("PUT"),
                webhooks_api.WebhookUpdateIn(scope_type="provider"),
            )

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(
            self._stored(created["id"]),
            {"scope_type": "service", "scope_ref_id": sid, "enabled": 1},
        )

    def test_changing_the_word_and_naming_the_new_target_goes_through(self) -> None:
        sid = self._seed_service()
        created = self._create(scope_type="service", scope_ref_id=sid)

        webhooks_api.update_webhook(
            created["id"],
            _request("PUT"),
            webhooks_api.WebhookUpdateIn(scope_type="provider", scope_ref_id=3),
        )

        self.assertEqual(
            self._stored(created["id"]),
            {"scope_type": "provider", "scope_ref_id": 3, "enabled": 1},
        )

    def test_a_target_that_is_there_is_armed(self) -> None:
        created = self._create(scope_type="provider", scope_ref_id=2)

        self.assertEqual(
            self._stored(created["id"]),
            {"scope_type": "provider", "scope_ref_id": 2, "enabled": 1},
        )

    def test_a_scope_of_everything_names_nothing_and_is_asked_for_nothing(self) -> None:
        created = self._create(scope_type="all")

        self.assertEqual(
            self._stored(created["id"]),
            {"scope_type": "all", "scope_ref_id": None, "enabled": 1},
        )

    def test_a_partial_update_keeps_the_scope_it_had(self) -> None:
        """The enable/disable toggle sends only `enabled`; it must not resend a scope to keep
        the one already stored, and the check must not fire on a scope nobody touched."""
        created = self._create(scope_type="provider", scope_ref_id=2)

        webhooks_api.update_webhook(
            created["id"], _request("PUT"), webhooks_api.WebhookUpdateIn(enabled=0)
        )

        self.assertEqual(
            self._stored(created["id"]),
            {"scope_type": "provider", "scope_ref_id": 2, "enabled": 0},
        )

    def test_every_scope_word_names_a_table_to_check_it_against(self) -> None:
        """The gate that generalises, beside the one the delete paths already have.

        A scope word added to `_WEBHOOK_SCOPE_TYPES` with no table behind it reaches
        `_SCOPE_TARGET_TABLE[scope_type]` and raises `KeyError`, which is a 500 on a wrong
        id instead of the 400 this check exists to give.
        """
        from app.api.webhooks import _SCOPE_TARGET_TABLE, _WEBHOOK_SCOPE_TYPES

        self.assertEqual(
            sorted(_SCOPE_TARGET_TABLE),
            sorted(_WEBHOOK_SCOPE_TYPES - {"all"}),
            "a scope word with no table behind it turns a wrong id into a 500",
        )
        conn = models.get_db()
        for table in _SCOPE_TARGET_TABLE.values():
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 -- the table names above
        conn.close()

# ─────────────────────────────────────────────────────────────────────────────────────
# One hostname, one service
# ─────────────────────────────────────────────────────────────────────────────────────
class OneHostnameOneServiceTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        self.calls: list = []
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: _SilentProvider(self.calls)),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patchers)])
        self._seed_providers()

    def test_a_second_service_on_the_same_hostname_is_refused(self) -> None:
        sid = self._seed_service()

        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._service_body())

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn(f"#{sid}", caught.exception.detail)
        self.assertEqual(self.calls, [])

    def test_a_tunnel_hostname_colliding_with_a_proxy_service_is_refused(self) -> None:
        """The unique index cannot see this one: the columns differ, the published host does not."""
        self._seed_service(subdomain="app", domain="example.com")

        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(
                _request(),
                self._service_body(
                    subdomain="other",
                    domain="example.org",
                    expose_mode="tunnel",
                    tunnel_provider_id=2,
                    proxy_provider_id=None,
                    tunnel_hostname="app.example.com",
                ),
            )

        self.assertEqual(caught.exception.status_code, 409)

    def test_a_different_hostname_is_accepted(self) -> None:
        self._seed_service()

        result = services_api.add_service(_request(), self._service_body(subdomain="other"))

        self.assertEqual(result.status_code, 201)

    def test_updating_a_service_does_not_collide_with_itself(self) -> None:
        sid = self._seed_service()

        body = self._service_body(target_port=9090)
        result = services_api.update_service(sid, _request("PUT"), body)

        self.assertEqual(result["id"], sid)
        self.assertEqual(result["target_port"], 9090)

    def test_updating_onto_a_taken_hostname_is_refused(self) -> None:
        self._seed_service(subdomain="app")
        second = self._seed_service(subdomain="other")

        with self.assertRaises(HTTPException) as caught:
            body = self._service_body(subdomain="app")
            services_api.update_service(second, _request("PUT"), body)

        self.assertEqual(caught.exception.status_code, 409)

    def test_the_database_carries_the_constraint_too(self) -> None:
        """An API bypass -- a restore, a manual edit -- still cannot write the duplicate."""
        import sqlite3

        self._seed_service(subdomain="app")
        conn = models.get_db()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO services (subdomain, domain, target_ip, target_port)
                   VALUES ('app', 'example.com', '10.0.0.1', 80)"""
            )
        conn.close()

    def test_an_install_that_already_holds_duplicates_still_boots(self) -> None:
        """The index is a goal, not a gate: a wedged startup cannot be fixed from the UI."""
        conn = models.get_db()
        conn.execute("DROP INDEX IF EXISTS idx_services_hostname")
        conn.execute(
            """INSERT INTO services (subdomain, domain, target_ip, target_port)
               VALUES ('dup', 'example.com', '10.0.0.1', 80)"""
        )
        conn.execute(
            """INSERT INTO services (subdomain, domain, target_ip, target_port)
               VALUES ('dup', 'example.com', '10.0.0.2', 80)"""
        )
        conn.commit()

        models._ensure_unique_service_hostnames(conn)
        conn.commit()
        conn.close()

        self.assertTrue(
            any("Duplicate service hostnames" in m and "dup.example.com" in m for m in self._logs()),
            self._logs(),
        )


# ─────────────────────────────────────────────────────────────────────────────────────
# The hostname was free when it was read, and taken by the time it was written
# ─────────────────────────────────────────────────────────────────────────────────────
class _RacingConnection:
    """Lets a second writer in, once, at a chosen statement of the route's own connection.

    The window is not reachable by calling an endpoint twice: it opens between a route's two
    statements, the read that clears the name and the write that stores it, and every call
    the route makes to a proxy or a DNS server sits inside it. Firing on the read reproduces
    it exactly and in order; a thread racing a real one would reproduce it sometimes.
    """

    def __init__(self, inner, trigger: str, steal) -> None:
        self._inner, self._trigger, self._steal = inner, trigger, steal
        self._fired = False

    def execute(self, sql, *args, **kwargs):
        cursor = self._inner.execute(sql, *args, **kwargs)
        if not self._fired and self._trigger in sql:
            self._fired = True
            self._steal()
        return cursor

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _is_closed(conn) -> bool:
    """A refusal that returns without closing leaks the handle for the life of the process."""
    import sqlite3

    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


# Both service routes scan the table for a published hostname before they touch a provider.
# It is the last read either of them does, so it is where the other operator gets in.
_CONFLICT_SCAN = "expose_mode, tunnel_hostname FROM services"


class HostnameTakenMidWriteTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        self.calls: list = []
        self.opened: list = []
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: _SilentProvider(self.calls)),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patchers)])
        self._seed_providers()

    def tearDown(self) -> None:
        # Only reached when the fix is absent. An open handle keeps the database file, and
        # the temporary directory then refuses to go on Windows -- so a regression here would
        # report a teardown error stacked on top of the assertion that actually failed.
        for conn in self.opened:
            if not _is_closed(conn):
                conn.close()
        super().tearDown()

    def _race(self, steal, trigger: str = _CONFLICT_SCAN) -> None:
        real_get_db = models.get_db

        def racing_get_db():
            conn = real_get_db()
            self.opened.append(conn)
            return _RacingConnection(conn, trigger, steal)

        patcher = patch.object(services_api, "get_db", racing_get_db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _claims(self, subdomain: str) -> None:
        """The other operator saves the same hostname while we are talking to a provider."""
        self._race(lambda: self._seed_service(subdomain=subdomain))

    def _warnings(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT message FROM logs WHERE level='warning' ORDER BY id").fetchall()
        conn.close()
        return [r["message"] for r in rows]

    # -- creating --------------------------------------------------------------------------
    def test_a_creation_that_loses_the_race_is_refused_and_not_a_server_error(self) -> None:
        self._claims("vault")

        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._service_body(subdomain="vault"))

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("vault.example.com", caught.exception.detail)

    def test_the_refused_creation_names_what_it_had_already_published(self) -> None:
        """The one thing the operator cannot look up afterwards: there is no row to look at."""
        self._claims("vault")

        with self.assertRaises(HTTPException):
            services_api.add_service(_request(), self._service_body(subdomain="vault"))

        self.assertIn(("create_host", "vault.example.com"), self.calls)
        published = [m for m in self._warnings() if "vault.example.com was published" in m]
        self.assertEqual(len(published), 1, self._warnings())
        self.assertIn("NPM", published[0])
        self.assertIn("check those providers", published[0])
        # The push's own line outlives the rolled-back INSERT because `add_log` opens its own
        # connection when it is not handed one. The warning has to be written the same way:
        # through the route's connection it would be discarded along with the INSERT, which is
        # the single outcome this whole branch exists to prevent.
        self.assertTrue(any("Proxy created: vault.example.com" in m for m in self._logs()))

    def test_the_refused_creation_closes_its_connection(self) -> None:
        self._claims("vault")

        with self.assertRaises(HTTPException):
            services_api.add_service(_request(), self._service_body(subdomain="vault"))

        self.assertTrue(self.opened, "the route never opened one, so nothing was measured")
        self.assertTrue(all(_is_closed(c) for c in self.opened))

    def test_an_uncontested_creation_is_still_created_and_says_nothing(self) -> None:
        self._race(lambda: None)

        result = services_api.add_service(_request(), self._service_body(subdomain="vault"))

        self.assertEqual(result.status_code, 201)
        self.assertEqual([m for m in self._warnings() if "was published" in m], [])

    # -- renaming --------------------------------------------------------------------------
    def test_a_rename_that_loses_the_race_is_refused_and_not_a_server_error(self) -> None:
        sid = self._seed_service(subdomain="old", dns_provider_id=None, dns_ip="")
        self._claims("vault")

        with self.assertRaises(HTTPException) as caught:
            services_api.update_service(sid, _request("PUT"), self._service_body(subdomain="vault"))

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("vault.example.com", caught.exception.detail)

    def test_the_refused_rename_says_what_the_providers_now_serve(self) -> None:
        sid = self._seed_service(subdomain="old", dns_provider_id=None, dns_ip="")
        self._claims("vault")

        with self.assertRaises(HTTPException):
            services_api.update_service(sid, _request("PUT"), self._service_body(subdomain="vault"))

        drifted = [m for m in self._warnings() if f"Service #{sid} was reconfigured" in m]
        self.assertEqual(len(drifted), 1, self._warnings())
        self.assertIn("vault.example.com", drifted[0])
        self.assertIn("old.example.com", drifted[0])
        self.assertIn("drift check", drifted[0])

    def test_the_refused_rename_leaves_the_row_on_its_old_hostname(self) -> None:
        """Deliberately not withdrawn: the winner holds that name on these same providers."""
        sid = self._seed_service(subdomain="old", dns_provider_id=None, dns_ip="")
        self._claims("vault")

        with self.assertRaises(HTTPException):
            services_api.update_service(sid, _request("PUT"), self._service_body(subdomain="vault"))

        conn = models.get_db()
        row = conn.execute("SELECT subdomain, domain FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        self.assertEqual((row["subdomain"], row["domain"]), ("old", "example.com"))

    def test_the_refused_rename_closes_its_connection(self) -> None:
        sid = self._seed_service(subdomain="old", dns_provider_id=None, dns_ip="")
        self._claims("vault")

        with self.assertRaises(HTTPException):
            services_api.update_service(sid, _request("PUT"), self._service_body(subdomain="vault"))

        self.assertTrue(self.opened, "the route never opened one, so nothing was measured")
        self.assertTrue(all(_is_closed(c) for c in self.opened))


# ─────────────────────────────────────────────────────────────────────────────────────
# A rename answers for the collision its own creation already answers for
# ─────────────────────────────────────────────────────────────────────────────────────
class RenamesRefuseTheWayCreationsDoTests(_IsolatedDB):
    """Two lists, edited in the same panel, where only the create half had the handler.

    Nothing is published for a tag or an environment, so the cost is smaller than the service
    routes' -- but the reply was a 500 for a name collision the operator caused and could fix,
    three lines of code away from the 409 the creation answers for exactly the same clash.
    """

    def setUp(self) -> None:
        super().setUp()
        for module in (tags_api, environments_api):
            patcher = patch.object(module, "require_auth", lambda _req, scope=None: None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _race(self, module, trigger: str, steal) -> None:
        real_get_db = models.get_db

        def racing_get_db():
            return _RacingConnection(real_get_db(), trigger, steal)

        patcher = patch.object(module, "get_db", racing_get_db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _insert(self, table: str, name: str):
        def steal():
            conn = models.get_db()
            statement = f"INSERT INTO {table} (name, color) VALUES (?, 'blue')"  # noqa: S608
            conn.execute(statement, (name,))
            conn.commit()
            conn.close()

        return steal

    def test_a_tag_rename_that_loses_the_race_is_refused(self) -> None:
        conn = models.get_db()
        tid = conn.execute("INSERT INTO tags (name, color) VALUES ('draft', 'blue')").lastrowid
        conn.commit()
        conn.close()
        self._race(tags_api, "FROM tags WHERE name=?", self._insert("tags", "prod"))

        with self.assertRaises(HTTPException) as caught:
            tags_api.update_tag(tid, _request("PUT"), tags_api.TagIn(name="prod"))

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, "A tag with this name already exists")

    def test_an_uncontested_tag_rename_still_goes_through(self) -> None:
        conn = models.get_db()
        tid = conn.execute("INSERT INTO tags (name, color) VALUES ('draft', 'blue')").lastrowid
        conn.commit()
        conn.close()

        result = tags_api.update_tag(tid, _request("PUT"), tags_api.TagIn(name="prod"))

        self.assertEqual(result, {"ok": True})

    def test_an_environment_rename_that_loses_the_race_is_refused(self) -> None:
        conn = models.get_db()
        seed = "INSERT INTO environments (name, color) VALUES ('draft', 'blue')"
        eid = conn.execute(seed).lastrowid
        conn.commit()
        conn.close()
        self._race(
            environments_api, "FROM environments WHERE name=?", self._insert("environments", "prod")
        )

        with self.assertRaises(HTTPException) as caught:
            environments_api.update_environment(
                eid, _request("PUT"), environments_api.EnvironmentIn(name="prod")
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, "An environment with this name already exists")

    def test_an_uncontested_environment_rename_still_goes_through(self) -> None:
        conn = models.get_db()
        seed = "INSERT INTO environments (name, color) VALUES ('draft', 'blue')"
        eid = conn.execute(seed).lastrowid
        conn.commit()
        conn.close()

        result = environments_api.update_environment(
            eid, _request("PUT"), environments_api.EnvironmentIn(name="prod")
        )

        self.assertEqual(result["name"], "prod")


# ─────────────────────────────────────────────────────────────────────────────────────
# The same question, asked of every UNIQUE column in the schema
# ─────────────────────────────────────────────────────────────────────────────────────
_INSERT_SQL = re.compile(r"INSERT\s+(?:OR\s+(\w+)\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", re.I | re.S)
_UPDATE_SQL = re.compile(r"UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\bWHERE\b|$)", re.I | re.S)
_HANDLED = ("Exception", "BaseException", "sqlite3.IntegrityError", "IntegrityError")


def _unique_columns() -> dict[str, set[str]]:
    """Every column the schema refuses a duplicate on, read out of `app/models.py` itself."""
    source = inspect.getsource(models)
    unique: dict[str, set[str]] = {}
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\s*\)", source, re.S)
    for table, body in tables:
        for line in body.splitlines():
            column = re.match(r"\s*(\w+)\s+\w+.*\bUNIQUE\b", line)
            if column:
                unique.setdefault(table, set()).add(column.group(1))
            combined = re.match(r"\s*UNIQUE\s*\(([^)]*)\)", line)
            if combined:
                names = combined.group(1).split(",")
                unique.setdefault(table, set()).update(c.strip() for c in names)
    # `idx_services_hostname` is built apart from its table, because an install that already
    # holds duplicates has to keep booting. It constrains the same way once it exists.
    for table, cols in re.findall(r"CREATE UNIQUE INDEX[^\"']*?ON (\w+)\s*\(([^)]*)\)", source):
        unique.setdefault(table, set()).update(c.strip() for c in cols.split(","))
    return unique


def _catches_integrity(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    named = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(ast.unparse(name) in _HANDLED for name in named)


def _unguarded_unique_writes(sources: dict[str, str]) -> list[str]:
    """Writes to a UNIQUE column where nothing but the index would notice the duplicate.

    Reads the SQL where it is written, so a statement hoisted into a module constant and
    passed in by name is invisible here. No route writes one today -- every INSERT and
    UPDATE in `app/api` is spelled at the call -- and this says so rather than implying a
    reach it does not have.
    """
    unique = _unique_columns()
    found = []
    for filename, text in sources.items():
        for function in ast.walk(ast.parse(text)):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            guarded = [
                (min(s.lineno for s in node.body), max(s.end_lineno or s.lineno for s in node.body))
                for node in ast.walk(function)
                if isinstance(node, ast.Try) and any(_catches_integrity(h) for h in node.handlers)
            ]
            for node in ast.walk(function):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                sql = node.value
                written = set()
                for conflict, table, columns in _INSERT_SQL.findall(sql):
                    # `INSERT OR REPLACE` / `OR IGNORE` settles the clash in the statement.
                    if not conflict:
                        named = {c.strip() for c in columns.split(",")}
                        written |= named & unique.get(table, set())
                for table, assignments in _UPDATE_SQL.findall(sql):
                    assigned = {m.group(1) for m in re.finditer(r"(\w+)\s*=", assignments)}
                    written |= assigned & unique.get(table, set())
                if not written or re.search(r"ON\s+CONFLICT\b", sql, re.I):
                    continue
                if any(low <= node.lineno <= high for low, high in guarded):
                    continue
                found.append(f"{filename}:{node.lineno} {function.name}() -> {sorted(written)}")
    return sorted(found)


def _api_sources() -> dict[str, str]:
    directory = os.path.dirname(inspect.getfile(services_api))
    sources = {}
    for filename in sorted(os.listdir(directory)):
        if filename.endswith(".py"):
            with open(os.path.join(directory, filename), encoding="utf-8") as handle:
                sources[filename] = handle.read()
    return sources


class EveryUniqueWriteAnswersForItselfTests(unittest.TestCase):
    """The four routes above were found by asking this of the whole schema rather than one route.

    Kept so the next write to a UNIQUE column has to answer it too. A write is accounted for
    when the statement settles the clash itself -- `INSERT OR IGNORE`, `INSERT OR REPLACE`, an
    `ON CONFLICT ... DO UPDATE` upsert -- or when it sits inside a handler that would take an
    `IntegrityError`, including the per-row `except Exception` an import uses to refuse one
    line and carry on with the rest. Anything else reaches the operator as a 500.
    """

    def test_no_write_to_a_unique_column_is_left_to_the_index_alone(self) -> None:
        self.assertEqual(_unguarded_unique_writes(_api_sources()), [])

    def test_the_scan_reports_an_unguarded_write_and_spares_a_guarded_one(self) -> None:
        """A guard that finds nothing is worth exactly as much as no guard."""
        fixture = {
            "sample.py": (
                "def unguarded(conn, name):\n"
                "    conn.execute('INSERT INTO tags (name) VALUES (?)', (name,))\n"
                "\n"
                "def guarded(conn, name):\n"
                "    try:\n"
                "        conn.execute('INSERT INTO tags (name) VALUES (?)', (name,))\n"
                "    except sqlite3.IntegrityError:\n"
                "        raise HTTPException(409, 'taken')\n"
                "\n"
                "def settled(conn, name):\n"
                "    conn.execute('INSERT OR IGNORE INTO tags (name) VALUES (?)', (name,))\n"
            )
        }

        reported = _unguarded_unique_writes(fixture)

        self.assertEqual(len(reported), 1, reported)
        self.assertIn("unguarded()", reported[0])


if __name__ == "__main__":
    unittest.main()
