"""Regression tests for the security findings of the cross-checked review (S1 to S9).

Each class names the finding it covers. `tests/` is not a package, so the isolated-database
base class is duplicated here by design, like in the other files.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.auth as auth
import app.db as app_db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from app.api import auth as auth_api

PASSWORD = "correct horse battery"


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _IsolatedDB(unittest.TestCase):
    APP_PASSWORD = ""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "review.test.db")
        models.init_db()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._patchers = [
            patch.object(auth, "APP_PASSWORD", self.APP_PASSWORD),
            patch.object(_limiter, "_check_request_limit", _no_rate_limit),
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _setting(self, key: str) -> str:
        conn = models.get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()


class _WithKeys(_IsolatedDB):
    """A password-protected instance with one key per scope, over HTTP."""

    APP_PASSWORD = "test-password"

    def setUp(self) -> None:
        super().setUp()
        conn = models.get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                   prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                   created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
        )
        for name, scopes in (("ro", "read"), ("rw", "write"), ("adm", "admin")):
            conn.execute(
                "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                (name, hashlib.sha256(f"key-{name}".encode()).hexdigest(), name[:8], scopes),
            )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (1, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{"a": 1}', 1)"""
        )
        conn.commit()
        conn.close()
        self._client_cm = TestClient(app_main.app, base_url="http://localhost")
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        super().tearDown()

    def _h(self, key: str) -> dict:
        return {"Authorization": f"Bearer key-{key}"}

    def _provider(self) -> dict:
        conn = models.get_db()
        try:
            return dict(conn.execute("SELECT * FROM providers WHERE id=1").fetchone())
        finally:
            conn.close()


# ---------------------------------------------------------------------------- S2 / S9.1


class S2SetupPasswordCannotBeReplayedTests(_IsolatedDB):
    def _setup(self, password: str = PASSWORD):
        return auth_api.setup_password(
            _request(path="/api/auth/setup-password"),
            auth_api.SetPasswordBody(password=password),
        )

    def test_an_open_install_can_still_set_its_password(self):
        self._setup()
        self.assertEqual(self._setting("auth_mode"), "password")

    def test_a_vanished_hash_does_not_reopen_the_route(self):
        self._setup()
        self._exec("DELETE FROM settings WHERE key='app_password_hash'")
        with self.assertRaises(HTTPException) as ctx:
            self._setup("an attacker's choice")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self._setting("app_password_hash"), "")

    def test_an_unreadable_database_does_not_open_the_route(self):
        boom = sqlite3.OperationalError("database is locked")
        with patch.object(auth, "_read_auth_settings", side_effect=boom):
            with self.assertRaises(HTTPException) as ctx:
                self._setup()
        self.assertEqual(ctx.exception.status_code, 400)

    def test_the_write_rechecks_under_the_lock(self):
        """A concurrent call that won the race must not be overwritten."""
        self._exec("INSERT INTO settings (key, value) VALUES ('app_password_hash', 'pbkdf2:x$y$z')")
        with patch.object(auth_api, "has_password_configured", lambda: False), \
                patch.object(auth_api, "password_was_configured_once", lambda: False):
            with self.assertRaises(HTTPException):
                self._setup()
        self.assertEqual(self._setting("app_password_hash"), "pbkdf2:x$y$z")

    def test_setting_it_ends_the_sessions_of_the_open_period(self):
        self._setup()
        self.assertEqual(self._setting("session_epoch"), "1")


# -------------------------------------------------------------------------------- S1


