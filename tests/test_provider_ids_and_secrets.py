"""Three things the code claimed and did not do.

- `DELETE /api/providers/{pid}/proxy-hosts/{host_id}` coerced the identifier to `int`
  before handing it to the provider. Only NPM numbers its hosts; Cloudflare Tunnel
  addresses ingress rules by hostname and Traefik by router name, so the route answered
  `500 invalid literal for int()` and deleting a tunnel route through the API was
  impossible.
- `decrypt_secret` returned the value unchanged on any failure. For a password stored in
  clear before encryption existed that is right; for a Fernet token whose `SECRET_KEY` is
  gone it sent the ciphertext to the provider as the password.
- `update_service` held SQLite's single writer lock across up to three provider HTTP
  calls, so a provider that hung locked out every other writer for the duration.
"""

import hashlib
import logging
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.auth as auth
import app.config as config
import app.db as app_db
import app.scheduler as scheduler
from app import models
from app.providers.npm import NPMProvider

PASSWORD = "test-password-long-enough"


def _response(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    return r


class _ApiHarness(unittest.TestCase):
    """A temp database and an authenticated client. Duplicated per file: `tests/` is
    not a package."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "ids.test.db")
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
            conn.execute(
                "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                ("adm", hashlib.sha256(b"key-adm").hexdigest(), "adm", "admin"),
            )
            conn.commit()
        finally:
            conn.close()
        self.headers = {"Authorization": "Bearer key-adm"}

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _provider(self, ptype: str, name: str = "P") -> int:
        conn = models.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO providers (name, type, url, username, password) VALUES (?,?,?,?,?)",
                (name, ptype, "http://provider.lan", "u", ""),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()


class _Recorder:
    """A provider that records the identifier it was handed, and nothing else."""

    def __init__(self, ok: bool = True) -> None:
        self.seen: list = []
        self._ok = ok

    def delete_host(self, host_id):
        self.seen.append(host_id)
        return self._ok


class TheProviderReadsItsOwnIdentifierTests(_ApiHarness):
    """`int(host_id)` at the route was a claim that every provider numbers its routes."""

    def _delete(self, pid: int, host_id: str):
        return self.client.delete(
            f"/api/providers/{pid}/proxy-hosts/{host_id}", headers=self.headers
        )

    def test_a_tunnel_route_can_be_deleted_by_the_hostname_that_names_it(self) -> None:
        pid = self._provider("cloudflare_tunnel", "CF Tunnel")
        rec = _Recorder()
        with patch("app.api.providers.create_provider", return_value=rec):
            resp = self._delete(pid, "app.example.com")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(rec.seen, ["app.example.com"])

    def test_the_failure_it_used_to_give_is_gone(self) -> None:
        """It was a 500 whose text was `invalid literal for int() with base 10`."""
        pid = self._provider("cloudflare_tunnel", "CF Tunnel")
        with patch("app.api.providers.create_provider", return_value=_Recorder()):
            resp = self._delete(pid, "app.example.com")
        self.assertNotIn("invalid literal", resp.text)

    def test_an_npm_route_still_reaches_the_provider(self) -> None:
        pid = self._provider("npm", "NPM")
        rec = _Recorder()
        with patch("app.api.providers.create_provider", return_value=rec):
            resp = self._delete(pid, "42")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(rec.seen, ["42"])

    def test_a_provider_that_refuses_is_still_reported_as_a_refusal(self) -> None:
        pid = self._provider("cloudflare_tunnel", "CF Tunnel")
        with patch("app.api.providers.create_provider", return_value=_Recorder(ok=False)):
            resp = self._delete(pid, "app.example.com")
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("provider rejected", resp.json()["detail"])

    def test_a_provider_with_no_proxy_side_is_refused_before_any_of_this(self) -> None:
        pid = self._provider("adguard", "AdGuard")
        resp = self._delete(pid, "42")
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("does not support proxy", resp.json()["detail"])


class TheOneProviderThatNeedsANumberChecksForOneTests(unittest.TestCase):
    """NPM interpolates the id into a URL, so it is the one that has to be sure."""

    def setUp(self) -> None:
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm.session.get = MagicMock(return_value=_response(200, []))
        self.npm.session.post = MagicMock(return_value=_response(200))
        self.npm.session.put = MagicMock(return_value=_response(200))
        self.npm.session.delete = MagicMock(return_value=_response(204))

    def test_a_numeric_string_from_a_url_path_is_accepted(self) -> None:
        self.assertTrue(self.npm.delete_host("42"))
        self.assertIn("/nginx/proxy-hosts/42", self.npm.session.delete.call_args[0][0])

    def test_an_integer_still_works(self) -> None:
        self.assertTrue(self.npm.delete_host(42))
        self.assertIn("/nginx/proxy-hosts/42", self.npm.session.delete.call_args[0][0])

    def test_a_hostname_is_refused_without_a_request_going_out(self) -> None:
        self.assertFalse(self.npm.delete_host("app.example.com"))
        self.npm.session.delete.assert_not_called()

    def test_a_path_traversal_never_reaches_the_url(self) -> None:
        """The value is interpolated into `{api}/nginx/proxy-hosts/{host_id}`."""
        for hostile in ("1/../../users", "1/enable", "../tokens", ""):
            with self.subTest(host_id=hostile):
                self.npm.session.delete.reset_mock()
                self.assertFalse(self.npm.delete_host(hostile))
                self.npm.session.delete.assert_not_called()

    def test_toggle_host_is_held_to_the_same_rule(self) -> None:
        self.assertTrue(self.npm.toggle_host("7", False))
        self.assertIn("/nginx/proxy-hosts/7/disable", self.npm.session.post.call_args[0][0])
        self.npm.session.post.reset_mock()
        self.assertFalse(self.npm.toggle_host("router@docker", True))
        self.npm.session.post.assert_not_called()

    def test_update_host_is_held_to_the_same_rule(self) -> None:
        ok = self.npm.update_host("9", "a.example.com", "10.0.0.5", 80)
        self.assertTrue(ok)
        self.assertIn("/nginx/proxy-hosts/9", self.npm.session.put.call_args[0][0])
        self.npm.session.put.reset_mock()
        self.assertFalse(self.npm.update_host(None, "a.example.com", "10.0.0.5", 80))
        self.npm.session.put.assert_not_called()

    def test_a_network_error_is_still_a_false_and_not_an_exception(self) -> None:
        self.npm.session.delete = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertFalse(self.npm.delete_host(42))


class ACiphertextThatWillNotOpenIsNotAPasswordTests(unittest.TestCase):
    """Both failures used to leave `decrypt_secret` as the value itself."""

    def test_a_secret_written_by_this_instance_comes_back(self) -> None:
        self.assertEqual(config.decrypt_secret(config.encrypt_secret("hunter2")), "hunter2")

    def test_nothing_stays_nothing(self) -> None:
        self.assertEqual(config.decrypt_secret(""), "")

    def test_a_password_stored_before_encryption_existed_is_left_alone(self) -> None:
        """The legacy case this fallback was written for, and the only one it still serves."""
        for legacy in ("hunter2", "not-a-token", "AAAA"):
            with self.subTest(value=legacy):
                self.assertEqual(config.decrypt_secret(legacy), legacy)

    def test_a_token_from_another_key_yields_nothing_rather_than_itself(self) -> None:
        stranger = Fernet(Fernet.generate_key()).encrypt(b"hunter2").decode()
        self.assertTrue(stranger.startswith("gAAAAA"))
        with self.assertLogs("app.config", level="ERROR"):
            self.assertEqual(config.decrypt_secret(stranger), "")

    def test_the_log_line_names_the_cause_and_the_way_out(self) -> None:
        stranger = Fernet(Fernet.generate_key()).encrypt(b"hunter2").decode()
        with self.assertLogs("app.config", level="ERROR") as logs:
            config.decrypt_secret(stranger)
        joined = "\n".join(logs.output)
        self.assertIn("SECRET_KEY", joined)
        self.assertIn("re-enter", joined)

    def test_the_ciphertext_itself_never_comes_back(self) -> None:
        """What used to go out to the provider, in the field where a password goes."""
        stranger = Fernet(Fernet.generate_key()).encrypt(b"hunter2").decode()
        with self.assertLogs("app.config", level="ERROR"):
            self.assertNotEqual(config.decrypt_secret(stranger), stranger)

    def test_a_truncated_token_is_treated_as_a_key_mismatch_too(self) -> None:
        broken = config.encrypt_secret("hunter2")[:-6]
        with self.assertLogs("app.config", level="ERROR"):
            self.assertEqual(config.decrypt_secret(broken), "")


class _SecondWriter:
    """A provider whose calls write to the database from a second connection.

    This is what a slow provider does to a route that is holding SQLite's writer lock: it
    does not merely wait, it makes every other writer wait too. Rather than sleep for the
    fifteen seconds of `busy_timeout` to prove it, the fake writes -- which either succeeds
    because the lock was released, or raises `database is locked`.
    """

    def __init__(self) -> None:
        self.locked_out: list[str] = []
        self.calls: list[str] = []

    def _write(self, label: str) -> None:
        self.calls.append(label)
        conn = models.get_db()
        try:
            conn.execute("PRAGMA busy_timeout=800")
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (f"probe_{label}", "1"),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 - the point of the test is which one
            self.locked_out.append(f"{label}: {e}")
        finally:
            conn.close()

    def find_best_certificate(self, domain_suffix):
        self._write("find_best_certificate")
        return None

    def update_host(self, host_id, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._write("update_host")
        return True

    def toggle_host(self, host_id, enabled):
        self._write("toggle_host")
        return True

    def delete_host(self, host_id):
        self._write("delete_host")
        return True


class TheWriteLockIsNotHeldAcrossProviderCallsTests(_ApiHarness):
    """WAL leaves readers alone; a writer that waits out `busy_timeout` gets an error."""

    def setUp(self) -> None:
        super().setUp()
        self.pid = self._provider("npm", "NPM")
        conn = models.get_db()
        try:
            conn.execute(
                """INSERT INTO services
                       (id, subdomain, domain, target_ip, target_port, forward_scheme,
                        enabled, proxy_provider_id, expose_mode, npm_host_id)
                   VALUES (1, 'app', 'example.com', '10.0.0.5', 8080, 'http', 1, ?, 'proxy_dns', 42)""",
                (self.pid,),
            )
            conn.commit()
        finally:
            conn.close()

    def _body(self, enabled: bool) -> dict:
        return {
            "subdomain": "app",
            "domain": "example.com",
            "target_ip": "10.0.0.5",
            "target_port": 8080,
            "forward_scheme": "http",
            "enabled": enabled,
            "proxy_provider_id": self.pid,
            "expose_mode": "proxy_dns",
            "public_target_mode": "manual",
        }

    def test_a_provider_call_can_still_write_to_the_database(self) -> None:
        fake = _SecondWriter()
        with patch("app.api.services.create_provider", return_value=fake):
            resp = self.client.put("/api/services/1", json=self._body(False), headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("toggle_host", fake.calls)
        self.assertEqual(fake.locked_out, [], "the writer lock was still held")

    def test_the_bulk_route_releases_it_too(self) -> None:
        """It costs more here: three provider calls per service, not three in total."""
        conn = models.get_db()
        try:
            conn.execute(
                """INSERT INTO services
                       (id, subdomain, domain, target_ip, target_port, forward_scheme,
                        enabled, proxy_provider_id, expose_mode, npm_host_id)
                   VALUES (2, 'two', 'example.com', '10.0.0.6', 8080, 'http', 1, ?, 'proxy_dns', 43)""",
                (self.pid,),
            )
            conn.commit()
        finally:
            conn.close()

        fake = _SecondWriter()
        with patch("app.api.services.create_provider", return_value=fake):
            resp = self.client.post(
                "/api/services/bulk",
                json={"ids": [1, 2], "action": "disable"},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("toggle_host", fake.calls)
        self.assertEqual(fake.locked_out, [], "the writer lock was still held")

    def test_the_service_row_is_still_written(self) -> None:
        """Splitting the transaction must not lose the update it was carrying."""
        fake = _SecondWriter()
        with patch("app.api.services.create_provider", return_value=fake):
            self.client.put("/api/services/1", json=self._body(False), headers=self.headers)
        conn = models.get_db()
        try:
            row = conn.execute("SELECT enabled, target_port FROM services WHERE id=1").fetchone()
        finally:
            conn.close()
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["target_port"], 8080)


class TheDockerSocketIsDescribedAsWhatItIsTests(unittest.TestCase):
    """`:ro` applies to the socket file. The Docker API behind it is unchanged."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, rel: str) -> str:
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_every_place_that_mounts_it_says_what_it_grants(self) -> None:
        for rel in ("docker-compose.yml", "README.md", "docs/DEPLOYMENT.md"):
            with self.subTest(document=rel):
                text = self._read(rel)
                self.assertIn("docker.sock", text)
                self.assertIn("root on the host", text)

    def test_the_way_to_spend_less_is_written_down(self) -> None:
        deployment = self._read("docs/DEPLOYMENT.md")
        self.assertIn("docker-socket-proxy", deployment)
        # The number moves with the code. An unreachable daemon -- and a dropped socket
        # mount is exactly that -- answers 502 from `app/api/docker.py::_docker_client`,
        # not the 503 it used to. Pinning the sentence rather than the bare digits keeps
        # this from passing on a "502" that happens to appear somewhere else in the file.
        self.assertIn("the Docker screens then answer 502", deployment)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main(verbosity=2)
