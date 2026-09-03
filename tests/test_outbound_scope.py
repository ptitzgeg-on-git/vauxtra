"""Who gets to make this instance send a request somewhere, and to where.

Three keys in the API let the caller choose a URL that *Vauxtra itself* then fetches, from
inside whatever network it is deployed in. All three asked for `write`:

- `public_target_sources` -- the resolvers polled to discover this host's WAN address, on a
  schedule, with the answer written into public DNS for every service in `auto` mode.
- an Apprise URL on one of the six generic schemes (`json://`, `form://`, `xml://` and
  their TLS forms), which name a host and a body rather than a service.
- `POST /api/webhooks/test-url`, which sends one of those immediately and stores nothing.

A key minted for a monitoring dashboard could reach every one of them. They now ask for
`admin`; none of them is refused outright, because posting JSON to your own service is a
fair reason to run a tool like this one.

The rest of the file is about the answers coming back, and about the numbers written down
next to them: a resolver that replies `127.0.0.1` used to be believed, the secure-export
passphrase floor was eight where the login password's was twelve, and three `minLength={8}`
attributes let the browser accept what the server was always going to refuse.
"""

import base64
import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as app_db
import app.public_target as public_target
import app.scheduler as scheduler
import app.security as security
from app import models
from app.config import encrypt_for_backup

REPO_ROOT = Path(__file__).resolve().parents[1]

PASSWORD = "test-password-long-enough"

# Every apprise scheme whose whole point is "POST this body to that host".
GENERIC_SCHEMES = ("json", "jsons", "form", "forms", "xml", "xmls")


