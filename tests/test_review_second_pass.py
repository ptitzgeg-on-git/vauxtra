"""Regression tests for the second review pass (H2 to H4, M1 to M22, B1 to B26).

Each class names the finding it covers. The isolated-database base classes come from
`test_review_security_fixes.py`; `tests/` is not a package, so they are loaded by path.
H2 lives next to the C13.3 tests in `test_review_correctness_fixes.py`.
"""

import importlib.util
import os
import unittest
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient

import app.main as app_main
from app.providers.desec import DesecProvider
from app.providers.powerdns import PowerDNSProvider
from app.providers.technitium import TechnitiumProvider

_spec = importlib.util.spec_from_file_location(
    "_security_fixtures", os.path.join(os.path.dirname(__file__), "test_review_security_fixes.py")
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)

_IsolatedDB = _fixtures._IsolatedDB
_WithKeys = _fixtures._WithKeys


def _response(status_code: int = 200, json_data=None, links=None, headers=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = {} if json_data is None else json_data
    r.links = links if links is not None else {}
    r.headers = headers if headers is not None else {}
    return r


# -------------------------------------------------------------------------------- H3


class H3TechnitiumUnreadableListIsUnknownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.p._token = "tok"
        self.p._ensure_token = MagicMock(return_value=True)

    def test_a_zone_list_timeout_is_none(self):
        self.p.session.post = MagicMock(side_effect=requests.Timeout("slow"))
        self.assertIsNone(self.p.list_rewrites())

    def test_one_unreadable_zone_makes_the_whole_list_unknown(self):
        def post(url, data=None, **_kw):
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "a.lan"}, {"name": "b.lan"}]}})
            if data and data.get("zone") == "b.lan":
                return _response(500)
            return _response(200, {"status": "ok", "response": {"records": [
                {"name": "x.a.lan", "type": "A", "rData": {"ipAddress": "10.0.0.1"}}]}})

        self.p.session.post = MagicMock(side_effect=post)
        self.assertIsNone(self.p.list_rewrites())

    def test_an_empty_server_is_an_empty_list(self):
        self.p.session.post = MagicMock(return_value=_response(
            200, {"status": "ok", "response": {"zones": []}}))
        self.assertEqual(self.p.list_rewrites(), [])


class H3PowerDNSUnreadableListIsUnknownTests(unittest.TestCase):
    def test_a_refused_zone_body_is_none(self):
        p = PowerDNSProvider("http://pdns:8081", "", "key")

        def get(url, *_a, **_kw):
            if url.endswith("/zones"):
                return _response(200, [{"name": "a.lan.", "id": "a.lan."}])
            return _response(403)

        p.session.get = MagicMock(side_effect=get)
        self.assertIsNone(p.list_rewrites())


class H3DesecUnreadableListIsUnknownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = DesecProvider("", "", "token")

    def test_a_throttled_rrset_listing_is_none(self):
        def get(url, *_a, **_kw):
            if url.endswith("/domains/"):
                return _response(200, [{"name": "a.dedyn.io"}])
            return _response(429, headers={"Retry-After": "60"})

        self.p.session.get = MagicMock(side_effect=get)
        self.assertIsNone(self.p.list_rewrites())

    def test_an_unreadable_domain_list_is_none(self):
        self.p.session.get = MagicMock(return_value=_response(500))
        self.assertIsNone(self.p.list_rewrites())


# -------------------------------------------------------------------------------- M15


class M15DesecThrottleAndPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = DesecProvider("", "", "token")

    def test_a_short_retry_after_is_honoured(self):
        self.p.session.get = MagicMock(side_effect=[
            _response(429, headers={"Retry-After": "1"}),
            _response(200, [{"name": "a.dedyn.io"}]),
        ])
        with patch("app.providers.desec.time.sleep") as sleep:
            got = self.p._get_all("https://desec.io/api/v1/domains/")
        self.assertEqual(got, [{"name": "a.dedyn.io"}])
        sleep.assert_called_once_with(1.0)

    def test_a_long_retry_after_is_not_waited_for(self):
        self.p.session.get = MagicMock(return_value=_response(429, headers={"Retry-After": "3600"}))
        with patch("app.providers.desec.time.sleep") as sleep:
            self.assertIsNone(self.p._get_all("https://desec.io/api/v1/domains/"))
        sleep.assert_not_called()

    def test_the_page_cap_with_pages_left_is_none(self):
        loop = _response(200, [{"name": "a"}],
                         links={"next": {"url": "https://desec.io/api/v1/domains/?cursor=x"}})
        self.p.session.get = MagicMock(return_value=loop)
        self.assertIsNone(self.p._get_all("https://desec.io/api/v1/domains/"))

    def test_rrsets_are_listed_with_an_explicit_cursor(self):
        urls = []

        def get(url, *_a, **_kw):
            urls.append(url)
            if url.endswith("/domains/"):
                return _response(200, [{"name": "a.dedyn.io"}])
            return _response(200, [])

        self.p.session.get = MagicMock(side_effect=get)
        self.assertEqual(self.p.list_rewrites(), [])
        self.assertTrue(any(u.endswith("/rrsets/?cursor=") for u in urls), urls)


