"""Features that could be configured, tested, and would still never run.

Every case here shared one shape: a switch with nothing behind it, and no way for the
operator to find out.

- `auto_reconcile` imported `_execute_push` from `app.api.sync`, a name that had never been
  defined. The job would have raised `ImportError` on its first tick -- except it could not
  be scheduled either, because `auto_reconcile_enabled` was not a writable setting.
- `webhook_retry_retention_days` was documented in the changelog as configurable and
  dropped by `save_settings` on the way in.
- `settings.webhook_url` was writable, masked on the way out, and had its own test-send
  route that really delivered. No alert ever went through it: delivery reads `webhooks.url`.
- the admin password policy existed twice -- a `validate_password_strength` nothing called,
  and an inline `len < 8` at each endpoint. The one that ran was the weaker one.
- `POST /api/reset` left five tables standing, including a Docker host with its credentials
  and a queue of notifications the retry job would keep trying to send.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models, scheduler, security
from app.api import auth as auth_api
from app.api import settings as settings_api
from app.api import sync as sync_api
from app.limiter import limiter as _app_limiter


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "dead-ends.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        # slowapi keeps its counters process-wide, so 3/minute would leak between tests.
        # The wrapper reads `request.state.view_rate_limit` afterwards, hence the assignment.
        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(_app_limiter, "_check_request_limit", _no_rate_limit)
        self._limiter_patch.start()

    def tearDown(self) -> None:
        import app.db as _app_db

        self._limiter_patch.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _setting(self, key: str):
        conn = models.get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else None

    def _save(self, body: dict):
        with patch.object(settings_api, "require_auth", lambda _r, scope=None: None):
            return settings_api.save_settings(_request("POST", "/api/settings"), body)


class AutoReconcileCanActuallyBeTurnedOnTests(_IsolatedDB):
    """The switch, and the thing behind the switch. Neither worked alone."""

    def test_the_scheduler_can_import_what_it_imports(self) -> None:
        """`run_auto_reconcile` does `from app.api.sync import _execute_push` at call time.

        The name did not exist, so the very first tick would have died on an ImportError --
        in a background job, where nobody reads the traceback.
        """
        self.assertTrue(hasattr(sync_api, "_execute_push"))

    def test_a_push_runs_without_an_http_request(self) -> None:
        """That is the whole point: the scheduler has no `Request` to authenticate."""
        self._exec(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port,
                                     expose_mode, enabled)
               VALUES (1, 'app', 'example.com', '10.0.0.9', 8080, 'proxy_dns', 1)"""
        )
        conn = models.get_db()
        svc = conn.execute("SELECT * FROM services WHERE id=1").fetchone()
        conn.close()

        result = sync_api._execute_push(svc, 1)

        # No provider is attached, so the push has nothing to do and says so. What matters
        # is that it returned a report instead of raising.
        self.assertIn("ok", result)
        self.assertIn("errors", result)

    def test_the_connection_is_closed_even_when_the_push_raises(self) -> None:
        self._exec(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port,
                                     expose_mode, enabled)
               VALUES (1, 'app', 'example.com', '10.0.0.9', 8080, 'proxy_dns', 1)"""
        )
        conn = models.get_db()
        svc = conn.execute("SELECT * FROM services WHERE id=1").fetchone()
        conn.close()

        with patch.object(sync_api, "_push_service_row", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                sync_api._execute_push(svc, 1)

        # A leaked write connection would hold the SQLite lock and hang the next writer.
        self._exec("UPDATE services SET status='ok' WHERE id=1")

    def test_the_three_orphan_settings_are_writable(self) -> None:
        with patch.object(scheduler, "configure_reconcile", lambda *a: None):
            result = self._save({
                "auto_reconcile_enabled": "true",
                "auto_reconcile_interval": "30",
                "webhook_retry_retention_days": "14",
            })
        self.assertEqual(result["ignored"], [])
        self.assertEqual(self._setting("auto_reconcile_enabled"), "true")
        self.assertEqual(self._setting("auto_reconcile_interval"), "30")
        self.assertEqual(self._setting("webhook_retry_retention_days"), "14")

    def test_their_bounds_are_checked_like_every_other_number(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._save({"auto_reconcile_interval": "99999"})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsNone(self._setting("auto_reconcile_interval"))

        with self.assertRaises(HTTPException):
            self._save({"webhook_retry_retention_days": "0"})
        self.assertIsNone(self._setting("webhook_retry_retention_days"))

        with self.assertRaises(HTTPException):
            self._save({"auto_reconcile_enabled": "sometimes"})
        self.assertIsNone(self._setting("auto_reconcile_enabled"))

    def test_turning_it_on_applies_now_and_not_at_the_next_restart(self) -> None:
        """`check_interval` has always been applied live; this one is no different."""
        seen = []
        with patch.object(scheduler, "configure_reconcile",
                          lambda enabled, minutes: seen.append((enabled, minutes))):
            self._save({"auto_reconcile_enabled": "true", "auto_reconcile_interval": "45"})
        self.assertEqual(seen, [(True, 45)])

    def test_moving_one_key_reads_the_other_back_instead_of_assuming_it(self) -> None:
        seen = []
        with patch.object(scheduler, "configure_reconcile",
                          lambda enabled, minutes: seen.append((enabled, minutes))):
            self._save({"auto_reconcile_enabled": "true", "auto_reconcile_interval": "45"})
            seen.clear()
            self._save({"auto_reconcile_interval": "10"})
        self.assertEqual(seen, [(True, 10)])


class OnePasswordPolicyTests(_IsolatedDB):
    """There were two, and the one that ran was the one nobody had thought about."""

    def _setup(self, password: str):
        return auth_api.setup_password(
            _request("POST", "/api/auth/setup-password"),
            auth_api.SetPasswordBody(password=password),
        )

    def test_the_endpoint_applies_the_shared_rule(self) -> None:
        """`setup_password` used to carry its own `len < 8`, eleven characters below this."""
        with self.assertRaises(HTTPException) as ctx:
            self._setup("elevenchars")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("12", str(ctx.exception.detail))

    def test_a_passphrase_is_accepted_without_a_single_symbol(self) -> None:
        """The old dead rule demanded four character classes; that is what it cost."""
        result = self._setup("correct horse battery staple")
        self.assertTrue(result["ok"])

    def test_changing_the_password_applies_the_same_rule(self) -> None:
        self._setup("correct horse battery staple")
        with patch.object(auth_api, "require_auth", lambda _r, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                auth_api.change_password(
                    _request("POST", "/api/auth/change-password"),
                    auth_api.ChangePasswordBody(
                        current_password="correct horse battery staple",
                        new_password="elevenchars",
                    ),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_length_alone_does_not_buy_a_repeated_character(self) -> None:
        ok, why = security.validate_password_strength("abababababababab")
        self.assertFalse(ok)
        self.assertIn("different characters", why)


class TheGlobalNotificationUrlDeliversTests(_IsolatedDB):
    """It could be set, it could be tested, and it reached nobody when it mattered."""

    URL = "discord://token@channel"

    def test_the_legacy_setting_moves_into_the_webhooks_table(self) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES ('webhook_url', ?)", (self.URL,))
        self._exec("INSERT INTO settings (key, value) VALUES ('webhook_enabled', 'true')")

        conn = models.get_db()
        models._migrate_legacy_webhook_url(conn)
        conn.commit()
        row = conn.execute("SELECT * FROM webhooks WHERE url=?", (self.URL,)).fetchone()
        conn.close()

        self.assertIsNotNone(row, "the configured URL must end up where delivery reads")
        self.assertEqual(row["enabled"], 1)
        self.assertEqual(row["scope_type"], "all")
        # A global URL meant "tell me when anything breaks"; a row with no rule is silent.
        self.assertEqual(row["alert_on_any_down"], 1)
        self.assertEqual(row["alert_on_any_up"], 1)
        self.assertIsNone(self._setting("webhook_url"))
        self.assertIsNone(self._setting("webhook_enabled"))

    def test_a_disabled_legacy_url_stays_disabled(self) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES ('webhook_url', ?)", (self.URL,))
        self._exec("INSERT INTO settings (key, value) VALUES ('webhook_enabled', 'false')")

        conn = models.get_db()
        models._migrate_legacy_webhook_url(conn)
        conn.commit()
        row = conn.execute("SELECT enabled FROM webhooks WHERE url=?", (self.URL,)).fetchone()
        conn.close()
        self.assertEqual(row["enabled"], 0)

    def test_running_twice_does_not_duplicate_the_target(self) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES ('webhook_url', ?)", (self.URL,))
        for _ in range(2):
            conn = models.get_db()
            models._migrate_legacy_webhook_url(conn)
            conn.commit()
            conn.close()
        conn = models.get_db()
        count = conn.execute("SELECT COUNT(*) AS n FROM webhooks WHERE url=?", (self.URL,)).fetchone()
        conn.close()
        self.assertEqual(count["n"], 1)

    def test_the_setting_can_no_longer_be_written(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._save({"webhook_url": self.URL})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("/api/webhooks", str(ctx.exception.detail))
        self.assertIsNone(self._setting("webhook_url"))

    def test_the_enabled_flag_is_retired_with_it(self) -> None:
        """It used to be caught by the boolean branch above and stored happily.

        A key that switches on a delivery path that does not exist is the same trap as the
        URL itself, one indirection further away.
        """
        with self.assertRaises(HTTPException) as ctx:
            self._save({"webhook_enabled": "true"})
        self.assertIn("/api/webhooks", str(ctx.exception.detail))
        self.assertIsNone(self._setting("webhook_enabled"))

    def test_the_masked_form_is_still_named_for_what_it_is(self) -> None:
        """The more specific message survives: it tells an agent what it did wrong."""
        with self.assertRaises(HTTPException) as ctx:
            self._save({"webhook_url": "discord://***"})
        self.assertIn("masked", str(ctx.exception.detail))

    def test_the_test_route_refuses_when_nothing_is_configured(self) -> None:
        with patch.object(settings_api, "require_auth", lambda _r, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                settings_api.test_webhook(_request("POST", "/api/settings/test-webhook"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("/api/webhooks", str(ctx.exception.detail))

    def test_the_test_route_reports_each_target_separately(self) -> None:
        """A single ok/failed hid which of several targets was broken."""
        self._exec(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'Good', ?, 1)", (self.URL,)
        )
        self._exec(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (2, 'Bad', 'nope://x', 1)"
        )
        self._exec(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (3, 'Off', 'mailto://o@e.com', 0)"
        )

        class _Apprise:
            def __init__(self):
                self._url = ""

            def add(self, url):
                self._url = url
                return not url.startswith("nope://")

            def notify(self, **_kw):
                return True

        with patch.object(settings_api, "require_auth", lambda _r, scope=None: None), \
             patch.dict("sys.modules", {"apprise": type("M", (), {"Apprise": _Apprise})}):
            result = settings_api.test_webhook(_request("POST", "/api/settings/test-webhook"))

        self.assertFalse(result["ok"])
        self.assertEqual([r["id"] for r in result["results"]], [1, 2], "disabled rows are skipped")
        self.assertTrue(result["results"][0]["ok"])
        self.assertFalse(result["results"][1]["ok"])
        self.assertTrue(result["results"][1]["error"])


class ResetMeansResetTests(_IsolatedDB):
    """Five tables used to survive the button that says it empties the instance."""

    def test_the_tables_that_used_to_survive_do_not(self) -> None:
        self._exec(
            "INSERT INTO docker_endpoints (name, docker_host) "
            "VALUES ('remote', 'tcp://10.0.0.9:2376')"
        )
        self._exec(
            "INSERT INTO service_templates (id, name) VALUES (1, 'web app')"
        )
        # `webhook_id` stays NULL on purpose. Schema 11 gave the column a cascade from
        # `webhooks`, and a row with a parent would be swept away by the `DELETE FROM
        # webhooks` further down `reset_all` even if its own DELETE were removed. An ad-hoc
        # send has no parent, so only the explicit wipe can clear it -- which is the line
        # this test exists to hold.
        self._exec(
            "INSERT INTO webhook_delivery_log (id, webhook_id, url, title, body, status) "
            "VALUES (1, NULL, 'discord://x', 't', 'b', 'pending')"
        )
        self._exec(
            "INSERT INTO scheduler_state (key, value) VALUES ('provider_last_status', '{}')"
        )

        with patch.object(settings_api, "require_auth", lambda _r, scope=None: None):
            self.assertTrue(settings_api.reset_all(_request("POST", "/api/reset"))["ok"])

        conn = models.get_db()
        for table in ("service_templates", "webhook_delivery_log", "scheduler_state"):
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
            self.assertEqual(n, 0, f"{table} survived the reset")

        # The added endpoint and its host are gone; the seeded local one is put back, so the
        # instance is not left with no Docker host until someone restarts it.
        rows = conn.execute("SELECT name, is_default FROM docker_endpoints").fetchall()
        conn.close()
        self.assertEqual([r["name"] for r in rows], ["Local Docker"])
        self.assertEqual(rows[0]["is_default"], 1)


class ColumnsAndAliasesThatPointedNowhereTests(_IsolatedDB):
    def test_the_legacy_environment_column_is_gone(self) -> None:
        """Superseded by `service_environments`; read by no Python, no TypeScript, no backup."""
        conn = models.get_db()
        columns = {r[1] for r in conn.execute("PRAGMA table_info(services)").fetchall()}
        conn.close()
        self.assertNotIn("environment", columns)

    def test_dropping_it_leaves_a_usable_services_table(self) -> None:
        self._exec(
            """INSERT INTO services (subdomain, domain, target_ip, target_port, enabled)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 1)"""
        )
        conn = models.get_db()
        row = conn.execute("SELECT * FROM services").fetchone()
        conn.close()
        self.assertEqual(row["subdomain"], "app")

    def test_the_scheduler_no_longer_keeps_a_frozen_alias(self) -> None:
        """`_tunnel_last_status = _provider_last_status` was taken at import.

        `_load_state` rebinds `_provider_last_status` at startup, so the alias kept pointing
        at the dict from before the state was loaded. Anything clearing it -- the test suite
        did exactly that -- was clearing something nobody read.
        """
        from app import scheduler

        self.assertFalse(hasattr(scheduler, "_tunnel_last_status"))

    def test_the_request_cache_module_is_gone(self) -> None:
        """A per-request allocation, on every request, for a cache with no callers."""
        with self.assertRaises(ImportError):
            import app.cache  # noqa: F401


class TheDatabaseStillHoldsItselfTogetherTests(_IsolatedDB):
    def test_a_second_service_on_one_hostname_is_still_refused(self) -> None:
        """Dropping a column rebuilds the table in SQLite; the Lot C index must survive."""
        self._exec(
            """INSERT INTO services (subdomain, domain, target_ip, target_port, enabled)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 1)"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self._exec(
                """INSERT INTO services (subdomain, domain, target_ip, target_port, enabled)
                   VALUES ('app', 'example.com', '10.0.0.10', 9090, 1)"""
            )


if __name__ == "__main__":
    unittest.main()
