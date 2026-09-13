"""Signing in, failing to sign in and setting the password must reach the journal.

`app/api/auth.py` and `app/auth.py` held no `add_log` call between them. Every other kind
of change wrote one -- a domain deleted, a provider saved, a webhook retried -- so the
activity log read as a complete record of what had happened to the instance, and the single
category missing from it was the one an operator opens it for after a suspected compromise:
who signed in, and how many times somebody failed to.

The lines carry no client address, on purpose. `request.client.host` is the reverse proxy
for every caller unless `FORWARDED_ALLOW_IPS` is set, which `app/limiter.py` explains at
length, so an address written here would name the proxy in the investigation it exists for.
The last test holds that, because an address is exactly what a later reader would think to
add.
"""

import os
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models

# Not a credential: twelve characters and five distinct ones is the whole rule
# (`validate_password_strength`), and these are the example everyone recognises as an
# example.
_PASSWORD = "correct-horse-battery"
_NEW_PASSWORD = "staple-bagpipe-lantern"


class _AuthJournalCase(unittest.TestCase):
    """A live app on a temporary database, with the login rate limit out of the way."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)
        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "auth.journal.test.db")
        models.init_db()

        self._patches = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            # Five a minute is the point of the limit and the reason these lines are bounded,
            # but it also means the fourth test of a run would get a 429 instead of a 401.
            patch.object(auth, "APP_PASSWORD", ""),
        ]
        for p in self._patches:
            p.start()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(_limiter, "_check_request_limit", _no_rate_limit)
        self._limiter_patch.start()

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        self._limiter_patch.stop()
        for p in reversed(self._patches):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # ---- helpers ----

    def _logs(self) -> list[tuple[str, str]]:
        conn = models.get_db()
        try:
            rows = conn.execute("SELECT level, message FROM logs ORDER BY id").fetchall()
        finally:
            conn.close()
        return [(r["level"], r["message"]) for r in rows]

    def _clear_logs(self) -> None:
        conn = models.get_db()
        try:
            conn.execute("DELETE FROM logs")
            conn.commit()
        finally:
            conn.close()

    def _install_password(self) -> None:
        """Go through the wizard route, which is how a real instance gets its password."""
        resp = self.client.post("/api/auth/setup-password", json={"password": _PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.text)


class AuthJournalTests(_AuthJournalCase):
    def test_setting_the_password_in_the_wizard_is_recorded(self) -> None:
        self._install_password()

        written = [m for level, m in self._logs() if "password" in m.lower()]
        self.assertEqual(len(written), 1, self._logs())
        self.assertIn("setup wizard", written[0])

    def test_a_wrong_password_is_recorded_as_a_problem(self) -> None:
        self._install_password()
        self._clear_logs()

        resp = self.client.post("/api/auth/login", json={"password": "not the password"})
        self.assertEqual(resp.status_code, 401)

        self.assertEqual(self._logs(), [("warning", "Sign-in refused: wrong password")])

    def test_every_failed_attempt_gets_its_own_line(self) -> None:
        """Three wrong guesses must read as three, not as one."""
        self._install_password()
        self._clear_logs()

        for _ in range(3):
            self.client.post("/api/auth/login", json={"password": "still wrong"})

        self.assertEqual(len(self._logs()), 3, self._logs())

    def test_signing_in_is_recorded(self) -> None:
        self._install_password()
        self._clear_logs()

        resp = self.client.post("/api/auth/login", json={"password": _PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.text)

        self.assertEqual(self._logs(), [("info", "Signed in")])

    def test_changing_the_password_says_the_other_sessions_ended(self) -> None:
        """The line has to carry the consequence: every other cookie stopped working."""
        self._install_password()
        self._clear_logs()

        resp = self.client.post(
            "/api/auth/change-password",
            json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        self.assertEqual(len(self._logs()), 1, self._logs())
        level, message = self._logs()[0]
        self.assertEqual(level, "info")
        self.assertIn("signed out", message)

    def test_a_refused_change_writes_nothing(self) -> None:
        """A wrong current password changes nothing, so it claims nothing."""
        self._install_password()
        self._clear_logs()

        resp = self.client.post(
            "/api/auth/change-password",
            json={"current_password": "not it", "new_password": _NEW_PASSWORD},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._logs(), [])

    def test_no_line_carries_a_client_address(self) -> None:
        """Behind a proxy that address is the proxy, for everyone. It must stay out."""
        self._install_password()
        self.client.post("/api/auth/login", json={"password": "wrong"})
        self.client.post("/api/auth/login", json={"password": _PASSWORD})
        self.client.post(
            "/api/auth/change-password",
            json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )

        looks_like_an_address = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}|testclient")
        for level, message in self._logs():
            self.assertIsNone(looks_like_an_address.search(message), (level, message))


if __name__ == "__main__":
    unittest.main()