# -------------------------------------------------------------------------------- M10


class M10NoParentZoneAfterAnApiErrorTests(unittest.TestCase):
    def test_technitium_does_not_guess_a_zone_after_a_failed_read(self):
        p = TechnitiumProvider("http://dns:5380", "admin", "secret")
        p._ensure_token = MagicMock(return_value=True)
        writes = []

        def post(url, data=None, **_kw):
            if "zones/list" in url:
                return _response(503)
            writes.append(data)
            return _response(200, {"status": "ok"})

        p.session.post = MagicMock(side_effect=post)
        self.assertFalse(p.add_rewrite("app.sub.example.com", "10.0.0.1"))
        self.assertEqual(writes, [], "the record was sent to a guessed parent zone")


# ------------------------------------------------------------------------------ H4 / M1


class _OpenInstanceClient(_IsolatedDB):
    BASE_URL = "http://localhost"

    def setUp(self) -> None:
        super().setUp()
        self._client_cm = TestClient(app_main.app, base_url=self.BASE_URL)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        super().tearDown()


class H4OpenInstanceRefusesRebindableNamesTests(_OpenInstanceClient):
    def test_a_rebound_name_is_refused_on_an_open_instance(self):
        resp = self.client.get("/api/auth/me", headers={"Host": "rebind.attacker.example:8888"})
        self.assertEqual(resp.status_code, 421)

    def test_a_rebound_page_cannot_reset(self):
        resp = self.client.post(
            "/api/auth/setup-complete",
            headers={"Host": "rebind.attacker.example", "Origin": "http://rebind.attacker.example"},
        )
        self.assertEqual(resp.status_code, 421)
        self.assertEqual(self._setting("setup_completed"), "")

    def test_ip_literals_and_localhost_still_work(self):
        for host in ("localhost:8888", "127.0.0.1:8888", "192.168.1.10", "[::1]:8888", "app.localhost"):
            with self.subTest(host=host):
                resp = self.client.get("/api/auth/me", headers={"Host": host})
                self.assertEqual(resp.status_code, 200, host)

    def test_a_cors_origin_host_is_trusted(self):
        with patch.object(app_main, "_cors_origins", ["https://vauxtra.example.com"]):
            resp = self.client.get("/api/auth/me", headers={"Host": "vauxtra.example.com"})
        self.assertEqual(resp.status_code, 200)


class H4ProtectedInstanceAnswersByNameTests(_OpenInstanceClient):
    APP_PASSWORD = "test-password"

    def test_a_name_is_accepted_once_a_password_is_set(self):
        resp = self.client.get("/api/auth/me", headers={"Host": "vauxtra.lan:8888"})
        self.assertEqual(resp.status_code, 200)