class S1ProviderSecretCannotBeRedirectedTests(_WithKeys):
    def test_a_write_key_cannot_move_the_url_to_another_host(self):
        resp = self.client.put("/api/providers/1", json={"url": "http://evil.example:81"},
                               headers=self._h("rw"))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(self._provider()["url"], "http://npm:81")

    def test_a_write_key_may_edit_the_extra_keys_that_move_no_secret(self):
        # `proxied`, `tunnel_id` and the like decide nothing about where the secret goes.
        resp = self.client.put("/api/providers/1", json={"extra": {"a": 2}}, headers=self._h("rw"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(json.loads(self._provider()["extra"]), {"a": 2})

    def test_a_write_key_cannot_turn_tls_verification_off(self):
        resp = self.client.put("/api/providers/1", json={"extra": {"a": 1, "tls_verify": False}},
                               headers=self._h("rw"))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(json.loads(self._provider()["extra"]), {"a": 1})

    def test_a_write_key_cannot_change_the_ca_bundle(self):
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
            path = fh.name
        try:
            body = {"extra": {"a": 1, "ca_bundle": path}}
            resp = self.client.put("/api/providers/1", json=body, headers=self._h("rw"))
            self.assertEqual(resp.status_code, 403, resp.text)
            self.assertEqual(json.loads(self._provider()["extra"]), {"a": 1})
            resp = self.client.put("/api/providers/1", json=body, headers=self._h("adm"))
            self.assertEqual(resp.status_code, 200, resp.text)
        finally:
            os.unlink(path)

    def test_a_write_key_may_do_it_with_a_new_secret(self):
        resp = self.client.put(
            "/api/providers/1",
            json={"url": "http://npm2:81", "password": "fresh-secret"},
            headers=self._h("rw"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_a_write_key_keeps_the_harmless_edits(self):
        resp = self.client.put(
            "/api/providers/1",
            json={"name": "Renamed", "url": "http://npm:81/", "extra": {"a": 1}, "enabled": 0},
            headers=self._h("rw"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._provider()["name"], "Renamed")

    def test_an_admin_key_may_move_it(self):
        resp = self.client.put("/api/providers/1", json={"url": "http://npm2:81"},
                               headers=self._h("adm"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._provider()["url"], "http://npm2:81")


# -------------------------------------------------------------------------------- S4


class S4CrossSiteWritesAreRefusedTests(_IsolatedDB):
    """An open instance grants admin with no cookie: a form on any page could reach it."""

    def setUp(self) -> None:
        super().setUp()
        self._client_cm = TestClient(app_main.app, base_url="http://localhost")
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        super().tearDown()

    def test_a_foreign_origin_cannot_write(self):
        resp = self.client.post("/api/auth/setup-complete",
                                headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._setting("setup_completed"), "")

    def test_a_cross_site_fetch_without_origin_cannot_write(self):
        resp = self.client.post("/api/auth/setup-complete",
                                headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(resp.status_code, 403)

    def test_the_interface_itself_still_writes(self):
        resp = self.client.post("/api/auth/setup-complete",
                                headers={"Origin": "http://localhost",
                                         "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_scripts_without_origin_still_write(self):
        self.assertEqual(self.client.post("/api/auth/setup-complete").status_code, 200)

    def test_reads_are_not_affected(self):
        resp = self.client.get("/api/auth/me", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 200)


# -------------------------------------------------------------------------------- S5


class S5RedirectsDoNotLeaveTheProviderHostTests(unittest.TestCase):
    def _redirect(self, location: str, url: str = "http://pihole.lan/api/auth"):
        import requests

        from app.providers.base import TimeoutSession

        resp = requests.Response()
        resp.status_code = 307
        resp.headers["location"] = location
        resp.request = requests.Request("POST", url, json={"password": "x"}).prepare()
        return TimeoutSession().get_redirect_target(resp)

    def test_a_redirect_to_another_host_is_not_followed(self):
        self.assertIsNone(self._redirect("http://evil.example/collect"))

    def test_a_redirect_on_the_same_host_still_is(self):
        self.assertEqual(self._redirect("/admin/"), "/admin/")
        self.assertEqual(self._redirect("https://pihole.lan/api/auth"), "https://pihole.lan/api/auth")


# ------------------------------------------------------------------------------ S9.3


class S9SessionsEndTests(_IsolatedDB):
    APP_PASSWORD = "test-password-123"

    def setUp(self) -> None:
        super().setUp()
        p = patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", True)
        p.start()
        self._patchers.append(p)
        self._client_cm = TestClient(app_main.app, base_url="http://localhost")
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        super().tearDown()

    def test_a_cookie_copied_before_logout_is_dead_after_it(self):
        self.assertEqual(
            self.client.post("/api/auth/login", json={"password": self.APP_PASSWORD}).status_code, 200
        )
        stolen = self.client.cookies.get("vauxtra_session")
        self.assertTrue(self.client.get("/api/auth/me").json()["authenticated"])

        self.client.post("/api/auth/logout")

        self.client.cookies.set("vauxtra_session", stolen)
        self.assertFalse(self.client.get("/api/auth/me").json()["authenticated"])

    def test_a_session_has_an_absolute_lifetime(self):
        old = {"issued_at": 1}
        self.assertFalse(auth._session_is_fresh(old))
        legacy: dict = {}
        self.assertTrue(auth._session_is_fresh(legacy))
        self.assertIn("issued_at", legacy)


# ------------------------------------------------------------------------------ S9.4


class S9PlaintextComparisonTests(unittest.TestCase):
    def test_a_non_ascii_candidate_is_refused_not_raised(self):
        with patch.object(auth, "APP_PASSWORD", "plain-pass"), \
                patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", True):
            self.assertFalse(auth.check_password("mötdepasse"))
            self.assertTrue(auth.check_password("plain-pass"))


# ----------------------------------------------------------------------------- S9.11


class S9AppriseOneUrlPerWebhookTests(_IsolatedDB):
    def _validate(self, url: str):
        from app.api import webhooks as webhooks_api

        return webhooks_api._validate_apprise_url(url, _request())

    def test_a_second_url_after_a_space_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._validate("discord://id/token json://10.0.0.1/hook")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_a_second_url_after_a_comma_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._validate("discord://id/token,json://10.0.0.1/hook")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_a_hidden_generic_scheme_still_needs_admin(self):
        from app.api import webhooks as webhooks_api

        def _deny(_req, scope=None):
            raise HTTPException(403, scope)

        with patch.object(webhooks_api, "require_auth", _deny):
            with self.assertRaises(HTTPException) as ctx:
                self._validate("discord://id/token,json://10.0.0.1/hook")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_a_single_url_is_accepted(self):
        self.assertEqual(self._validate("discord://id/token"), "discord://id/token")


# -------------------------------------------------------------------------------- S6


class S6EnvPasswordChangeRevokesSessionsTests(_IsolatedDB):
    def _epoch(self) -> str:
        conn = models.get_db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (auth.SESSION_EPOCH_KEY,)
            ).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()

    def _fingerprint_stored(self) -> str:
        conn = models.get_db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (auth.ENV_PASSWORD_FINGERPRINT_KEY,)
            ).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()

    def test_first_boot_records_and_logs_nobody_out(self):
        before = self._epoch()
        with patch.object(auth, "APP_PASSWORD", "first-password"):
            self.assertFalse(auth.revoke_sessions_if_env_password_changed())
        self.assertEqual(self._epoch(), before)
        stored = self._fingerprint_stored()
        self.assertTrue(stored)
        self.assertNotIn("first-password", stored, "the password itself was stored")

    def test_same_password_revokes_nothing(self):
        with patch.object(auth, "APP_PASSWORD", "first-password"):
            auth.revoke_sessions_if_env_password_changed()
            before = self._epoch()
            self.assertFalse(auth.revoke_sessions_if_env_password_changed())
        self.assertEqual(self._epoch(), before)

    def test_a_changed_password_bumps_the_epoch(self):
        with patch.object(auth, "APP_PASSWORD", "first-password"):
            auth.revoke_sessions_if_env_password_changed()
        before = self._epoch()
        with patch.object(auth, "APP_PASSWORD", "second-password"):
            self.assertTrue(auth.revoke_sessions_if_env_password_changed())
        self.assertNotEqual(self._epoch(), before)


# ---------------------------------------------------------------------------- S7


class S7DockerIsNotAPortScannerTests(_WithKeys):
    def test_a_daemon_error_says_nothing_about_the_network(self):
        import sys
        import types

        from app.api import docker as docker_api

        fake = types.ModuleType("docker")

        def _refuse(*_a, **_kw):
            raise ConnectionError("connection refused by 10.0.0.5:2375")

        fake.DockerClient = _refuse
        fake.from_env = _refuse
        with patch.dict(sys.modules, {"docker": fake}):
            with self.assertRaises(HTTPException) as ctx:
                docker_api._docker_client("tcp://10.0.0.5:2375")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertNotIn("refused", str(ctx.exception.detail))
        self.assertNotIn("10.0.0.5", str(ctx.exception.detail))

    def test_a_remote_endpoint_takes_an_admin_key(self):
        body = {"name": "remote", "docker_host": "tcp://10.0.0.5:2375"}
        r = self.client.post("/api/docker/endpoints", json=body, headers=self._h("rw"))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.post("/api/docker/endpoints", json=body, headers=self._h("adm"))
        self.assertEqual(r.status_code, 201, r.text)

    def test_a_local_socket_takes_an_admin_key_too(self):
        """B23: a caller-chosen socket path is no smaller a grant than a remote host."""
        body = {"name": "local", "docker_host": "unix:///run/user/1000/docker.sock"}
        r = self.client.post("/api/docker/endpoints", json=body, headers=self._h("rw"))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.post("/api/docker/endpoints", json=body, headers=self._h("adm"))
        self.assertEqual(r.status_code, 201, r.text)


# ---------------------------------------------------------------------------- S8


class S8LinkLocalProviderUrlsAreRefusedTests(unittest.TestCase):
    def _provider(self, url: str):
        from app.api.providers import ProviderIn

        return ProviderIn(name="x", type="npm", url=url)

    def test_metadata_addresses_are_refused_in_every_spelling(self):
        from pydantic import ValidationError

        for url in (
            "http://169.254.169.254",
            "http://2852039166",
            "http://[fe80::1]:81",
            "http://[::ffff:169.254.169.254]",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                self._provider(url)

    def test_a_lan_address_is_still_accepted(self):
        self.assertTrue(self._provider("http://192.168.1.10:81").url.startswith("http://192.168.1.10"))


# ---------------------------------------------------------------------------- S9.2


class S9LogStreamStopsWhenAccessEndsTests(_IsolatedDB):
    def test_the_stream_ends_once_authentication_fails(self):
        import asyncio

        from app.api import settings as settings_api

        calls = {"n": 0}

        def _auth(request, scope="read"):
            calls["n"] += 1
            if calls["n"] > 1:
                raise HTTPException(401, "Not authenticated")

        class _Req:
            async def is_disconnected(self):
                return False

        async def _collect():
            gen = await settings_api.stream_logs(_Req())
            return [item async for item in gen]

        with patch.object(settings_api, "require_auth", _auth), \
                patch.object(settings_api, "_HAS_SSE", True), \
                patch.object(settings_api, "_SSEResponse", lambda gen: gen):
            items = asyncio.run(asyncio.wait_for(_collect(), timeout=5))
        self.assertEqual(items, [])
        self.assertEqual(calls["n"], 2)


# ---------------------------------------------------------------------------- S9.5


@unittest.skipUnless(os.name == "posix", "file modes are POSIX only")
class S9SecretKeyFileModeTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import config

        self.config = config
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patchers = [
            patch.object(config, "DATA_DIR", self._tmpdir.name),
            patch.dict(os.environ, {"SECRET_KEY": ""}),
        ]
        for p in self._patchers:
            p.start()
        self.key_file = os.path.join(self._tmpdir.name, ".secret_key")

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    def test_a_new_key_file_is_created_0600(self):
        first = self.config._get_or_generate_secret()
        self.assertEqual(os.stat(self.key_file).st_mode & 0o777, 0o600)
        self.assertEqual(self.config._get_or_generate_secret(), first)

    def test_an_old_readable_key_file_is_restricted(self):
        with open(self.key_file, "w") as fh:
            fh.write("stored-value")
        os.chmod(self.key_file, 0o644)
        self.assertEqual(self.config._get_or_generate_secret(), "stored-value")
        self.assertEqual(os.stat(self.key_file).st_mode & 0o777, 0o600)


# ---------------------------------------------------------------------------- S9.6


class S9MetricsAskForAKeyTests(_WithKeys):
    def test_metrics_without_credentials_are_refused(self):
        self.assertEqual(self.client.get("/metrics").status_code, 401)

    def test_metrics_with_a_read_key_are_served(self):
        self.assertEqual(self.client.get("/metrics", headers=self._h("ro")).status_code, 200)


# ---------------------------------------------------------------------------- S9.7


class S9DockerLabelsAreFilteredTests(unittest.TestCase):
    def test_only_routing_labels_without_credentials_are_returned(self):
        from app.api.docker import _public_labels

        labels = {
            "vauxtra.subdomain": "app",
            "traefik.http.routers.app.rule": "Host(`app.example.com`)",
            "traefik.http.middlewares.auth.basicauth.users": "admin:$apr1$hash",
            "com.docker.compose.project": "stack",
            "com.docker.compose.service": "web",
            "com.docker.compose.config-hash": "abc",
            "DB_PASSWORD": "hunter2",
            "vauxtra.api_token": "t",
        }
        self.assertEqual(
            _public_labels(labels),
            {
                "vauxtra.subdomain": "app",
                "traefik.http.routers.app.rule": "Host(`app.example.com`)",
                "com.docker.compose.project": "stack",
                "com.docker.compose.service": "web",
            },
        )


# ---------------------------------------------------------------------------- S9.10


class S9UpstreamTlsIsPerProviderTests(unittest.TestCase):
    def _session_verify(self, extra: str):
        from app.providers.factory import create_provider

        row = {
            "type": "npm", "url": "https://npm.lan", "username": "u",
            "password": "", "extra": extra,
        }
        with patch("app.providers.factory.decrypt_secret", lambda v: v):
            return create_provider(row).session.verify

    def test_certificates_are_verified_by_default(self):
        self.assertIs(self._session_verify("{}"), True)

    def test_a_string_false_does_not_turn_verification_off(self):
        self.assertIs(self._session_verify('{"tls_verify": "false"}'), True)

    def test_a_boolean_false_turns_it_off_for_that_provider(self):
        self.assertIs(self._session_verify('{"tls_verify": false}'), False)

    def test_a_private_ca_bundle_is_used(self):
        self.assertEqual(self._session_verify('{"ca_bundle": "/certs/ca.pem"}'), "/certs/ca.pem")


if __name__ == "__main__":
    unittest.main()
