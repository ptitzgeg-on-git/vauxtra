"""A key granted no scope at all: refused at creation, and never read back as a scope.

`POST /api/settings/api-keys` accepted `"scopes": []`. The validator loops over the list to
check each value, and an empty list has nothing to loop over, so the request went through:
the row was written with an empty `scopes` column, and `"".split(",")` read it back as
`[""]` -- a scope that does not exist, satisfies nothing, and is drawn in the key list as a
blank badge where the permission should be. The operator could not tell what that key was
allowed to do, and the key still answered every route whose `require_auth` asks for no
scope in particular. The one key that had been granted the least was the one that looked
fine and behaved worst.

The interface never produced one (`ApiKeysTab` refuses to submit with no box ticked). A
script or the MCP bridge did, and the phantom row then showed up in that same list.

The last test here is about the file header rather than about a request: it claimed the
prefix kept for display was the first 8 characters of the key while the code kept 10.
"""

import ast
import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as app_db
import app.scheduler as scheduler
from app import models

PASSWORD = "test-password-long-enough"

# An unscoped read route: `require_auth(request)` with no scope, which is the shape that
# never consults the granted scopes at all.
UNSCOPED_ROUTE = "/api/environments"


class EmptyScopeKeys(unittest.TestCase):
    """A temp database, one key per scope plus one granted nothing, no rate limiting.

    `tests/` is not a package, so this fixture is duplicated per file by design.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "empty-scopes.test.db")
        models.init_db()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            # Without a password every request is granted `admin`, and no scope is tested.
            patch.object(auth, "APP_PASSWORD", PASSWORD),
            # The creation route is capped at 10/minute, and this file posts more than that.
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
            # `ghost` is what the hole used to write: a real row, with an empty scope column.
            for name, scopes in (("ro", "read"), ("adm", "admin"), ("ghost", "")):
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

    def _stored(self, name: str) -> list[str]:
        """The `scopes` column as SQLite holds it, for every row under that name."""
        conn = models.get_db()
        try:
            return [
                r["scopes"]
                for r in conn.execute(
                    "SELECT scopes FROM api_keys WHERE name=?", (name,)
                ).fetchall()
            ]
        finally:
            conn.close()

    def _listed(self, name: str) -> dict:
        resp = self.client.get("/api/settings/api-keys", headers=self._headers("adm"))
        self.assertEqual(resp.status_code, 200, resp.text)
        matching = [k for k in resp.json() if k["name"] == name]
        self.assertEqual(len(matching), 1, f"expected exactly one key named {name}: {resp.text}")
        return matching[0]

    # -- creation ---------------------------------------------------------------

    def test_creating_a_key_with_an_empty_scope_list_is_refused(self):
        """And nothing is written: a refused creation must not leave a row behind."""
        resp = self.client.post(
            "/api/settings/api-keys",
            json={"name": "monitoring", "scopes": []},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(self._stored("monitoring"), [])

    def test_the_phantom_scope_cannot_be_asked_for_by_name_either(self):
        """`[""]` is the value the old rows read back as, so the API must not accept it."""
        resp = self.client.post(
            "/api/settings/api-keys",
            json={"name": "monitoring", "scopes": [""]},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(self._stored("monitoring"), [])

    def test_one_scope_is_still_accepted(self):
        """Positive witness: the refusal must fall on the empty list and on nothing else."""
        resp = self.client.post(
            "/api/settings/api-keys",
            json={"name": "dashboard", "scopes": ["read"]},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["scopes"], ["read"])
        self.assertEqual(self._stored("dashboard"), ["read"])
        self.assertEqual(self._listed("dashboard")["scopes"], ["read"])

    def test_omitting_the_field_keeps_the_default_read_scope(self):
        """Positive witness: the constraint applies to what was sent, not to the default."""
        resp = self.client.post(
            "/api/settings/api-keys",
            json={"name": "defaulted"},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["scopes"], ["read"])
        self.assertEqual(self._stored("defaulted"), ["read"])

    def test_several_scopes_survive_the_round_trip(self):
        """Positive witness: deduplication still works and no real scope is dropped."""
        resp = self.client.post(
            "/api/settings/api-keys",
            json={"name": "operator", "scopes": ["read", "admin", "read"]},
            headers=self._headers("adm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["scopes"], ["admin", "read"])
        self.assertEqual(self._listed("operator")["scopes"], ["admin", "read"])

    # -- rows the hole already wrote --------------------------------------------

    def test_a_row_stored_without_a_scope_is_listed_as_an_empty_list(self):
        """`[""]` says "one permission, unnamed"; `[]` says "none", which is the truth."""
        self.assertEqual(self._listed("ghost")["scopes"], [])

    def test_a_row_with_real_scopes_is_listed_untouched(self):
        """Positive witness: the filter drops empty segments, not short ones."""
        self.assertEqual(self._listed("ro")["scopes"], ["read"])
        self.assertEqual(self._listed("adm")["scopes"], ["admin"])

    def test_a_key_granted_nothing_is_refused_where_no_scope_is_asked(self):
        """The whole point of the row: it authorizes nothing, so it must open nothing.

        An unscoped `require_auth` never reaches `_scope_satisfies`, so before this the
        phantom key passed here and was refused everywhere else -- the exact shape that
        sends a troubleshooter looking for a broken permission instead of a broken key.
        """
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("ghost"))
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_a_read_key_still_passes_that_same_unscoped_route(self):
        """Positive witness: the route is reachable, and a key granted `read` still reaches it."""
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("ro"))
        self.assertEqual(resp.status_code, 200, resp.text)


class PrefixCommentTruth(unittest.TestCase):
    def test_the_header_names_the_prefix_length_the_code_actually_keeps(self):
        """The header said "first 8 chars" while `prefix = raw_key[:10]` kept ten.

        Nothing computes on that number -- the key list prints whatever the column holds --
        so the sentence could only mislead the next reader of the file. It is asked of the
        source rather than of a constant because there is no constant: the slice is written
        inline, and a comment that drifts from it drifts in silence.
        """
        source = (
            Path(__file__).resolve().parent.parent / "app" / "api" / "api_keys.py"
        ).read_text(encoding="utf-8")

        slices = re.findall(r"raw_key\[:(\d+)\]", source)
        self.assertEqual(len(slices), 1, "expected exactly one prefix slice in api_keys.py")
        kept = slices[0]

        header = ast.get_docstring(ast.parse(source)) or ""
        claimed = re.findall(r"first (\d+) chars", header)
        self.assertEqual(
            claimed,
            [kept],
            f"the file header claims {claimed or 'nothing'} while the code keeps {kept}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
