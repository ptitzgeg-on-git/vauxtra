"""Ending a session, and the four defaults that promised something they did not do.

Changing the admin password used to invalidate nothing. The session cookie is signed with
`SECRET_KEY` and says `authenticated`; neither of those changes when the password does, so
the one move an operator makes after "I think someone has my session" left that session
working for the rest of its seven days. A stored counter now stands between the two: a
cookie carries the epoch it was opened in, `change_password` raises the stored one, and a
cookie that disagrees is not merely ignored -- it is cleared, so the browser stops
presenting a credential that will never be accepted again.

The rest of this file is about statements the code made and did not keep:

- `app/limiter.py` announced `default_limits=["120/minute"]`. slowapi applies those through
  `SlowAPIMiddleware`, which is not mounted: nothing outside the decorated routes was ever
  limited, and the line was a reason to believe otherwise.
- every rate limit keys on the socket peer, which behind a reverse proxy is the proxy, for
  everyone. Five failed logins from anywhere locked the real operator out. Uvicorn will read
  `X-Forwarded-For` when told which hop may set it, and now does when told.
- `.env.example` said "leave empty for same-origin only" next to a default of three
  localhost origins allowed with credentials on every install.
- `index.html` fetched Inter from rsms.me on every page load of a tool that holds provider
  credentials, and there was no Content-Security-Policy to say that was unusual.
"""

import logging
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import app.auth as auth
import app.db as app_db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from app.api import settings as settings_api

REPO_ROOT = Path(__file__).resolve().parents[1]

PASSWORD = "correct horse battery"
NEW_PASSWORD = "a different passphrase entirely"


def _request_with_session(session: dict) -> Request:
    """A request carrying exactly this session, without SessionMiddleware in the way."""
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [], "session": session}
    )