class _ScopedClient(unittest.TestCase):
    """A temp database, one API key per scope, and no rate limiting in the way.

    `tests/` is not a package, so this base class is duplicated per file by design.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "outbound.test.db")
        models.init_db()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            # Without a password every request is granted `admin`, and no scope is tested.
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
            for name, scopes in (("ro", "read"), ("rw", "write"), ("adm", "admin")):
                conn.execute(
                    "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                    (name, hashlib.sha256(f"key-{name}".encode()).hexdigest(), name[:8], scopes),
                )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- helpers ----------------------------------------------------------------

    def _headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer key-{key}"}

    def _setting(self, key: str) -> str | None:
        conn = models.get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def _write(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


class ChoosingTheUrlsTheServerPollsNeedsAdminTests(_ScopedClient):
    """`public_target_sources` names hosts this instance connects to, unattended."""

    METADATA = "http://169.254.169.254/latest/meta-data/"

    def test_a_write_key_may_no_longer_aim_the_resolver(self) -> None:
        resp = self.client.post(
            "/api/settings",
            json={"public_target_sources": self.METADATA},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("admin", resp.json().get("detail", ""))

    def test_an_admin_key_still_may(self) -> None:
        resp = self.client.post(
            "/api/settings",
            json={"public_target_sources": "https://api.ipify.org"},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._setting("public_target_sources"), "https://api.ipify.org")

    def test_the_refusal_saves_nothing_at_all(self) -> None:
        """The route is all-or-nothing on validation; the scope check keeps it that way."""
        resp = self.client.post(
            "/api/settings",
            json={"public_target_sources": self.METADATA, "public_target_timeout": "3.0"},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIsNone(self._setting("public_target_sources"))
        self.assertIsNone(self._setting("public_target_timeout"))

    def test_the_two_wan_keys_that_hold_no_url_stay_at_write(self) -> None:
        """A timeout is a number and a priority is one of three fixed words.

        Widening the rule to the whole WAN-policy form would make the ordinary case --
        an operator lowering a timeout -- need a credential it has no reason to need.
        """
        resp = self.client.post(
            "/api/settings",
            json={"public_target_timeout": "3.0", "public_target_priority": "current"},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._setting("public_target_timeout"), "3.0")

    def test_a_read_key_is_refused_for_being_read_only_first(self) -> None:
        resp = self.client.post(
            "/api/settings",
            json={"public_target_sources": "https://api.ipify.org"},
            headers=self._headers("ro"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("'write' required", resp.json().get("detail", ""))

    def test_an_ordinary_setting_is_untouched_by_the_new_pre_pass(self) -> None:
        resp = self.client.post(
            "/api/settings", json={"check_interval": "10"}, headers=self._headers("rw")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._setting("check_interval"), "10")


class AGenericAppriseSchemeIsAnOutboundRequestTests(_ScopedClient):
    """`json://host/path` is not a notification service. It is a POST the caller composed."""

    def test_every_generic_scheme_needs_admin_to_be_stored(self) -> None:
        for scheme in GENERIC_SCHEMES:
            with self.subTest(scheme=scheme):
                resp = self.client.post(
                    "/api/webhooks",
                    json={"name": "x", "url": f"{scheme}://internal.example/collect"},
                    headers=self._headers("rw"),
                )
                self.assertEqual(resp.status_code, 403, resp.text)
                self.assertIn("admin", resp.json().get("detail", ""))

    def test_the_scheme_is_read_without_regard_to_case(self) -> None:
        resp = self.client.post(
            "/api/webhooks",
            json={"name": "x", "url": "JSON://internal.example/collect"},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_a_scheme_that_names_a_service_is_still_a_write(self) -> None:
        """The six are gated because they name a host and a body. `discord://` names Discord."""
        resp = self.client.post(
            "/api/webhooks",
            json={"name": "chat", "url": "discord://1234567890/abcdefghijklmnop"},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_an_admin_key_may_store_one(self) -> None:
        resp = self.client.post(
            "/api/webhooks",
            json={"name": "self-hosted", "url": "json://collector.lan/hook"},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_the_route_that_sends_without_storing_is_the_same_request(self) -> None:
        resp = self.client.post(
            "/api/webhooks/test-url",
            json={"url": "json://internal.example/collect"},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("admin", resp.json().get("detail", ""))

    def test_an_admin_key_reaches_the_send_attempt_on_that_route(self) -> None:
        with patch("apprise.Apprise.notify", return_value=True) as notify:
            resp = self.client.post(
                "/api/webhooks/test-url",
                json={"url": "json://collector.lan/hook"},
                headers=self._headers("adm"),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(notify.called)

    def test_toggling_a_webhook_an_admin_created_stays_a_write(self) -> None:
        """A partial update carries no `url`, so there is no URL for the caller to choose.

        Charging admin for `{"enabled": false}` would make the scope depend on a value the
        caller never sent, which is how a security rule turns into a support ticket.
        """
        self._write(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'x', 'json://c.lan/h', 1)"
        )
        resp = self.client.put(
            "/api/webhooks/1", json={"enabled": False}, headers=self._headers("rw")
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_but_rewriting_that_url_does_not(self) -> None:
        self._write(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'x', 'json://c.lan/h', 1)"
        )
        resp = self.client.put(
            "/api/webhooks/1",
            json={"url": "json://elsewhere.example/collect"},
            headers=self._headers("rw"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_the_gate_is_the_scope_and_not_a_ban(self) -> None:
        """Nothing here refuses the schemes themselves; a UI session is always admin."""
        from app.api.webhooks import _GENERIC_HTTP_SCHEMES

        self.assertEqual(
            sorted(_GENERIC_HTTP_SCHEMES),
            sorted(f"{s}://" for s in GENERIC_SCHEMES),
        )


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self, size: int) -> bytes:
        return self._body[:size]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class AResolverThatAnswersWithALanAddressIsNotAnAnswerTests(unittest.TestCase):
    """Whatever comes back here is written into public DNS for every `auto` service."""

    def _urlopen(self, answers: dict):
        def fake(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url not in answers:
                raise OSError("unreachable")
            return _FakeResponse(answers[url])

        return fake

    def test_an_address_the_outside_world_cannot_route_back_is_refused(self) -> None:
        for answer in (
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "172.16.0.1",
            "169.254.169.254",
            "0.0.0.0",
            "240.0.0.1",
            "::1",
            "fd00::1",
        ):
            with self.subTest(answer=answer):
                with patch.object(
                    public_target, "urlopen", self._urlopen({"https://r.example": answer})
                ):
                    self.assertEqual(
                        public_target.detect_server_public_ip(
                            sources=["https://r.example"], timeout_seconds=0.1
                        ),
                        "",
                    )

    def test_a_public_answer_is_returned_unchanged(self) -> None:
        with patch.object(
            public_target, "urlopen", self._urlopen({"https://r.example": "93.184.216.34"})
        ):
            self.assertEqual(
                public_target.detect_server_public_ip(
                    sources=["https://r.example"], timeout_seconds=0.1
                ),
                "93.184.216.34",
            )

    def test_the_refused_source_is_named_out_loud(self) -> None:
        """A resolver answering with a LAN address is broken, not empty. Skipping it in
        silence leaves the operator with `auto_unavailable` and nothing to read."""
        with patch.object(
            public_target, "urlopen", self._urlopen({"https://broken.example": "192.168.1.1"})
        ):
            with self.assertLogs("app.public_target", level="WARNING") as logs:
                public_target.detect_server_public_ip(
                    sources=["https://broken.example"], timeout_seconds=0.1
                )
        joined = "\n".join(logs.output)
        self.assertIn("https://broken.example", joined)
        self.assertIn("192.168.1.1", joined)

    def test_a_bad_source_does_not_stop_the_next_one_from_answering(self) -> None:
        answers = {"https://bad.example": "10.0.0.1", "https://good.example": "8.8.8.8"}
        with patch.object(public_target, "urlopen", self._urlopen(answers)):
            with self.assertLogs("app.public_target", level="WARNING"):
                self.assertEqual(
                    public_target.detect_server_public_ip(
                        sources=["https://bad.example", "https://good.example"],
                        timeout_seconds=0.1,
                    ),
                    "8.8.8.8",
                )

    def test_a_body_that_is_not_an_address_at_all_is_still_survivable(self) -> None:
        answers = {"https://html.example": "<html>nope", "https://good.example": "1.1.1.1"}
        with patch.object(public_target, "urlopen", self._urlopen(answers)):
            self.assertEqual(
                public_target.detect_server_public_ip(
                    sources=["https://html.example", "https://good.example"],
                    timeout_seconds=0.1,
                ),
                "1.1.1.1",
            )


class TheExportPassphraseMeetsTheSameFloorAsThePasswordTests(_ScopedClient):
    """The backup file leaves the instance, so its passphrase is attacked offline."""

    GOOD = "correct horse battery staple"

    def test_eight_characters_no_longer_buy_an_encrypted_export(self) -> None:
        resp = self.client.post(
            "/api/backup/secure", json={"passphrase": "short123"}, headers=self._headers("adm")
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        detail = resp.json().get("detail", "")
        self.assertIn("Passphrase", detail)
        self.assertIn(str(security.MIN_PASSWORD_LENGTH), detail)
        self.assertNotIn("Password", detail)

    def test_twelve_of_the_same_character_is_not_a_passphrase(self) -> None:
        resp = self.client.post(
            "/api/backup/secure",
            json={"passphrase": "a" * security.MIN_PASSWORD_LENGTH},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("different characters", resp.json().get("detail", ""))

    def test_a_real_passphrase_exports(self) -> None:
        resp = self.client.post(
            "/api/backup/secure", json={"passphrase": self.GOOD}, headers=self._headers("adm")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(json.loads(resp.content)["secrets_included"])

    def test_the_floor_is_the_one_the_login_password_already_had(self) -> None:
        ok, _ = security.validate_password_strength("short123")
        self.assertFalse(ok)
        ok, _ = security.validate_password_strength(self.GOOD)
        self.assertTrue(ok)

    def test_a_file_written_under_the_old_rule_still_restores(self) -> None:
        """Only the export is gated. Refusing an old backup would turn a hardening change
        into data loss for whoever needs it most: the operator restoring after an incident."""
        salt = os.urandom(16)
        old_passphrase = "short123"
        backup = {
            "version": "8",
            "secrets_included": True,
            "encryption_salt": base64.urlsafe_b64encode(salt).decode(),
            "encrypted_fields": ["providers.password"],
            "providers": [
                {
                    "id": 1,
                    "name": "NPM",
                    "type": "npm",
                    "url": "http://npm:81",
                    "username": "admin",
                    "password": encrypt_for_backup("provider-secret", old_passphrase, salt),
                    "extra": "{}",
                    "enabled": 1,
                }
            ],
        }
        resp = self.client.post(
            "/api/restore",
            json={"backup": backup, "passphrase": old_passphrase},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_a_restore_does_bring_back_the_wan_resolvers(self) -> None:
        """What the comment on that loop used to deny. It comes through, on purpose: a
        restore has to return the configuration it saved, and the route is admin-only."""
        backup = {
            "version": "8",
            "secrets_included": False,
            "settings": [{"key": "public_target_sources", "value": "https://ip.example"}],
        }
        resp = self.client.post(
            "/api/restore", json={"backup": backup}, headers=self._headers("adm")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._setting("public_target_sources"), "https://ip.example")


class TheDocumentedWayBackInIsTheOneThatWorksTests(unittest.TestCase):
    """`README.md` told operators to delete the hash. That is now the locked state."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "recovery.test.db")
        models.init_db()
        self._password_patch = patch.object(auth, "APP_PASSWORD", "")
        self._password_patch.start()

        conn = models.get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
                (auth.hash_password("a passphrase that is long"),),
            )
            auth.mark_password_configured(conn)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._password_patch.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _delete(self, *keys: str) -> None:
        conn = models.get_db()
        try:
            placeholders = ",".join("?" * len(keys))
            conn.execute(f"DELETE FROM settings WHERE key IN ({placeholders})", keys)  # noqa: S608
            conn.commit()
        finally:
            conn.close()

    def test_deleting_only_the_hash_locks_the_instance_rather_than_opening_it(self) -> None:
        self._delete("app_password_hash")
        self.assertTrue(auth.auth_is_downgraded())

    def test_deleting_both_rows_is_what_hands_the_instance_back(self) -> None:
        self._delete("app_password_hash", "auth_mode")
        self.assertFalse(auth.auth_is_downgraded())
        self.assertFalse(auth.has_password_configured())

    def test_the_readme_recovery_snippet_names_both_rows(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        snippet = re.search(r"DELETE FROM settings WHERE key[^\n]*", readme)
        self.assertIsNotNone(snippet, "the recovery snippet disappeared from README.md")
        self.assertIn("app_password_hash", snippet.group(0))
        self.assertIn("auth_mode", snippet.group(0))


class TheInterfaceAsksForWhatTheServerWillAcceptTests(unittest.TestCase):
    """One floor, written once on each side, and read from there in eight languages."""

    NUMBERED_KEYS = (
        "settings.backup.passphrase_placeholder",
        "settings.backup.passphrase_min",
        "settings.auth.new_password",
        "settings.auth.new_min",
    )

    def _read(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")

    def test_no_field_still_advertises_the_old_floor(self) -> None:
        source = self._read("frontend/src/pages/Settings.tsx")
        self.assertNotIn("minLength={8}", source)
        self.assertIn("minLength={MIN_PASSWORD_LENGTH}", source)

    def test_both_sides_agree_on_the_number(self) -> None:
        found = re.search(
            r"MIN_PASSWORD_LENGTH\s*=\s*(\d+)", self._read("frontend/src/constants.ts")
        )
        self.assertIsNotNone(found)
        self.assertEqual(int(found.group(1)), security.MIN_PASSWORD_LENGTH)

    def test_no_locale_carries_a_length_of_its_own(self) -> None:
        locales = sorted((REPO_ROOT / "frontend/src/locales").glob("*.json"))
        self.assertEqual(len(locales), 8, [p.name for p in locales])
        for path in locales:
            messages = json.loads(path.read_text(encoding="utf-8"))
            for key in self.NUMBERED_KEYS:
                with self.subTest(locale=path.name, key=key):
                    value = messages.get(key)
                    self.assertIsNotNone(value, f"{path.name} is missing {key}")
                    self.assertIn("{min}", value)
                    self.assertIsNone(
                        re.search(r"\d", value),
                        f"{path.name}:{key} still writes a figure of its own: {value}",
                    )

    def test_every_call_site_passes_the_substitution(self) -> None:
        """A `{min}` nobody fills in reads as a literal brace on the operator's screen."""
        source = self._read("frontend/src/pages/Settings.tsx")
        for key in self.NUMBERED_KEYS:
            with self.subTest(key=key):
                for call in re.findall(rf"t\('{re.escape(key)}'[^)]*\)", source):
                    self.assertIn("min: MIN_PASSWORD_LENGTH", call)

    def test_the_proxy_variable_is_documented_where_operators_look(self) -> None:
        for rel in (".env.example", "README.md", "docs/DEPLOYMENT.md"):
            with self.subTest(document=rel):
                self.assertIn("FORWARDED_ALLOW_IPS", self._read(rel))


if __name__ == "__main__":
    unittest.main(verbosity=2)
