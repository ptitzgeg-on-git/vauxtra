"""An Apprise URL is the credential -- these tests hold every door it used to walk out of.

`discord://<id>/<token>`, `tgram://<bot token>/<chat id>`, `slack://<tokens>/<channel>`:
there is no separate password field to clear, so the URL either leaves in full or it does
not leave at all. Four doors were open at once -- the webhook list, the service-alerts
join, the settings map, and the log table -- plus the export file whose own flag said it
carried no secrets. Each one has a test here, because closing three of four leaks the
same token.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

import app.auth as auth
from app import models, scheduler
from app.api import backup as backup_api
from app.api import settings as settings_api
from app.api import webhooks as webhooks_api
from app.api.backup import RestoreRequest, SecureBackupRequest
from app.limiter import limiter as _app_limiter
from app.security import mask_secret_url

SECRET_URL = "discord://123456789/abcdefghijklmnopqrstuvwxyz-TOKEN"
SECRET_TOKEN = "abcdefghijklmnopqrstuvwxyz-TOKEN"


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _IsolatedDB(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)

        db_path = os.path.join(self._tmpdir.name, "vauxtra.secrets.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        self._auth_patch = patch.object(auth, "APP_PASSWORD", "")
        self._auth_patch.start()

        def _no_rate_limit(request, *_args, **_kwargs):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(_app_limiter, "_check_request_limit", _no_rate_limit)
        self._limiter_patch.start()

    def tearDown(self) -> None:
        self._limiter_patch.stop()
        self._auth_patch.stop()
        import app.db as _app_db
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _insert_webhook(self, name: str = "Discord", url: str = SECRET_URL) -> int:
        conn = models.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO webhooks (name, url, enabled) VALUES (?,?,1)", (name, url)
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


class MaskSecretUrlTests(unittest.TestCase):
    """The masking itself: what survives has to be useless to an attacker and useful to an
    operator. The scheme is the useful half -- it says *which* webhook a line is about."""

    def test_token_never_survives(self) -> None:
        for url in (
            SECRET_URL,
            "tgram://1234567:AAH-bot-token/-1001234567890",
            "slack://T00/B00/xoxb-secret-token/general",
            "mailto://user:hunter2@smtp.example.com",
        ):
            with self.subTest(url=url):
                masked = mask_secret_url(url)
                self.assertNotIn("TOKEN", masked)
                self.assertNotIn("AAH-bot-token", masked)
                self.assertNotIn("xoxb-secret-token", masked)
                self.assertNotIn("hunter2", masked)

    def test_scheme_survives(self) -> None:
        self.assertTrue(mask_secret_url(SECRET_URL).startswith("discord://"))

    def test_host_kept_only_when_an_at_sign_proves_it_is_one(self) -> None:
        # `user:pass@host` -- the host is an address, and the operator needs it to tell two
        # self-hosted targets apart.
        self.assertEqual(
            mask_secret_url("ntfy://user:pass@ntfy.home.lan/topic"), "ntfy://***@ntfy.home.lan"
        )
        # No `@`: the authority is opaque and is itself half the secret.
        self.assertEqual(mask_secret_url(SECRET_URL), "discord://***")

    def test_empty_and_malformed(self) -> None:
        self.assertEqual(mask_secret_url(""), "")
        self.assertEqual(mask_secret_url(None), "")
        self.assertEqual(mask_secret_url("not-a-url"), "***")


class WebhookReadPathTests(_IsolatedDB):
    """`require_auth(request)` with no `scope=` means every GET is `read`, and `read` is what
    an API key gets by default. These are the widest doors onto the token."""

    def test_list_never_returns_the_url(self) -> None:
        self._insert_webhook()
        with patch.object(webhooks_api, "require_auth", lambda _r, scope=None: None):
            rows = webhooks_api.list_webhooks(_request("GET", "/api/webhooks"))

        self.assertEqual(len(rows), 1)
        # `url` is dropped, not masked: a client that reads a webhook and writes it back
        # would otherwise store `discord://***` as the real URL.
        self.assertNotIn("url", rows[0])
        self.assertEqual(rows[0]["url_masked"], "discord://***")
        self.assertNotIn(SECRET_TOKEN, json.dumps(rows))

    def test_service_alerts_join_never_returns_the_url(self) -> None:
        wid = self._insert_webhook()
        conn = models.get_db()
        conn.execute(
            "INSERT INTO services (subdomain, domain, target_ip, target_port, forward_scheme, "
            "enabled, status) VALUES ('app','example.com','127.0.0.1',8080,'http',1,'unknown')"
        )
        sid = conn.execute("SELECT id FROM services").fetchone()["id"]
        conn.execute(
            "INSERT INTO service_alerts (service_id, webhook_id, on_up, on_down) VALUES (?,?,1,1)",
            (sid, wid),
        )
        conn.commit()
        conn.close()

        with patch.object(webhooks_api, "require_auth", lambda _r, scope=None: None):
            rows = webhooks_api.get_service_alerts(sid, _request("GET", f"/api/services/{sid}/alerts"))

        self.assertEqual(len(rows), 1)
        self.assertNotIn("webhook_url", rows[0])
        self.assertEqual(rows[0]["webhook_url_masked"], "discord://***")
        self.assertNotIn(SECRET_TOKEN, json.dumps(rows))

    def test_write_responses_echo_a_masked_url(self) -> None:
        with patch.object(webhooks_api, "require_auth", lambda _r, scope=None: None):
            created = webhooks_api.add_webhook(
                _request("POST", "/api/webhooks"), {"name": "D", "url": SECRET_URL}
            )
            # A partial update -- the enable/disable toggle sends only `enabled` -- must not
            # echo back a URL the caller never sent.
            updated = webhooks_api.update_webhook(
                created["id"], _request("PUT", "/api/webhooks/1"), {"enabled": 0}
            )

        for body in (created, updated):
            self.assertNotIn("url", body)
            self.assertEqual(body["url_masked"], "discord://***")

    def test_the_mask_is_refused_as_a_url(self) -> None:
        """Masking creates exactly one new way to break a webhook: writing the mask back.

        A caller that reads a webhook and writes it back -- an agent through the MCP bridge,
        a script -- would store `discord://***` as the real URL and silently kill the
        alerting."""
        from fastapi import HTTPException

        wid = self._insert_webhook()
        with patch.object(webhooks_api, "require_auth", lambda _r, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                webhooks_api.update_webhook(
                    wid, _request("PUT", f"/api/webhooks/{wid}"), {"url": "discord://***"}
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("masked", ctx.exception.detail)

        conn = models.get_db()
        row = conn.execute("SELECT url FROM webhooks WHERE id=?", (wid,)).fetchone()
        conn.close()
        self.assertEqual(row["url"], SECRET_URL)

    def test_a_partial_update_keeps_the_stored_url(self) -> None:
        """Which is what makes the mask safe to show: you never have to send it back."""
        wid = self._insert_webhook()
        with patch.object(webhooks_api, "require_auth", lambda _r, scope=None: None):
            webhooks_api.update_webhook(
                wid, _request("PUT", f"/api/webhooks/{wid}"), {"enabled": 0}
            )

        conn = models.get_db()
        row = conn.execute("SELECT url, enabled FROM webhooks WHERE id=?", (wid,)).fetchone()
        conn.close()
        self.assertEqual(row["url"], SECRET_URL)
        self.assertEqual(row["enabled"], 0)

    def test_settings_masks_the_legacy_global_url(self) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('webhook_url', ?)", (SECRET_URL,)
        )
        conn.commit()
        conn.close()

        with patch.object(settings_api, "require_auth", lambda _r, scope=None: None):
            data = settings_api.get_settings(_request("GET", "/api/settings"))

        # The key stays -- the operator needs to know one is set -- with the token gone.
        self.assertEqual(data["webhook_url"], "discord://***")
        self.assertNotIn(SECRET_TOKEN, json.dumps(data))


class WebhookLogTests(_IsolatedDB):
    """`add_log` writes into the `logs` table, which `GET /api/logs` and its SSE stream hand
    to any key. A transient Discord outage used to persist the token there."""

    def _logs(self) -> str:
        conn = models.get_db()
        try:
            return "\n".join(r["message"] for r in conn.execute("SELECT message FROM logs"))
        finally:
            conn.close()

    def test_unusable_url_is_logged_masked(self) -> None:
        # A scheme apprise does not know: `a.add()` returns False, which is the first of the
        # four log lines that used to interpolate the raw URL.
        scheduler._try_send_apprise("nosuchscheme://" + SECRET_TOKEN, "t", "b")
        logged = self._logs()
        self.assertIn("[Webhook]", logged)
        self.assertNotIn(SECRET_TOKEN, logged)

    def test_migration_purges_urls_logged_before_the_masking_existed(self) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO logs (level, message) VALUES ('error', ?)",
            (f"[Webhook] Delivery failed for {SECRET_URL}: timeout",),
        )
        conn.execute(
            "INSERT INTO logs (level, message) VALUES ('info', 'Service app.example.com is up')"
        )
        conn.execute("DELETE FROM settings WHERE key='webhook_log_purge_done'")
        conn.commit()
        conn.close()

        conn = models.get_db()
        models._purge_logged_webhook_urls(conn)
        conn.commit()
        conn.close()

        logged = self._logs()
        self.assertNotIn(SECRET_TOKEN, logged)
        # Ordinary history survives: only `[Webhook]` lines carrying a `://` are touched.
        self.assertIn("Service app.example.com is up", logged)

    def test_purge_runs_once(self) -> None:
        conn = models.get_db()
        models._purge_logged_webhook_urls(conn)
        conn.commit()
        conn.execute(
            "INSERT INTO logs (level, message) VALUES ('error', ?)",
            (f"[Webhook] Delivery failed for {SECRET_URL}: timeout",),
        )
        conn.commit()
        # Marked done at boot, so a line written afterwards -- which the masking makes
        # impossible anyway -- is not silently deleted on every restart.
        models._purge_logged_webhook_urls(conn)
        conn.commit()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE message LIKE '%[Webhook]%'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 1)


class BackupSecretTests(_IsolatedDB):
    """One file says `secrets_included: false`, the other says "encrypted credentials".
    Both shipped the Apprise URL in clear."""

    def _no_auth(self):
        return patch.object(backup_api, "require_auth", lambda _r, scope=None: None)

    def test_plain_export_carries_no_url(self) -> None:
        self._insert_webhook()
        conn = models.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('webhook_url', ?)", (SECRET_URL,)
        )
        conn.commit()
        conn.close()

        with self._no_auth():
            body = json.loads(backup_api.export_backup(_request("GET", "/api/backup")).body)

        self.assertFalse(body["secrets_included"])
        self.assertEqual(body["webhooks"][0]["url"], "")
        self.assertEqual(body["webhooks"][0]["url_masked"], "discord://***")
        self.assertNotIn("webhook_url", {s["key"] for s in body["settings"]})
        self.assertNotIn(SECRET_TOKEN, json.dumps(body))

    def test_plain_restore_brings_the_webhook_back_disabled_and_says_so(self) -> None:
        self._insert_webhook()
        with self._no_auth():
            body = json.loads(backup_api.export_backup(_request("GET", "/api/backup")).body)
            result = backup_api.import_backup(
                _request("POST", "/api/restore"), RestoreRequest(backup=body)
            )

        self.assertEqual(result["webhooks_needing_url"], 1)
        conn = models.get_db()
        row = conn.execute("SELECT name, url, enabled FROM webhooks").fetchone()
        conn.close()
        # The name, the scope and the rules survive; only the one field that cannot be
        # restored is missing, and the row is off rather than silently broken.
        self.assertEqual(row["name"], "Discord")
        self.assertEqual(row["url"], "")
        self.assertEqual(row["enabled"], 0)

    def test_secure_export_encrypts_the_url(self) -> None:
        self._insert_webhook()
        conn = models.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('webhook_url', ?)", (SECRET_URL,)
        )
        conn.commit()
        conn.close()

        with self._no_auth():
            body = json.loads(
                backup_api.export_backup_secure(
                    _request("POST", "/api/backup/secure"),
                    SecureBackupRequest(passphrase="MyBackupPass1"),
                ).body
            )

        self.assertTrue(body["secrets_included"])
        self.assertIn("webhooks.url", body["encrypted_fields"])
        self.assertNotIn(SECRET_TOKEN, json.dumps(body))

    def test_secure_round_trip_restores_a_working_url(self) -> None:
        self._insert_webhook()
        with self._no_auth():
            body = json.loads(
                backup_api.export_backup_secure(
                    _request("POST", "/api/backup/secure"),
                    SecureBackupRequest(passphrase="MyBackupPass1"),
                ).body
            )

        conn = models.get_db()
        conn.execute("DELETE FROM webhooks")
        conn.commit()
        conn.close()

        with self._no_auth():
            result = backup_api.import_backup(
                _request("POST", "/api/restore"),
                RestoreRequest(backup=body, passphrase="MyBackupPass1"),
            )

        self.assertEqual(result["webhooks_needing_url"], 0)
        conn = models.get_db()
        row = conn.execute("SELECT url, enabled FROM webhooks").fetchone()
        conn.close()
        # Stored in clear, like it always was: `_try_send_apprise` hands the URL to apprise
        # as-is. What changed is that it no longer crosses the wire or the disk in clear.
        self.assertEqual(row["url"], SECRET_URL)
        self.assertEqual(row["enabled"], 1)

    def test_wrong_passphrase_fails_before_anything_is_wiped(self) -> None:
        """A file with no providers used to reach the EXCLUSIVE transaction unvalidated."""
        self._insert_webhook()
        with self._no_auth():
            body = json.loads(
                backup_api.export_backup_secure(
                    _request("POST", "/api/backup/secure"),
                    SecureBackupRequest(passphrase="MyBackupPass1"),
                ).body
            )
        body["providers"] = []

        from fastapi import HTTPException

        with self._no_auth(), self.assertRaises(HTTPException) as ctx:
            backup_api.import_backup(
                _request("POST", "/api/restore"),
                RestoreRequest(backup=body, passphrase="WrongPassphrase9"),
            )
        self.assertEqual(ctx.exception.status_code, 400)

        conn = models.get_db()
        row = conn.execute("SELECT url FROM webhooks").fetchone()
        conn.close()
        self.assertEqual(row["url"], SECRET_URL)

    def test_a_version_7_backup_still_restores(self) -> None:
        """Before version 8 only the provider passwords were encrypted. Such a file has no
        `encrypted_fields`, and its webhook URLs must be taken as-is rather than run through
        a decryption that would fail on plain text."""
        legacy = {
            "version": "7",
            "secrets_included": True,
            "encryption_salt": "AAAAAAAAAAAAAAAAAAAAAA==",
            "providers": [],
            "webhooks": [{"id": 1, "name": "Legacy", "url": SECRET_URL, "enabled": 1}],
            "settings": [],
        }
        with self._no_auth():
            backup_api.import_backup(
                _request("POST", "/api/restore"),
                RestoreRequest(backup=legacy, passphrase="AnyPassphrase1"),
            )

        conn = models.get_db()
        row = conn.execute("SELECT url, enabled FROM webhooks").fetchone()
        conn.close()
        self.assertEqual(row["url"], SECRET_URL)
        self.assertEqual(row["enabled"], 1)


if __name__ == "__main__":
    unittest.main()
