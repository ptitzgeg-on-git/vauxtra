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
"""

import inspect
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import providers as providers_api
from app.api import services as services_api


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

    def _body(self, **overrides) -> services_api.ServiceIn:
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

    def test_a_second_service_on_the_same_hostname_is_refused(self) -> None:
        sid = self._seed_service()

        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._body())

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn(f"#{sid}", caught.exception.detail)
        self.assertEqual(self.calls, [])

    def test_a_tunnel_hostname_colliding_with_a_proxy_service_is_refused(self) -> None:
        """The unique index cannot see this one: the columns differ, the published host does not."""
        self._seed_service(subdomain="app", domain="example.com")

        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(
                _request(),
                self._body(
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

        result = services_api.add_service(_request(), self._body(subdomain="other"))

        self.assertEqual(result.status_code, 201)

    def test_updating_a_service_does_not_collide_with_itself(self) -> None:
        sid = self._seed_service()

        result = services_api.update_service(sid, _request("PUT"), self._body(target_port=9090))

        self.assertEqual(result["id"], sid)
        self.assertEqual(result["target_port"], 9090)

    def test_updating_onto_a_taken_hostname_is_refused(self) -> None:
        self._seed_service(subdomain="app")
        second = self._seed_service(subdomain="other")

        with self.assertRaises(HTTPException) as caught:
            services_api.update_service(second, _request("PUT"), self._body(subdomain="app"))

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


if __name__ == "__main__":
    unittest.main()