class M1ProxyRewritingHostTests(_OpenInstanceClient):
    def test_x_forwarded_host_is_accepted_behind_a_declared_proxy(self):
        with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": "172.18.0.2"}):
            resp = self.client.post(
                "/api/auth/setup-complete",
                headers={"Host": "127.0.0.1:8888", "Origin": "https://vauxtra.example.com",
                         "X-Forwarded-Host": "vauxtra.example.com"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_x_forwarded_host_is_ignored_without_a_proxy(self):
        with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": ""}):
            resp = self.client.post(
                "/api/auth/setup-complete",
                headers={"Host": "127.0.0.1:8888", "Origin": "https://vauxtra.example.com",
                         "X-Forwarded-Host": "vauxtra.example.com"},
            )
        self.assertEqual(resp.status_code, 403)

    def test_the_first_refusal_is_logged(self):
        with patch.object(app_main, "_warned_about_origin_mismatch", False), \
                self.assertLogs("app.main", level="WARNING") as logs:
            self.client.post("/api/auth/setup-complete",
                             headers={"Host": "127.0.0.1:8888", "Origin": "https://other.example"})
        self.assertTrue(any("proxy_set_header Host" in m for m in logs.output))


# ------------------------------------------------------------------------------- M20


class M20WebhooksCannotProbeTheLocalNetworkTests(_WithKeys):
    def _add(self, key: str, url: str):
        return self.client.post("/api/webhooks", headers=self._h(key), json={"name": "n", "url": url})

    def test_a_write_key_cannot_point_a_webhook_at_a_private_address(self):
        for url in ("ntfy://192.168.1.5/topic", "ntfy://localhost/topic", "gotify://[::1]/tok",
                    "ntfy://nas.lan/topic"):
            with self.subTest(url=url):
                self.assertEqual(self._add("rw", url).status_code, 403)

    def test_an_admin_key_still_can(self):
        self.assertEqual(self._add("adm", "ntfy://192.168.1.5/topic").status_code, 201)

    def test_a_public_target_needs_only_write(self):
        self.assertEqual(self._add("rw", "ntfy://ntfy.sh/topic").status_code, 201)

    def test_a_failed_send_does_not_echo_the_exception(self):
        with patch("apprise.Apprise.notify", side_effect=OSError("10.0.0.5:22 SSH-2.0-OpenSSH")):
            resp = self.client.post("/api/webhooks/test-url", headers=self._h("rw"),
                                    json={"url": "ntfy://ntfy.sh/topic"})
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn("OpenSSH", resp.text)
        self.assertNotIn("10.0.0.5", resp.text)

    def test_the_test_routes_are_rate_limited(self):
        from app.api import webhooks as webhooks_api

        for fn in (webhooks_api.test_webhook_url, webhooks_api.test_webhook):
            with self.subTest(route=fn.__name__):
                self.assertTrue(hasattr(fn, "__wrapped__"), "no limiter on this route")


# ------------------------------------------------------------------------------------ B3


class B3SecretKeyFileIsPublishedWholeTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        from app import config

        self.config = config
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(config, "DATA_DIR", self._tmp.name),
            patch.dict(os.environ, {"SECRET_KEY": ""}),
        ]
        for p in self._patches:
            p.start()
        self.key_file = os.path.join(self._tmp.name, ".secret_key")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def test_the_key_file_never_exists_empty(self):
        real_link = os.link
        seen = []

        def link(src, dst):
            seen.append(os.path.exists(dst))
            with open(src) as fh:
                seen.append(len(fh.read()))
            return real_link(src, dst)

        with patch("app.config.os.link", side_effect=link):
            key = self.config._get_or_generate_secret()
        self.assertEqual(seen, [False, 64], "the key file was visible before it was complete")
        with open(self.key_file) as fh:
            self.assertEqual(fh.read(), key)
        self.assertEqual(os.listdir(self._tmp.name), [".secret_key"], "temporary file left behind")

    def test_a_worker_that_loses_the_race_adopts_the_winner_key(self):
        def lose(_src, dst):
            with open(dst, "w") as fh:
                fh.write("winner")
            raise FileExistsError(dst)

        with patch("app.config.os.link", side_effect=lose):
            self.assertEqual(self.config._get_or_generate_secret(), "winner")
        self.assertEqual(os.listdir(self._tmp.name), [".secret_key"])


# ----------------------------------------------------------------------------- B19 / B20


class B19FailedLoginDelayHoldsNoThreadTests(_WithKeys):
    def test_the_delay_is_awaited_not_slept(self):
        from unittest.mock import AsyncMock

        with patch("app.api.auth.asyncio.sleep", new_callable=AsyncMock) as async_sleep, \
                patch("app.api.auth.time.sleep") as blocking_sleep:
            resp = self.client.post("/api/auth/login", json={"password": "wrong-password"})
        self.assertEqual(resp.status_code, 401)
        async_sleep.assert_awaited_once_with(0.5)
        blocking_sleep.assert_not_called()

    def test_the_right_password_still_logs_in(self):
        with patch.object(_fixtures.auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", True):
            resp = self.client.post("/api/auth/login", json={"password": self.APP_PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.text)


class B20KeyUsageStampDoesNotWaitOnAWriterTests(_WithKeys):
    def test_a_busy_database_does_not_delay_a_valid_key(self):
        import time as _time

        from app.api.api_keys import verify_api_key

        blocker = _fixtures.models.get_db()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            started = _time.monotonic()
            row = verify_api_key("key-rw")
            elapsed = _time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()
        self.assertIsNotNone(row)
        self.assertLess(elapsed, 3.0, "the usage stamp waited out the full busy_timeout")


# ----------------------------------------------------------------------------------- B15


class B15ReadKeysDoNotWriteTests(_WithKeys):
    def setUp(self) -> None:
        super().setUp()
        self._exec(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port, status)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 9, 'unknown')"""
        )

    def test_a_read_key_cannot_run_a_check_that_writes_the_status(self):
        resp = self.client.post("/api/services/1/check", headers=self._h("ro"))
        self.assertEqual(resp.status_code, 403, resp.text)
        conn = _fixtures.models.get_db()
        try:
            row = conn.execute("SELECT status, last_checked FROM services WHERE id=1").fetchone()
        finally:
            conn.close()
        self.assertEqual((row["status"], row["last_checked"]), ("unknown", None))

    def test_a_write_key_still_can(self):
        with patch("app.api.services.socket.create_connection", side_effect=OSError), \
                patch("app.api.services.socket.getaddrinfo", return_value=[]):
            resp = self.client.post("/api/services/1/check", headers=self._h("rw"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "error")

    def test_a_read_key_cannot_make_the_server_list_every_provider(self):
        self.assertEqual(self.client.post("/api/services/sync", headers=self._h("ro")).status_code, 403)

    def test_a_malformed_target_name_is_an_error_not_a_500(self):
        with patch("app.api.services.socket.create_connection", side_effect=UnicodeError("label")), \
                patch("app.api.services.socket.getaddrinfo", return_value=[]):
            resp = self.client.post("/api/services/1/check", headers=self._h("rw"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "error")


# ----------------------------------------------------------------------------------- B12


class B12TagNamesTests(_WithKeys):
    def test_a_unique_violation_is_a_409(self):
        from app.api import tags as tags_api

        self._exec("INSERT INTO tags (id, name) VALUES (1, 'prod')")
        real_get_db = tags_api.get_db

        class _SkipsTheCheck:
            """A connection whose pre-check misses the row, as a racing request would."""

            def __init__(self):
                self._c = real_get_db()

            def execute(self, sql, params=()):
                if sql.startswith("SELECT id FROM tags WHERE name"):
                    return self._c.execute("SELECT NULL WHERE 0")
                return self._c.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._c, name)

        with patch.object(tags_api, "get_db", _SkipsTheCheck):
            resp = self.client.post("/api/tags", headers=self._h("rw"), json={"name": "prod"})
        self.assertEqual(resp.status_code, 409, resp.text)

    def test_a_restore_renames_a_tag_name_the_api_would_refuse(self):
        """C9 replaced the refusal: one old tag used to fail the restore of the whole file."""
        self._exec("INSERT INTO tags (id, name) VALUES (1, 'kept')")
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        backup["tags"] = [{"id": 2, "name": "a:b", "color": "blue"}]
        resp = self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()["renamed"]), 1)
        conn = _fixtures.models.get_db()
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM tags")]
        finally:
            conn.close()
        self.assertEqual(names, ["a-b"])


# ----------------------------------------------------------------------------------- B22


class B22EntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        path = os.path.join(os.path.dirname(__file__), "..", "docker-entrypoint.sh")
        with open(path, encoding="utf-8") as fh:
            self.script = fh.read()

    def test_appuser_never_joins_the_root_group(self):
        self.assertIn('if [ "$SOCK_GID" = "0" ]', self.script)
        guarded = self.script.split('if [ "$SOCK_GID" = "0" ]', 1)[1].split("else", 1)[0]
        self.assertNotIn("usermod", guarded)

    def test_ownership_is_fixed_without_following_links(self):
        self.assertNotIn("chown -R", self.script)
        self.assertIn("chown -h appuser:appuser", self.script)

    def test_a_non_root_start_skips_the_root_only_steps(self):
        head, _, rest = self.script.partition('if [ "$(id -u)" != "0" ]; then')
        self.assertTrue(rest, "no branch for a container started with --user")
        branch = rest.split("\nfi\n", 1)[0]
        self.assertIn("exec uvicorn", branch)
        code = "\n".join(
            line for line in (head + branch).splitlines() if not line.lstrip().startswith("#")
        )
        for root_only in ("gosu", "chown", "usermod", "groupadd"):
            self.assertNotIn(root_only, code)

    def test_the_script_keeps_lf_line_endings(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "docker-entrypoint.sh"), "rb") as fh:
            self.assertNotIn(b"\r\n", fh.read())


# ----------------------------------------------------------------------------------- B16


class B16MetricsTests(_WithKeys):
    def test_warnings_are_counted(self):
        _fixtures.models.add_log("warn", "first")
        _fixtures.models.add_log("warning", "second")
        body = self.client.get("/metrics", headers=self._h("ro")).text
        self.assertIn('vauxtra_logs_24h{level="warn"} 2', body)

    def test_label_values_are_escaped(self):
        from app.api.metrics import _gauge

        self.assertEqual(_gauge("m", 1, {"type": 'a"b\\c\nd'}), 'm{type="a\\"b\\\\c\\nd"} 1')


# ------------------------------------------------------------------------------ B2 / B13


class B2EnvPasswordFingerprintSurvivesAResetTests(_WithKeys):
    def test_reset_and_restore_keep_the_fingerprint(self):
        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES "
                   "('app_password_env_fingerprint', 'fp')")
        self.assertEqual(self.client.post("/api/reset", headers=self._h("adm")).status_code, 200)
        self.assertEqual(self._setting("app_password_env_fingerprint"), "fp")
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        self.assertEqual(
            self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup}).status_code,
            200,
        )
        self.assertEqual(self._setting("app_password_env_fingerprint"), "fp")


class B13ClearingTheJournalTakesAdminTests(_WithKeys):
    def test_a_write_key_cannot_clear_the_logs(self):
        self._exec("INSERT INTO logs (level, message) VALUES ('info', 'evidence')")
        self.assertEqual(self.client.post("/api/logs/clear", headers=self._h("rw")).status_code, 403)
        conn = _fixtures.models.get_db()
        try:
            kept = conn.execute("SELECT COUNT(*) FROM logs WHERE message='evidence'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(kept, 1)

    def test_an_admin_key_can(self):
        self.assertEqual(self.client.post("/api/logs/clear", headers=self._h("adm")).status_code, 200)


# ------------------------------------------------------------------------------- M21


class M21TemplatesSurviveABackupRoundTripTests(_WithKeys):
    def test_a_template_comes_back_after_export_and_restore(self):
        self._exec(
            """INSERT INTO service_templates (id, name, target_port, proxy_provider_id, domain)
               VALUES (4, 'web', 8080, 1, 'example.com')"""
        )
        exported = self.client.get("/api/backup", headers=self._h("adm"))
        self.assertEqual(exported.status_code, 200, exported.text)
        backup = exported.json()
        self.assertEqual([t["name"] for t in backup["service_templates"]], ["web"])

        restored = self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})
        self.assertEqual(restored.status_code, 200, restored.text)
        conn = _fixtures.models.get_db()
        try:
            row = conn.execute("SELECT * FROM service_templates WHERE id=4").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "the restore wiped the template and did not write it back")
        self.assertEqual((row["name"], row["target_port"], row["proxy_provider_id"]), ("web", 8080, 1))

    def test_an_older_file_without_templates_still_restores(self):
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        backup.pop("service_templates")
        restored = self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})
        self.assertEqual(restored.status_code, 200, restored.text)


# -------------------------------------------------------------------------- B17, M3


class B17RestoreGoesThroughTheValidatorsTests(_WithKeys):
    def _restore(self, **rows):
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        backup.update(rows)
        return self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})

    def _service_count(self) -> int:
        conn = _fixtures.models.get_db()
        try:
            return conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        finally:
            conn.close()

    def test_a_provider_on_the_metadata_address_is_refused(self):
        r = self._restore(providers=[{"id": 9, "name": "meta", "type": "npm", "url": "http://169.254.169.254"}])
        self.assertEqual(r.status_code, 400, r.text)

    def test_an_invalid_docker_endpoint_is_refused(self):
        r = self._restore(docker_endpoints=[{"id": 3, "name": "x", "docker_host": "http://nope"}])
        self.assertEqual(r.status_code, 400, r.text)

    def test_an_invalid_service_is_left_out_and_reported(self):
        """C9: it used to fail the restore of the whole file; the others now come back."""
        r = self._restore(services=[
            {"id": 2, "subdomain": "bad", "domain": "example.com", "target_ip": "10.0.0.2", "target_port": 70000},
            {"id": 3, "subdomain": "good", "domain": "example.com", "target_ip": "10.0.0.3", "target_port": 80},
        ], service_tags=[{"service_id": 2, "tag_id": 1}], tags=[{"id": 1, "name": "t"}])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["skipped"]), 1)
        self.assertEqual(self._service_count(), 1)

    def test_a_valid_service_is_stored_normalised(self):
        r = self._restore(services=[
            {"id": 2, "subdomain": "App", "domain": "Example.COM", "target_ip": "10.0.0.2", "target_port": 80},
        ])
        self.assertEqual(r.status_code, 200, r.text)
        conn = _fixtures.models.get_db()
        try:
            row = conn.execute("SELECT subdomain, domain FROM services WHERE id=2").fetchone()
        finally:
            conn.close()
        self.assertEqual((row["subdomain"], row["domain"]), ("app", "example.com"))

    def test_a_database_error_is_not_echoed(self):
        # A non-integer id in an INTEGER PRIMARY KEY column: SQLite's "datatype mismatch".
        r = self._restore(webhooks=[{"id": "not-an-int", "name": "w"}])
        self.assertEqual(r.status_code, 500, r.text)
        self.assertNotIn("datatype", r.text.lower())
        self.assertNotIn("sqlite", r.text.lower())


# -------------------------------------------------------------------------------- M5

_ms_spec = importlib.util.spec_from_file_location(
    "_multisync_fixtures", os.path.join(os.path.dirname(__file__), "test_multi_sync_targets.py")
)
_ms = importlib.util.module_from_spec(_ms_spec)
_ms_spec.loader.exec_module(_ms)


class M5NoWriteTransactionAcrossProviderCallsTests(_ms._MultiSyncTestCase):
    """SQLite has one writer: no route may hold it while a provider answers over HTTP."""

    def _service_row(self, sid: int):
        conn = _ms.models.get_db()
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return row

    def test_the_push_commits_before_resolving_the_public_target(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertEqual(status, 201, body)
        sid = body["id"]
        seen = []
        original = _ms.sync_api.resolve_dns_target

        def spy(conn, **kw):
            seen.append(conn.in_transaction)
            return original(conn, **kw)

        conn = _ms.models.get_db()
        try:
            with patch.object(_ms.sync_api, "resolve_dns_target", spy):
                _ms.sync_api._push_service_row(conn, self._service_row(sid), sid)
        finally:
            conn.close()
        self.assertEqual(seen, [False])

    def test_the_bulk_toggle_commits_the_relocated_host_id_before_toggling(self):
        proxy = self._add_provider(1, "Zoraxy", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertEqual(status, 201, body)
        sid = body["id"]
        conn = _ms.models.get_db()
        # Disabled first: since M3 a bulk enable only acts on a real transition.
        conn.execute("UPDATE services SET npm_host_id='old.example.com', enabled=0 WHERE id=?", (sid,))
        conn.commit()
        conn.close()

        seen = []
        captured = {}
        real_get_db = _ms.services_api.get_db

        def tracking_get_db():
            captured["conn"] = real_get_db()
            return captured["conn"]

        def toggle(host_id, enabled):
            seen.append(captured["conn"].in_transaction)
            return True

        proxy.toggle_host = toggle
        with patch.object(_ms.services_api, "get_db", tracking_get_db), \
                patch.object(_ms.services_api, "host_id_is_hostname", lambda *_a: True):
            _ms.services_api.bulk_action(
                _ms.services_api._BulkActionBody(action="enable", ids=[sid]), _ms._request()
            )
        self.assertEqual(seen, [False])

    def test_a_failed_npm_status_sync_does_not_carry_a_transaction_into_the_next(self):
        self._add_provider(1, "NPM", "npm")
        good = self._add_provider(3, "NPM bis", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        conn = _ms.models.get_db()
        for sid, pid, sub in ((1, 1, "a"), (2, 3, "b")):
            conn.execute(
                """INSERT INTO services (id, subdomain, domain, target_ip, target_port,
                   proxy_provider_id, npm_host_id, expose_mode, enabled)
                   VALUES (?,?,?,?,?,?,?,'proxy_dns',1)""",
                (sid, sub, "example.com", "10.0.0.9", 80, pid, 7),
            )
        conn.commit()

        def broken_list():
            conn.execute("UPDATE services SET enabled=0 WHERE id=1")
            raise RuntimeError("proxy unreachable")

        seen = []

        def good_list():
            seen.append(conn.in_transaction)
            return []

        self.providers[1].list_hosts = broken_list
        good.list_hosts = good_list
        try:
            _ms.services_api._sync_npm_statuses(conn)
            enabled = conn.execute("SELECT enabled FROM services WHERE id=1").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(seen, [False])
        self.assertEqual(enabled, 1, "the failed service's half-written change was kept")


# ---------------------------------------------------------------------------- B14


class B14LogStreamOffTheLoopAndCappedTests(_IsolatedDB):
    def test_sqlite_runs_in_a_worker_thread_and_the_slot_is_released(self):
        import asyncio
        import threading

        from fastapi import HTTPException

        from app.api import settings as settings_api

        loop_thread = {}
        db_threads = []
        real_get_db = settings_api.get_db
        calls = {"n": 0}

        def _get_db():
            db_threads.append(threading.get_ident())
            return real_get_db()

        def _auth(request, scope="read"):
            calls["n"] += 1
            if calls["n"] > 2:
                raise HTTPException(401, "Not authenticated")

        class _Req:
            async def is_disconnected(self):
                return False

        async def _collect():
            loop_thread["id"] = threading.get_ident()
            gen = await settings_api.stream_logs(_Req())
            return [item async for item in gen]

        with patch.object(settings_api, "require_auth", _auth), \
                patch.object(settings_api, "get_db", _get_db), \
                patch.object(settings_api, "_HAS_SSE", True), \
                patch.object(settings_api.asyncio, "sleep", AsyncSleepStub()), \
                patch.object(settings_api, "_SSEResponse", lambda gen: gen):
            asyncio.run(asyncio.wait_for(_collect(), timeout=5))
        self.assertTrue(db_threads)
        self.assertNotIn(loop_thread["id"], db_threads)
        self.assertEqual(settings_api._open_log_streams, 0)

    def test_a_stream_beyond_the_cap_is_refused(self):
        import asyncio

        from fastapi import HTTPException

        from app.api import settings as settings_api

        class _Req:
            async def is_disconnected(self):
                return True

        with patch.object(settings_api, "require_auth", lambda request, scope="read": None), \
                patch.object(settings_api, "_HAS_SSE", True), \
                patch.object(settings_api, "_stream_identity", lambda _r: ("key:1", False)), \
                patch.object(settings_api, "_open_log_streams", settings_api._MAX_LOG_STREAMS):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(settings_api.stream_logs(_Req()))
        self.assertEqual(ctx.exception.status_code, 429)


class AsyncSleepStub:
    async def __call__(self, _delay):
        return None


# ---------------------------------------------------------------------------- B5, B7, B10, B11


class _ServiceRoutes(_ms._MultiSyncTestCase):
    def _service_row(self, sid: int) -> dict:
        conn = _ms.models.get_db()
        try:
            return dict(conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone())
        finally:
            conn.close()


class B5FailedCleanupAtTheFormerProxyTests(_ServiceRoutes):
    def _pending(self) -> list[tuple]:
        conn = _ms.models.get_db()
        try:
            return [
                (r["provider_id"], r["host_id"])
                for r in conn.execute("SELECT * FROM pending_proxy_removals ORDER BY id")
            ]
        finally:
            conn.close()

    def test_the_removal_is_queued_and_the_reply_is_207(self):
        """C3: the former proxy is no longer kept as the service's own; its removal is queued."""
        from fastapi import Response

        proxy = self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertEqual(status, 201)
        sid = body["id"]
        host_id = self._service_row(sid)["npm_host_id"]
        self.assertIsNotNone(host_id)

        proxy.ok = False
        response = Response()
        result = _ms.services_api.update_service(
            sid, _ms._request(), self._body(proxy_provider_id=None, dns_provider_id=2), response
        )
        row = self._service_row(sid)
        self.assertEqual(response.status_code, 207)
        self.assertTrue(result["errors"])
        self.assertEqual((row["proxy_provider_id"], row["npm_host_id"]), (None, None))
        self.assertEqual(self._pending(), [(1, str(host_id))])

        # The scheduler retries it once the proxy accepts again.
        proxy.ok = True
        conn = _ms.models.get_db()
        try:
            self.assertEqual(_ms.sync_api.retry_pending_proxy_removals(conn), 1)
        finally:
            conn.close()
        self.assertEqual(self._pending(), [])
        self.assertFalse(proxy.holds("vault.example.com"))

    def test_a_route_taken_back_is_not_removed_by_the_retry(self):
        proxy = self._add_provider(1, "NPM", "npm")
        _status, body = self._create(proxy_provider_id=1, dns_ip="")
        sid = body["id"]
        host_id = self._service_row(sid)["npm_host_id"]
        self._exec_sql(
            "INSERT INTO pending_proxy_removals (provider_id, host_id, public_host) VALUES (1, ?, 'x')",
            (str(host_id),),
        )
        conn = _ms.models.get_db()
        try:
            self.assertEqual(_ms.sync_api.retry_pending_proxy_removals(conn), 0)
        finally:
            conn.close()
        self.assertEqual(self._pending(), [])
        self.assertTrue(proxy.holds("vault.example.com"), "the live route was removed")

    def test_changing_proxy_reports_a_refused_removal(self):
        """N1: a change of proxy ignored the former proxy's refusal."""
        from fastapi import Response

        old = self._add_provider(1, "NPM", "npm")
        self._add_provider(3, "NPM2", "npm")
        _status, body = self._create(proxy_provider_id=1, dns_ip="")
        sid = body["id"]
        old.ok = False
        response = Response()
        result = _ms.services_api.update_service(
            sid, _ms._request(), self._body(proxy_provider_id=3, dns_ip=""), response
        )
        self.assertEqual(response.status_code, 207)
        self.assertTrue(any("former proxy" in e for e in result["errors"]))
        self.assertEqual(self._service_row(sid)["proxy_provider_id"], 3)
        self.assertEqual([p for p, _h in self._pending()], [1])

    def _exec_sql(self, sql, params=()):
        conn = _ms.models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def test_a_successful_cleanup_still_drops_the_binding(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _status, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        result = _ms.services_api.update_service(
            sid, _ms._request(), self._body(proxy_provider_id=None, dns_provider_id=2)
        )
        row = self._service_row(sid)
        self.assertEqual(result["errors"], [])
        self.assertEqual((row["proxy_provider_id"], row["npm_host_id"]), (None, None))


class B7DriftReadsNamesWithoutCaseAndReportsUnreadableListsTests(_ServiceRoutes):
    def _drift(self, sid):
        conn = _ms.models.get_db()
        try:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
            return _ms.sync_api._compute_service_drift(conn, svc, sid)
        finally:
            conn.close()

    def test_a_route_spelled_in_capitals_is_found(self):
        proxy = self._add_provider(1, "NPM", "npm")
        _status, body = self._create(proxy_provider_id=1, dns_ip="")
        proxy.hosts[0]["domains"] = ["Vault.Example.COM"]
        types = {i["type"] for i in self._drift(body["id"])["issues"]}
        self.assertNotIn("missing_proxy_route", types)

    def test_an_unreadable_list_is_a_failed_check_not_a_missing_route(self):
        proxy = self._add_provider(1, "NPM", "npm")
        dns = self._add_provider(2, "AdGuard", "adguard")
        _status, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        proxy.list_hosts = lambda: None
        dns.list_rewrites = lambda: None
        types = {i["type"] for i in self._drift(body["id"])["issues"]}
        self.assertEqual(types, {"proxy_check_failed", "dns_check_failed"})


class B10B11ImportsTests(_ServiceRoutes):
    def _import(self, proxy_hosts):
        with patch.object(_ms.sync_api, "require_auth_or_setup", lambda _req, scope=None: None):
            return _ms.sync_api.import_services(
                _ms._request(), _ms.sync_api.ServicesImportIn(proxy_hosts=proxy_hosts)
            )

    def test_a_name_differing_only_in_case_is_a_duplicate(self):
        self._add_provider(1, "NPM", "npm")
        self._create(proxy_provider_id=1, dns_ip="")
        result = self._import([{"domains": ["VAULT.Example.com"], "host": "10.0.0.3", "_provider_id": 1}])
        self.assertEqual(result["imported"], 0)

    def test_the_docker_import_compares_without_case_too(self):
        from app.api import docker as docker_api

        self._add_provider(1, "NPM", "npm")
        self._create(proxy_provider_id=1, dns_ip="")
        with patch.object(docker_api, "require_auth", lambda _req, scope=None: None):
            result = docker_api.import_docker_containers(_ms._request(), {
                "domain": "Example.com",
                "containers": [{"name": "x", "subdomain": "VAULT", "target_ip": "10.0.0.4", "target_port": 80}],
            })
        self.assertEqual(result["imported"], 0)

    def test_a_route_id_must_exist_and_be_free(self):
        proxy = self._add_provider(1, "NPM", "npm")
        _status, body = self._create(proxy_provider_id=1, dns_ip="")
        bound_id = self._service_row(body["id"])["npm_host_id"]
        proxy.hosts.append({"id": 555, "domains": ["free.example.com"], "host": "10.0.0.5", "port": 80})

        result = self._import([
            {"id": 999, "domains": ["ghost.example.com"], "host": "10.0.0.3", "_provider_id": 1},
            {"id": bound_id, "domains": ["twin.example.com"], "host": "10.0.0.3", "_provider_id": 1},
            {"id": 555, "domains": ["free.example.com"], "host": "10.0.0.5", "_provider_id": 1},
            {"id": 555, "domains": ["again.example.com"], "host": "10.0.0.5", "_provider_id": 1},
        ])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["errors"]), 3)
        conn = _ms.models.get_db()
        try:
            hosts = [r[0] for r in conn.execute("SELECT npm_host_id FROM services WHERE subdomain='free'")]
        finally:
            conn.close()
        self.assertEqual(hosts, [555])


class B18ImportBodyIsTypedTests(_WithKeys):
    def test_a_body_of_the_wrong_shape_is_a_422(self):
        r = self.client.post("/api/services/import", json=["not", "an", "object"], headers=self._h("rw"))
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/services/import", json={"proxy_hosts": "x"}, headers=self._h("rw"))
        self.assertEqual(r.status_code, 422)

    def test_webhook_bodies_of_the_wrong_shape_are_a_422(self):
        h = self._h("rw")
        for method, path, body in (
            ("post", "/api/webhooks", {"name": 5, "url": "mailto://a@example.com"}),
            ("post", "/api/webhooks", {"name": "a", "url": "mailto://a@example.com", "min_down_minutes": "x"}),
            ("post", "/api/webhooks/test-url", ["mailto://a@example.com"]),
            ("post", "/api/services/1/alerts", {"alerts": "nope"}),
        ):
            with self.subTest(path=path, body=body):
                r = getattr(self.client, method)(path, json=body, headers=h)
                self.assertEqual(r.status_code, 422, r.text)

    def test_a_partial_webhook_update_still_works(self):
        h = self._h("rw")
        created = self.client.post(
            "/api/webhooks", json={"name": "ops", "url": "mailto://a@example.com"}, headers=h
        )
        self.assertEqual(created.status_code, 201, created.text)
        r = self.client.put(f"/api/webhooks/{created.json()['id']}", json={"enabled": False}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["name"], r.json()["enabled"]), ("ops", 0))

    def test_a_template_naming_an_unknown_provider_is_a_400(self):
        h = self._h("rw")
        r = self.client.post("/api/templates", json={"name": "t", "proxy_provider_id": 999}, headers=h)
        self.assertEqual(r.status_code, 400, r.text)
        ok = self.client.post("/api/templates", json={"name": "t"}, headers=h)
        self.assertEqual(ok.status_code, 201, ok.text)
        r = self.client.put(f"/api/templates/{ok.json()['id']}", json={"name": "t", "dns_provider_id": 999}, headers=h)
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.post("/api/templates", json={"name": "u", "public_target_mode": "weird"}, headers=h)
        self.assertEqual(r.status_code, 422, r.text)


if __name__ == "__main__":
    unittest.main()
