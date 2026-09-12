"""Environments answered a write the way no other list in this panel does.

Settings edits two taxonomies side by side, tags and environments, through one component and
one input. The tag routes read the row back before they write: gone means 404, name taken
means 409. The environment routes wrote first and answered afterwards, so a PUT on a row
another tab had just deleted returned 200 and the panel showed "Environment updated" over a
write that never happened, and a rename onto a name already taken reached the UNIQUE index
uncaught and came back as a 500 "Server error" instead of "this name is taken".

Creation hid the same shape from the other side: a bare `except Exception` around the INSERT
turned a locked database, a full disk or a half-applied migration into "Environment already
exists", which sends whoever reads the API looking for a row that was never written.

The name length was the third voice in the same chorus. A tag name stopped at 32 characters
and an environment name had no ceiling at all, though both are typed into the same field.

Every scenario below therefore runs against both taxonomies at once. Tags are the witness:
they were already green and must stay green, otherwise these assertions are measuring the
harness instead of the fix.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app import models
from app.api import environments as environments_api
from app.api import tags as tags_api

# The two halves of the panel, and the table each one writes to.
_KINDS = ("tags", "environments")
_TABLE = {"tags": "tags", "environments": "environments"}


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _no_auth(_req, scope=None):
    """Stand in for `require_auth`, which is not what any of this measures."""
    return None


class _WritesFail:
    """A connection that reads normally and refuses to write, the way a locked base does.

    Only the write fails, so a route that looks before it writes still gets its answer from
    the real table and trips only on the statement that actually stores something.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, *args):
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _create(kind: str, name: str, color: str = "blue"):
    if kind == "tags":
        with patch.object(tags_api, "require_auth", _no_auth):
            return tags_api.create_tag(_request(), tags_api.TagIn(name=name, color=color))
    with patch.object(environments_api, "require_auth", _no_auth):
        return environments_api.add_environment(_request(), {"name": name, "color": color})


def _update(kind: str, ident: int, name: str, color: str = "blue"):
    if kind == "tags":
        with patch.object(tags_api, "require_auth", _no_auth):
            body = tags_api.TagIn(name=name, color=color)
            return tags_api.update_tag(ident, _request("PUT"), body)
    with patch.object(environments_api, "require_auth", _no_auth):
        return environments_api.update_environment(
            ident, _request("PUT"), {"name": name, "color": color}
        )


def _delete(kind: str, ident: int):
    if kind == "tags":
        with patch.object(tags_api, "require_auth", _no_auth):
            return tags_api.delete_tag(ident, _request("DELETE"))
    with patch.object(environments_api, "require_auth", _no_auth):
        return environments_api.delete_environment(ident, _request("DELETE"))


def _status(call, *args) -> int:
    """The status a client reads back from `call`, 200 when the call went through.

    A tag refuses a malformed name inside `TagIn`, which FastAPI answers as a 422 before the
    route is even entered; an environment body is a plain dict and refuses it in the route
    itself. Both come out of here as a number, the only form in which the two lists can be
    compared. Success is flattened to 200: the 201 on creation is on the decorator, not on
    the value the function returns.
    """
    try:
        call(*args)
    except ValidationError:
        return 422
    except HTTPException as exc:
        return exc.status_code
    return 200


def _stored(kind: str, ident: int) -> dict | None:
    """The row as the database holds it, so a green answer can be checked against a write."""
    conn = models.get_db()
    try:
        row = conn.execute(
            f"SELECT name, color FROM {_TABLE[kind]} WHERE id=?", (ident,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)

        db_path = os.path.join(self._tmpdir.name, "environments.parity.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()


class MissingRowTests(_IsolatedDB):
    """A write aimed at a row that is gone says so instead of reporting a success."""

    def test_updating_a_row_that_is_gone_answers_404(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(_status(_update, kind, 999, "ghost"), 404)

    def test_deleting_a_row_that_is_gone_answers_404(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(_status(_delete, kind, 999), 404)

    def test_a_row_that_is_there_is_still_renamed_and_still_deleted(self) -> None:
        """The witness: the check refuses the missing row and nothing else."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-alive")
                self.assertEqual(_status(_update, kind, created["id"], f"{kind}-renamed"), 200)
                self.assertEqual(_stored(kind, created["id"])["name"], f"{kind}-renamed")
                self.assertEqual(_status(_delete, kind, created["id"]), 200)
                self.assertIsNone(_stored(kind, created["id"]))


class NameCollisionTests(_IsolatedDB):
    """The UNIQUE index is not an error report. The route answers 409 before reaching it."""

    def test_creating_a_name_already_taken_answers_409(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                _create(kind, f"{kind}-dup")
                self.assertEqual(_status(_create, kind, f"{kind}-dup"), 409)

    def test_renaming_onto_a_name_already_taken_answers_409(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                first = _create(kind, f"{kind}-alpha")
                second = _create(kind, f"{kind}-beta")
                self.assertEqual(_status(_update, kind, second["id"], first["name"]), 409)
                self.assertEqual(_stored(kind, second["id"])["name"], f"{kind}-beta")

    def test_a_row_keeping_its_own_name_is_not_a_collision(self) -> None:
        """The witness: recolouring a row without renaming it is the panel's commonest edit."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-keep", "blue")
                self.assertEqual(
                    _status(_update, kind, created["id"], f"{kind}-keep", "green"), 200
                )
                self.assertEqual(_stored(kind, created["id"])["color"], "green")


class WriteFailureTests(_IsolatedDB):
    """A write that fails for any other reason must not be dressed up as a duplicate."""

    def test_a_locked_database_is_not_reported_as_a_duplicate(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                module = tags_api if kind == "tags" else environments_api
                with patch.object(module, "get_db", lambda: _WritesFail(models.get_db())):
                    with self.assertRaises(sqlite3.OperationalError):
                        _create(kind, f"{kind}-never-stored")

    def test_a_working_database_still_stores_the_row(self) -> None:
        """The witness: the fake failure above is the only thing that fails."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-stored")
                self.assertEqual(_stored(kind, created["id"])["name"], f"{kind}-stored")


class NameLengthTests(_IsolatedDB):
    """One ceiling for both lists, because there is one field for both lists."""

    def test_a_name_over_32_characters_is_refused_on_creation(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(_status(_create, kind, "x" * 33), 422)

    def test_a_name_over_32_characters_is_refused_on_a_rename(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-short")
                self.assertEqual(_status(_update, kind, created["id"], "x" * 33), 422)
                self.assertEqual(_stored(kind, created["id"])["name"], f"{kind}-short")

    def test_a_name_of_exactly_32_characters_goes_through(self) -> None:
        """The witness: the ceiling refuses what is above it and nothing below it."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(_status(_create, kind, "x" * 32), 200)

    def test_an_empty_name_is_still_refused(self) -> None:
        """The witness for the other end of the same field, which already worked."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                self.assertIn(_status(_create, kind, "   "), (400, 422))


if __name__ == "__main__":
    unittest.main()
