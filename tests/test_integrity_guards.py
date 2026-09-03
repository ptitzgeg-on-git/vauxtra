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
