"""`APP_PASSWORD` must behave the way the documentation says it does.

The docs told operators to put a plaintext password in `APP_PASSWORD`; the code only
accepts one when `ALLOW_PLAINTEXT_APP_PASSWORD` is on, which nothing in the repository
mentioned. Following the deployment guide therefore produced an instance nobody could log
into: a 401 on the right password, a setup wizard that refuses to open because a non-empty
`APP_PASSWORD` counts as "configured", and not one line in the logs.

The docs are fixed; these tests hold the behaviour they now describe, and check the
rejection is no longer silent.
"""

import importlib
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

import app.auth as auth
from app import models


class _DbIsolated(unittest.TestCase):
    """`check_password` falls back to the DB hash, so give each test its own."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "password.test.db")
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()


class AppPasswordTests(_DbIsolated):
    def test_a_hash_in_the_variable_is_accepted(self):
        with patch.object(auth, "APP_PASSWORD", auth.hash_password("s3cret")):
            self.assertTrue(auth.check_password("s3cret"))
            self.assertFalse(auth.check_password("wrong"))

    def test_a_plaintext_value_is_refused_by_default(self):
        """The documented setup, before the fix: it silently could not work."""
        with patch.object(auth, "APP_PASSWORD", "s3cret"), \
             patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", False):
            self.assertFalse(auth.check_password("s3cret"))

    def test_the_refusal_says_why(self):
        with patch.object(auth, "APP_PASSWORD", "s3cret"), \
             patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", False), \
             self.assertLogs("app.auth", level=logging.ERROR) as captured:
            auth.check_password("s3cret")

        message = "\n".join(captured.output)
        self.assertIn("ALLOW_PLAINTEXT_APP_PASSWORD", message)
        self.assertIn("hash_password", message)

    def test_the_opt_in_still_works_for_a_lab_instance(self):
        with patch.object(auth, "APP_PASSWORD", "s3cret"), \
             patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", True):
            self.assertTrue(auth.check_password("s3cret"))
            self.assertFalse(auth.check_password("wrong"))

    def test_a_correct_hash_does_not_log_an_error(self):
        """The error must fire on the broken configuration only, not on every login."""
        logger = logging.getLogger("app.auth")
        with patch.object(auth, "APP_PASSWORD", auth.hash_password("s3cret")), \
             patch.object(logger, "error") as error:
            auth.check_password("s3cret")
        error.assert_not_called()

    def test_an_empty_variable_falls_through_to_the_database(self):
        conn = models.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (auth.hash_password("from-the-wizard"),),
        )
        conn.commit()
        conn.close()

        with patch.object(auth, "APP_PASSWORD", ""):
            self.assertTrue(auth.check_password("from-the-wizard"))


class AllowPlaintextParsingTests(unittest.TestCase):
    """The opt-in is read from the environment at import time."""

    def _flag_for(self, value: str | None) -> bool:
        env = dict(os.environ)
        env.pop("ALLOW_PLAINTEXT_APP_PASSWORD", None)
        if value is not None:
            env["ALLOW_PLAINTEXT_APP_PASSWORD"] = value
        with patch.dict(os.environ, env, clear=True):
            reloaded = importlib.reload(auth)
            flag = reloaded._ALLOW_PLAINTEXT_APP_PASSWORD
        importlib.reload(auth)  # restore the module the rest of the suite shares
        return flag

    def test_it_is_off_when_unset(self):
        self.assertFalse(self._flag_for(None))

    def test_the_documented_spellings_turn_it_on(self):
        for value in ("true", "TRUE", "1", "yes", " true "):
            with self.subTest(value=value):
                self.assertTrue(self._flag_for(value))

    def test_anything_else_leaves_it_off(self):
        for value in ("false", "0", "no", "", "maybe"):
            with self.subTest(value=value):
                self.assertFalse(self._flag_for(value))


class ChangingAnEnvManagedPasswordTests(_DbIsolated):
    """The screen offered a change that could not take effect, and cost the session.

    With `APP_PASSWORD` set, `check_password` returns on the variable and never reads the
    stored hash. `POST /api/auth/change-password` wrote one anyway, then bumped the session
    epoch: every session died -- the operator's included -- the new password was refused,
    and the way back in was the old password they had just tried to retire.
    """

    def test_the_variable_is_reported_as_the_owner(self):
        with patch.object(auth, "APP_PASSWORD", auth.hash_password("from-the-env-1")):
            self.assertTrue(auth.password_is_env_managed())

    def test_an_empty_variable_leaves_the_database_in_charge(self):
        with patch.object(auth, "APP_PASSWORD", ""):
            self.assertFalse(auth.password_is_env_managed())

    def test_a_plaintext_value_nobody_honours_leaves_the_database_in_charge(self):
        """`check_password` falls through to the stored hash here, so this screen works."""
        with patch.object(auth, "APP_PASSWORD", "plain-text"),              patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", False):
            self.assertFalse(auth.password_is_env_managed())

    def test_the_opted_in_plaintext_value_does_own_it(self):
        with patch.object(auth, "APP_PASSWORD", "plain-text"),              patch.object(auth, "_ALLOW_PLAINTEXT_APP_PASSWORD", True):
            self.assertTrue(auth.password_is_env_managed())

    def test_the_route_refuses_instead_of_writing_a_hash_nobody_reads(self):
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        import app.api.auth as api_auth

        body = api_auth.ChangePasswordBody(
            current_password="from-the-env-1",
            new_password="a-new-one-9876",
        )
        with patch.object(auth, "APP_PASSWORD", auth.hash_password("from-the-env-1")),              patch.object(api_auth, "require_auth", lambda *a, **k: None),              patch.object(api_auth, "bump_session_epoch") as bumped,              self.assertRaises(HTTPException) as ctx:
            api_auth.change_password.__wrapped__(MagicMock(), body)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("APP_PASSWORD", ctx.exception.detail)
        # The session epoch is what logs everyone out. It must not move for a change that
        # was refused.
        bumped.assert_not_called()

        conn = models.get_db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='app_password_hash'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row, "a refused change must leave no hash behind")

    def test_auth_me_names_the_owner_so_the_interface_can_say_so(self):
        import app.api.auth as api_auth

        with patch.object(auth, "APP_PASSWORD", auth.hash_password("from-the-env-1")),              patch.object(api_auth, "is_authenticated", lambda _r: True):
            payload = api_auth.auth_me(MagicMockRequest())
        self.assertEqual(payload["password_source"], "environment")

        with patch.object(auth, "APP_PASSWORD", ""),              patch.object(api_auth, "is_authenticated", lambda _r: True):
            payload = api_auth.auth_me(MagicMockRequest())
        self.assertEqual(payload["password_source"], "database")


class MagicMockRequest:
    """`auth_me` only ever passes the request on to `is_authenticated`, which is patched."""


class DocumentationMatchesTheCodeTests(unittest.TestCase):
    """The variable existed only in `app/auth.py`; an operator had no way to find it."""

    def _repo_file(self, *parts: str) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_opt_in_is_documented_where_an_operator_looks(self):
        for parts in ((".env.example",), ("docs", "HOWTO.md"), ("docs", "DEPLOYMENT.md")):
            with self.subTest(file="/".join(parts)):
                self.assertIn("ALLOW_PLAINTEXT_APP_PASSWORD", self._repo_file(*parts))

    def test_the_howto_no_longer_claims_plaintext_is_compared(self):
        howto = self._repo_file("docs", "HOWTO.md")
        self.assertNotIn("are compared in plaintext (not hashed)", howto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
