"""A stored provider secret only goes back to the host it was entered for.

`PUT /api/providers/{pid}` kept the stored password when only the URL changed, and the next
test or health check sent it to the new address. A `write` key could therefore point an
integration at a server it controls and read the token off the request.
"""

import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as app_db
import app.scheduler as scheduler
from app import models
from app.api.providers import _same_endpoint
from app.config import decrypt_secret, encrypt_secret

PASSWORD = "test-password-long-enough"
STORED_SECRET = "s3cret-token-value"
ORIGINAL_URL = "http://npm.home.example:81"


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "endpoint.test.db")
        models.init_db()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            patch.object(auth, "APP_PASSWORD", PASSWORD),
            patch.object(_limiter, "_check_request_limit", _no_rate_limit),
        ]
        for p in self._patchers:
            p.start()

        import app.main as app_main

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

        conn = models.get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                       prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                       created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
            )
            for name, scopes in (("rw", "write"), ("adm", "admin")):
                conn.execute(
                    "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                    (name, hashlib.sha256(f"key-{name}".encode()).hexdigest(), name, scopes),
                )
            cur = conn.execute(
                "INSERT INTO providers (name, type, url, username, password, extra) "
                "VALUES (?,?,?,?,?,?)",
                ("NPM", "npm", ORIGINAL_URL, "admin@example.com",
                 encrypt_secret(STORED_SECRET), "{}"),
            )
            self.pid = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO providers (name, type, url, username, password, extra) "
                "VALUES (?,?,?,?,?,?)",
                ("Traefik", "traefik", "http://traefik.home.example:8080", "", "", "{}"),
            )
            self.passwordless_pid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _put(self, key: str, body: dict, pid: int | None = None):
        return self.client.put(
            f"/api/providers/{pid or self.pid}",
            json=body,
            headers={"Authorization": f"Bearer key-{key}"},
        )

    def _row(self, pid: int | None = None):
        conn = models.get_db()
        try:
            return conn.execute(
                "SELECT url, password FROM providers WHERE id=?", (pid or self.pid,)
            ).fetchone()
        finally:
            conn.close()


class AWriteKeyCannotRedirectTheStoredSecret(_Fixture):
    def test_moving_the_url_without_the_secret_is_refused(self):
        resp = self._put("rw", {"url": "http://collector.attacker.example:81"})
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("password", resp.json()["detail"])
        self.assertEqual(self._row()["url"], ORIGINAL_URL)

    def test_a_port_or_scheme_change_is_a_move_too(self):
        for url in ("http://npm.home.example:8081", "https://npm.home.example:81"):
            with self.subTest(url=url):
                resp = self._put("rw", {"url": url})
                self.assertEqual(resp.status_code, 403, resp.text)
                self.assertEqual(self._row()["url"], ORIGINAL_URL)

    def test_moving_it_with_a_new_secret_is_allowed(self):
        resp = self._put("rw", {"url": "http://npm2.home.example:81", "password": "new-one"})
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self._row()
        self.assertEqual(row["url"], "http://npm2.home.example:81")
        self.assertEqual(decrypt_secret(row["password"]), "new-one")

    def test_the_same_host_with_another_path_is_not_a_move(self):
        resp = self._put("rw", {"url": "http://NPM.home.example:81/api"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(decrypt_secret(self._row()["password"]), STORED_SECRET)

    def test_other_fields_still_save_without_the_secret(self):
        resp = self._put("rw", {"name": "NPM renamed", "url": ORIGINAL_URL})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_a_provider_with_no_stored_secret_can_move(self):
        resp = self._put(
            "rw", {"url": "http://traefik2.home.example:8080"}, pid=self.passwordless_pid
        )
        self.assertEqual(resp.status_code, 200, resp.text)


class AnAdminCanStillMoveIt(_Fixture):
    def test_an_admin_key_moves_it_and_keeps_the_secret(self):
        resp = self._put("adm", {"url": "http://npm2.home.example:81"})
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self._row()
        self.assertEqual(row["url"], "http://npm2.home.example:81")
        self.assertEqual(decrypt_secret(row["password"]), STORED_SECRET)


class SameEndpoint(unittest.TestCase):
    def test_default_ports_and_case_are_the_same_endpoint(self):
        self.assertTrue(_same_endpoint("http://Host.example", "http://host.example:80/x"))
        self.assertTrue(_same_endpoint("https://host.example:443", "https://host.example"))

    def test_host_port_and_scheme_each_make_a_different_endpoint(self):
        self.assertFalse(_same_endpoint("http://a.example", "http://b.example"))
        self.assertFalse(_same_endpoint("http://a.example:81", "http://a.example:82"))
        self.assertFalse(_same_endpoint("http://a.example", "https://a.example"))

    def test_an_unparseable_port_is_never_the_same_endpoint(self):
        self.assertFalse(_same_endpoint("http://a.example:99999", "http://a.example:99999"))


if __name__ == "__main__":
    unittest.main()