class _IsolatedDB(unittest.TestCase):
    """A temp database, no rate limiting, and no password in the environment.

    `tests/` is not a package, so this base class is duplicated per file by design.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "session.test.db")
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = db_path
        models.init_db()

        # The credential has to live in the database for `change_password` to move it.
        self._password_patch = patch.object(auth, "APP_PASSWORD", "")
        self._password_patch.start()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(_limiter, "_check_request_limit", _no_rate_limit)
        self._limiter_patch.start()

    def tearDown(self) -> None:
        self._limiter_patch.stop()
        self._password_patch.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- helpers ----------------------------------------------------------------

    def _stored_epoch(self) -> str:
        conn = models.get_db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='session_epoch'"
            ).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()

    def _write(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


class _WithServer(_IsolatedDB):
    """The whole application, over HTTP, so the cookie is a real cookie."""

    def setUp(self) -> None:
        super().setUp()
        self._start_patch = patch.object(scheduler, "start", lambda interval_minutes=0: None)
        self._configure_patch = patch.object(
            scheduler, "configure", lambda interval_minutes=0: None
        )
        self._start_patch.start()
        self._configure_patch.start()
        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        self._configure_patch.stop()
        self._start_patch.stop()
        super().tearDown()

    def _browser(self) -> TestClient:
        """A second, independent cookie jar against the same application."""
        return TestClient(app_main.app)

    def _configure_password(self) -> None:
        resp = self.client.post("/api/auth/setup-password", json={"password": PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.text)

    def _login(self, browser: TestClient, password: str = PASSWORD) -> None:
        resp = browser.post("/api/auth/login", json={"password": password})
        self.assertEqual(resp.status_code, 200, resp.text)


class AChangedPasswordEndsEveryOtherSessionTests(_WithServer):
    def test_a_session_opened_by_login_is_accepted_afterwards(self) -> None:
        self._configure_password()
        browser = self._browser()
        self._login(browser)
        self.assertEqual(browser.get("/api/settings").status_code, 200)

    def test_the_other_browser_is_logged_out_by_the_change(self) -> None:
        self._configure_password()
        thief = self._browser()
        self._login(thief)
        self.assertEqual(thief.get("/api/settings").status_code, 200)

        # The operator changes the password from their own browser.
        owner = self._browser()
        self._login(owner)
        resp = owner.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        self.assertEqual(thief.get("/api/settings").status_code, 401)

    def test_the_browser_that_changed_it_stays_logged_in(self) -> None:
        """Logging the operator out of the browser they are holding, as a side effect of
        following the advice, is how people learn not to follow it."""
        self._configure_password()
        owner = self._browser()
        self._login(owner)
        owner.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        self.assertEqual(owner.get("/api/settings").status_code, 200)

    def test_the_evicted_cookie_is_cleared_not_merely_refused(self) -> None:
        self._configure_password()
        thief = self._browser()
        self._login(thief)
        self.assertTrue(thief.cookies.get("vauxtra_session"))

        owner = self._browser()
        self._login(owner)
        owner.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )

        refused = thief.get("/api/settings")
        self.assertEqual(refused.status_code, 401)
        # The browser is told to drop it, so it stops sending a credential that can never
        # be accepted again -- and stops looking logged in to anything that reads the jar.
        self.assertFalse(thief.cookies.get("vauxtra_session"))

    def test_the_new_password_opens_a_session_that_works(self) -> None:
        self._configure_password()
        owner = self._browser()
        self._login(owner)
        owner.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )

        after = self._browser()
        self._login(after, NEW_PASSWORD)
        self.assertEqual(after.get("/api/settings").status_code, 200)

    def test_the_password_setup_auto_login_carries_an_epoch(self) -> None:
        """`setup-password` logs the operator straight in; that session needs the stamp too,
        or the very next request would throw it away."""
        self._configure_password()
        self.assertEqual(self.client.get("/api/settings").status_code, 200)


class ACookieIsCheckedAgainstTheStoredEpochTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        self._write(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (auth.hash_password(PASSWORD),),
        )
        self._write(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_mode', 'password')"
        )

    def test_a_matching_epoch_is_authenticated(self) -> None:
        request = _request_with_session({"authenticated": True, "epoch": 0})
        self.assertTrue(auth.is_authenticated(request))

    def test_a_cookie_from_before_this_existed_is_refused(self) -> None:
        """No epoch at all is what every cookie minted by the previous release carries. The
        upgrade costs one re-login, which is the right answer for a credential issued under
        rules that had no way to expire it."""
        session = {"authenticated": True}
        self.assertFalse(auth.is_authenticated(_request_with_session(session)))
        self.assertEqual(session, {})

    def test_a_stale_epoch_is_refused_and_dropped(self) -> None:
        conn = models.get_db()
        try:
            auth.bump_session_epoch(conn)
            conn.commit()
        finally:
            conn.close()

        session = {"authenticated": True, "epoch": 0}
        self.assertFalse(auth.is_authenticated(_request_with_session(session)))
        self.assertEqual(session, {})

    def test_the_epoch_alone_does_not_authenticate(self) -> None:
        self.assertFalse(auth.is_authenticated(_request_with_session({"epoch": 0})))


class TheCounterFailsClosedTests(_IsolatedDB):
    def test_a_missing_row_reads_as_zero(self) -> None:
        self.assertEqual(auth.current_session_epoch(), 0)

    def test_bumping_from_nothing_gives_one(self) -> None:
        conn = models.get_db()
        try:
            auth.bump_session_epoch(conn)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._stored_epoch(), "1")
        self.assertEqual(auth.current_session_epoch(), 1)

    def test_a_value_nobody_can_parse_restarts_the_count(self) -> None:
        """Hand-edited or half-restored rows must not make the counter throw on the one
        route an operator reaches for when they think they have been compromised."""
        self._write(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('session_epoch', 'lundi')"
        )
        conn = models.get_db()
        try:
            auth.bump_session_epoch(conn)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._stored_epoch(), "1")

    def test_an_unreadable_database_matches_no_cookie_at_all(self) -> None:
        with patch.object(auth, "_read_auth_settings", side_effect=sqlite3.Error("locked")):
            unreadable = auth.current_session_epoch()
        self.assertEqual(unreadable, -1)
        # -1 is not a value the writer can ever produce: a missing row reads as 0 and every
        # bump adds one, so no stored cookie can match a database nobody could read.
        self.assertNotIn(unreadable, (0, 1))


class AResetDoesNotResurrectASessionTests(_IsolatedDB):
    def test_the_counter_survives_the_reset(self) -> None:
        """It only ever goes up, so deleting it sends it back to 0 -- the epoch every cookie
        minted before the first password change is still carrying. The reset would have
        handed those sessions back their access."""
        self._write(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('session_epoch', '5')"
        )
        settings_api.reset_all(_request_with_session({"authenticated": True, "epoch": 5}))
        self.assertEqual(self._stored_epoch(), "5")

    def test_it_is_named_in_the_protected_keys(self) -> None:
        self.assertIn("session_epoch", settings_api._PROTECTED_SETTINGS)

    def test_the_placeholders_are_built_from_the_keys(self) -> None:
        """They were written out by hand as `(?,?,?)`, so adding a key would have raised
        "Incorrect number of bindings" at the exact moment an operator asked for a reset."""
        self.assertEqual(
            settings_api._PROTECTED_PLACEHOLDERS.count("?"),
            len(settings_api._PROTECTED_SETTINGS),
        )

    def test_a_reset_does_not_log_the_operator_out(self) -> None:
        """The password is untouched by a reset, so evicting the sessions buys nothing an
        attacker could not undo by logging in -- and costs the operator a 401 on the click
        right after the one they meant."""
        self._write(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (auth.hash_password(PASSWORD),),
        )
        self._write(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_mode', 'password')"
        )
        session = {"authenticated": True, "epoch": 0}
        settings_api.reset_all(_request_with_session(session))
        self.assertTrue(auth.is_authenticated(_request_with_session(session)))

    def test_it_cannot_be_written_through_the_settings_api(self) -> None:
        self.assertNotIn("session_epoch", settings_api._VALID_SETTINGS)


class TheResponseHeadersTests(_WithServer):
    def _headers_of(self, path: str = "/api/auth/me") -> dict:
        return dict(self.client.get(path).headers)

    def test_a_policy_is_sent(self) -> None:
        policy = self._headers_of().get("content-security-policy", "")
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)

    def test_scripts_and_connections_stay_on_this_origin(self) -> None:
        policy = self._headers_of().get("content-security-policy", "")
        self.assertIn("script-src 'self'", policy)
        self.assertIn("connect-src 'self'", policy)
        # No third-party host anywhere in the policy: that is the point of self-hosting the
        # font. `img-src` is the single exception, and it names no host either.
        self.assertNotIn("//", policy)

    def test_the_font_may_not_come_from_elsewhere(self) -> None:
        policy = self._headers_of().get("content-security-policy", "")
        self.assertIn("font-src 'self' data:", policy)

    def test_a_service_icon_may_still_be_a_remote_image(self) -> None:
        policy = self._headers_of().get("content-security-policy", "")
        self.assertIn("img-src 'self' data: https:", policy)

    def test_the_schema_is_exempt_so_swagger_can_load(self) -> None:
        """Swagger UI pulls its bundle from a CDN; the policy would leave a blank page
        explained only in the browser console."""
        self.assertNotIn("content-security-policy", self._headers_of("/openapi.json"))

    def test_the_old_headers_are_still_there(self) -> None:
        headers = self._headers_of()
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        self.assertEqual(headers.get("referrer-policy"), "strict-origin-when-cross-origin")

    def test_hsts_follows_https_only(self) -> None:
        self.assertNotIn("strict-transport-security", self._headers_of())
        with patch.object(app_main, "HTTPS_ONLY", True):
            self.assertIn("strict-transport-security", self._headers_of())


class TheProxyIsAnnouncedOrItIsNotTrustedTests(_WithServer):
    def setUp(self) -> None:
        super().setUp()
        app_main._warned_about_forwarded_headers = False
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("FORWARDED_ALLOW_IPS", None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        app_main._warned_about_forwarded_headers = False
        super().tearDown()

    def test_a_forwarded_request_says_what_it_costs(self) -> None:
        with self.assertLogs("app.main", level=logging.WARNING) as logs:
            self.client.get("/api/auth/me", headers={"X-Forwarded-For": "203.0.113.7"})
        said = "\n".join(logs.output)
        self.assertIn("FORWARDED_ALLOW_IPS", said)
        self.assertIn("shared by every visitor", said)

    def test_it_is_said_once_not_on_every_request(self) -> None:
        with self.assertLogs("app.main", level=logging.WARNING) as logs:
            for _ in range(5):
                self.client.get("/api/auth/me", headers={"X-Forwarded-For": "203.0.113.7"})
        forwarded = [line for line in logs.output if "FORWARDED_ALLOW_IPS" in line]
        self.assertEqual(len(forwarded), 1)

    def test_a_configured_proxy_is_not_warned_about(self) -> None:
        os.environ["FORWARDED_ALLOW_IPS"] = "172.18.0.2"
        with patch.object(app_main._logger, "warning") as warned:
            self.client.get("/api/auth/me", headers={"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(warned.call_count, 0)

    def test_nothing_is_said_when_no_proxy_is_involved(self) -> None:
        with patch.object(app_main._logger, "warning") as warned:
            self.client.get("/api/auth/me")
        self.assertEqual(warned.call_count, 0)


class TheSourceNoLongerPromisesWhatItDoesNotDoTests(unittest.TestCase):
    """These read files rather than behaviour: each defect below *was* a file saying one
    thing while the running code did another, and only the file can be checked."""

    def _read(self, relative: str) -> str:
        return (REPO_ROOT / relative).read_text(encoding="utf-8")

    def test_the_limiter_announces_no_limit_it_does_not_apply(self) -> None:
        """Read from the AST, not grepped: the docstring of that module explains what was
        removed and why, so the name is still in the file on purpose."""
        import ast

        tree = ast.parse(self._read("app/limiter.py"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        limiters = [c for c in calls if getattr(c.func, "id", "") == "Limiter"]
        self.assertEqual(len(limiters), 1)
        self.assertEqual([kw.arg for kw in limiters[0].keywords], ["key_func"])
        # And the middleware that would have made those limits real is still not mounted,
        # so the line must not come back by being "fixed" the other way round.
        self.assertNotIn("SlowAPIMiddleware", self._read("app/main.py"))

    def test_the_login_route_is_still_limited(self) -> None:
        """Removing the unused global must not be read as removing rate limiting."""
        self.assertIn('@limiter.limit("5/minute;20/hour")', self._read("app/api/auth.py"))

    def test_the_entrypoint_trusts_a_proxy_only_when_told_which(self) -> None:
        entrypoint = self._read("docker-entrypoint.sh")
        self.assertIn("FORWARDED_ALLOW_IPS", entrypoint)
        self.assertIn("--proxy-headers", entrypoint)
        # Never unconditionally: reading `X-Forwarded-For` from anyone lets any caller claim
        # any client address, which is worse than the problem it solves.
        self.assertIn('if [ -n "${FORWARDED_ALLOW_IPS:-}" ]; then', entrypoint)

    def test_the_example_env_documents_the_variable_it_needs(self) -> None:
        example = self._read(".env.example")
        self.assertIn("FORWARDED_ALLOW_IPS", example)
        # The old wording told operators to leave HTTPS_ONLY off behind a reverse proxy,
        # which is exactly the deployment that needs the Secure cookie and HSTS.
        self.assertNotIn("(not behind a reverse proxy)", example)

    def test_the_page_loads_nothing_from_another_host(self) -> None:
        index = self._read("frontend/index.html")
        self.assertNotIn("rsms.me", index)
        self.assertNotIn('href="http', index)
        self.assertNotIn('src="http', index)

    def test_the_font_is_bundled_instead(self) -> None:
        self.assertIn("@fontsource-variable/inter", self._read("frontend/src/main.tsx"))
        self.assertIn("@fontsource-variable/inter", self._read("frontend/package.json"))
        # Declaring the dependency and importing it is not enough if the stack never names
        # the family the bundled file actually registers.
        self.assertIn("Inter Variable", self._read("frontend/tailwind.config.js"))


if __name__ == "__main__":
    unittest.main()
