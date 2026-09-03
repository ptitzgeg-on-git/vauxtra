"""An instance that once had a password must never silently answer anonymously again.

`_get_auth_context` used to read: no password configured -> `{"kind": "open", "scopes":
["admin"]}`. That is the documented behaviour for an install whose operator pressed *Skip*
on the wizard's password step, and it stays. What it must not cover is the other way into
the same state: a database restored from the wrong file, an `app_password_hash` row deleted
by hand, a partially recovered `vauxtra.db`. Those instances have data, they have providers,
their operator believes they are protected -- and every request was being handed the admin
scope, with nothing in any log or response saying so.

`auth_mode=password` is written the first time a password is set and never removed.
`setup_completed` cannot play that role: adding a provider writes it too, and a legitimate
passwordless install carries it as well.

The audit's own proposal was an `ALLOW_ANONYMOUS_ADMIN` opt-in, default off. It is not what
is implemented here: it would have returned 401 on every route of every legitimate
passwordless install the moment it was upgraded, with no in-app way back in -- the login
screen has no password to accept, and the wizard does not reopen once providers exist.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import app.auth as auth
from app import models
from app.api import auth as auth_api


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class AuthModeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "auth_mode.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        self._auth_patch = patch.object(auth, "APP_PASSWORD", "")
        self._auth_patch.start()
        self._api_auth_patch = patch.object(auth_api, "has_password_configured",
                                            auth.has_password_configured)
        self._api_auth_patch.start()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(_limiter, "_check_request_limit", _no_rate_limit)
        self._limiter_patch.start()

    def tearDown(self) -> None:
        self._limiter_patch.stop()
        self._api_auth_patch.stop()
        self._auth_patch.stop()
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # ---- helpers ----

    def _setting(self, key: str) -> str:
        conn = models.get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _set_password(self, password: str = "correct-horse") -> None:
        auth_api.setup_password(_request("POST", "/api/auth/setup-password"),
                                auth_api.SetPasswordBody(password=password))


class TheOpenInstallStillWorksTests(AuthModeTestCase):
    """The passwordless mode is a supported choice, not a bug. Nothing here may break it."""

    def test_a_fresh_install_is_open_admin(self):
        self.assertFalse(auth.has_password_configured())
        self.assertFalse(auth.password_was_configured_once())
        auth.require_auth(_request(), scope="admin")  # must not raise

    def test_it_stays_open_once_providers_exist(self):
        self._exec(
            "INSERT INTO providers (name, type, url, username, password, extra, enabled) "
            "VALUES ('NPM','npm','http://npm:81','admin','','{}',1)"
        )
        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_completed','1')")
        auth.require_auth(_request(), scope="admin")  # must not raise
        self.assertFalse(auth.auth_is_downgraded())

    def test_auth_me_names_the_mode(self):
        self.assertEqual(auth_api.auth_me(_request())["auth_mode"], "open")
        self._set_password()
        self.assertEqual(auth_api.auth_me(_request())["auth_mode"], "password")


class SettingAPasswordLeavesAMarkTests(AuthModeTestCase):
    def test_the_wizard_writes_the_marker(self):
        self.assertEqual(self._setting("auth_mode"), "")
        self._set_password()
        self.assertEqual(self._setting("auth_mode"), "password")

    def test_changing_the_password_writes_it_too(self):
        """An install created before this marker existed reaches it through a change too."""
        self._set_password("first-password")
        self._exec("DELETE FROM settings WHERE key='auth_mode'")
        with patch.object(auth_api, "require_auth", lambda _r, scope=None: None):
            auth_api.change_password(
                _request("POST", "/api/auth/change-password"),
                auth_api.ChangePasswordBody(current_password="first-password",
                                            new_password="second-password"),
            )
        self.assertEqual(self._setting("auth_mode"), "password")

    def test_the_backfill_stamps_an_instance_that_predates_the_marker(self):
        self._set_password()
        self._exec("DELETE FROM settings WHERE key='auth_mode'")
        models.init_db()  # the next boot
        self.assertEqual(self._setting("auth_mode"), "password")

    def test_the_backfill_leaves_a_passwordless_instance_alone(self):
        models.init_db()
        self.assertEqual(self._setting("auth_mode"), "")


class TheHashDisappearingIsRefusedTests(AuthModeTestCase):
    """The whole point: this state used to be indistinguishable from a fresh open install."""

    def _lose_the_hash(self) -> None:
        self._set_password()
        self._exec("DELETE FROM settings WHERE key='app_password_hash'")

    def test_it_is_reported_as_a_downgrade(self):
        self._lose_the_hash()
        self.assertFalse(auth.has_password_configured())
        self.assertTrue(auth.password_was_configured_once())
        self.assertTrue(auth.auth_is_downgraded())

    def test_no_request_gets_the_admin_scope(self):
        self._lose_the_hash()
        self.assertIsNone(auth._get_auth_context(_request()))
        self.assertFalse(auth.is_authenticated(_request()))
        with self.assertRaises(HTTPException) as ctx:
            auth.require_auth(_request(), scope="admin")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_the_message_says_which_failure_this_is(self):
        """A bare `Unauthorized` sends the operator hunting for a password that does not exist."""
        self._lose_the_hash()
        with self.assertRaises(HTTPException) as ctx:
            auth.require_auth(_request())
        self.assertIn("no longer in the database", ctx.exception.detail)
        self.assertIn("APP_PASSWORD", ctx.exception.detail)

    def test_the_setup_wizard_does_not_reopen(self):
        """Otherwise anyone could set a brand new admin password on a populated instance."""
        self._lose_the_hash()
        self.assertFalse(auth.is_setup_incomplete())
        with self.assertRaises(HTTPException) as ctx:
            auth.require_auth_or_setup(_request(), scope="admin")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_an_env_password_still_gets_in(self):
        """The documented recovery path from the error message has to actually work."""
        self._lose_the_hash()
        with patch.object(auth, "APP_PASSWORD", auth.hash_password("rescue-password-1")):
            self.assertTrue(auth.has_password_configured())
            self.assertFalse(auth.auth_is_downgraded())
            self.assertTrue(auth.check_password("rescue-password-1"))


class AnUnreadableDatabaseFailsClosedTests(AuthModeTestCase):
    """The previous code read the hash, got `""` on any error, and concluded "open install"."""

    def test_a_database_error_does_not_grant_admin(self):
        boom = sqlite3.OperationalError("database is locked")
        with patch.object(auth, "_read_auth_settings", side_effect=boom):
            self.assertFalse(auth.has_password_configured())
            self.assertTrue(auth.password_was_configured_once())
            self.assertIsNone(auth._get_auth_context(_request()))
            with self.assertRaises(HTTPException):
                auth.require_auth(_request())

    def test_it_does_not_reopen_the_setup_wizard_either(self):
        with patch.object(auth, "_read_auth_settings",
                          side_effect=sqlite3.OperationalError("disk I/O error")):
            self.assertFalse(auth.is_setup_incomplete())


class TheMarkerSurvivesResetAndRestoreTests(AuthModeTestCase):
    """`_PROTECTED_SETTINGS` grew a fourth key; both DELETEs built `(?,?,?)` by hand."""

    def test_the_placeholders_match_the_tuple(self):
        from app.api.settings import _PROTECTED_PLACEHOLDERS, _PROTECTED_SETTINGS

        self.assertEqual(_PROTECTED_PLACEHOLDERS.count("?"), len(_PROTECTED_SETTINGS))
        self.assertIn("auth_mode", _PROTECTED_SETTINGS)

    def test_a_reset_keeps_the_password_and_the_marker(self):
        from app.api import settings as settings_api

        self._set_password()
        with patch.object(settings_api, "require_auth", lambda _r, scope=None: None):
            settings_api.reset_all(_request("POST", "/api/reset"))
        self.assertEqual(self._setting("auth_mode"), "password")
        self.assertTrue(auth.has_password_configured())
        self.assertFalse(auth.auth_is_downgraded())

    def test_a_restore_cannot_smuggle_the_marker_in(self):
        """`auth_mode` is not in `_VALID_SETTINGS`, so an imported file cannot write it."""
        from app.api.settings import _VALID_SETTINGS

        self.assertNotIn("auth_mode", _VALID_SETTINGS)
        self.assertNotIn("app_password_hash", _VALID_SETTINGS)


class TheDocumentedRecoveryStillWorksTests(unittest.TestCase):
    """HOWTO told operators to delete `app_password_hash` to recover a forgotten password.

    That command alone now leaves the instance refusing every request, which is the whole
    point -- so the document had to change with the code. Lot 7 was exactly this failure in
    the other direction (the doc said plaintext `APP_PASSWORD` worked; the code refused it
    in silence), so the doc gets a test.
    """

    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.howto = (root / "docs" / "HOWTO.md").read_text(encoding="utf-8")

    def test_the_recovery_command_removes_the_marker_too(self):
        self.assertIn("key IN ('app_password_hash','auth_mode')", self.howto)

    def test_it_no_longer_tells_you_the_hash_alone_is_enough(self):
        self.assertNotIn("DELETE FROM settings WHERE key='app_password_hash';", self.howto)

    def test_the_open_mode_is_described_as_what_it_is(self):
        """"Leave both empty for open access" said nothing about what open access grants."""
        section = " ".join(self.howto[self.howto.index("**No password**"):][:700].split())
        self.assertIn("admin scope with no credential", section)
        self.assertIn("Settings", section)


if __name__ == "__main__":
    unittest.main()
