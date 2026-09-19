"""A duplicate that arrives while the row is being written is still a duplicate.

Three creation routes ask for the duplicate before they write it: `POST /api/docker/endpoints`
(`app/api/docker.py`), `POST /api/environments` and `POST /api/tags`. That lookup is what
produces the sentence an operator can act on, and it replaced a bare `except Exception` around
the INSERT that called a locked base, a missing column and a full disk duplicates too.

A SELECT followed by an INSERT is two statements, not one atomic step. Two calls carrying the
same key both pass the lookup on a table that does not hold it yet; the second INSERT meets the
UNIQUE index, raises `sqlite3.IntegrityError`, and nothing caught it. Measured on all three
routes before the fix: `500 Internal Server Error`, a server announcing that it broke over a
conflict it has a word for, on a form whose only remaining move is to try again.

The race run here is real, not simulated. `_RacingConnection` hands every statement to a
genuine SQLite connection and, at the instant the route issues its INSERT, lets a *second*
connection commit the same key and close. The pre-check therefore reads a table without the
row and the INSERT, a moment later, one with it -- the interleaving of two concurrent callers,
with the window held open instead of waited for. Blinding the pre-check so the INSERT trips on
its own would have exercised the handler without ever showing that the two statements can
disagree, which is the whole defect, so that is not what is done.

Both halves stay, and both are measured: the lookup still answers the ordinary duplicate
without reaching the index (`test_the_ordinary_duplicate_is_still_refused_by_the_lookup`), and
the handler is narrow enough that a locked base is still our own 500
(`test_a_write_that_fails_for_another_reason_is_still_our_own_500`).
"""

import os
import sqlite3
import tempfile
import unittest
from types import ModuleType
from typing import NamedTuple
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as _app_db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from app.api import docker as docker_api
from app.api import environments as environments_api
from app.api import tags as tags_api


class _RacingConnection:
    """A real connection that lets a competitor take the key at the moment of the INSERT.

    Every statement is delegated untouched. The only thing added is timing: the first INSERT
    the route issues is held for exactly as long as it takes a second connection to store the
    same key and commit it, which is the window two concurrent callers open by themselves.
    """

    def __init__(self, conn: sqlite3.Connection, competitor) -> None:
        self._conn = conn
        self._competitor = competitor
        self.statements: list[str] = []
        self.raced = False

    def execute(self, sql: str, *args):
        self.statements.append(sql)
        if sql.lstrip().upper().startswith("INSERT") and not self.raced:
            self.raced = True
            self._competitor()
        return self._conn.execute(sql, *args)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


class _InsertFails:
    """Reads normally, refuses to insert -- the way a locked base or a full disk behaves."""

    def __init__(self, conn: sqlite3.Connection, error: Exception) -> None:
        self._conn = conn
        self._error = error

    def execute(self, sql: str, *args):
        if sql.lstrip().upper().startswith("INSERT"):
            raise self._error
        return self._conn.execute(sql, *args)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


class _Creation(NamedTuple):
    """One creation route, and what a competitor needs to beat it to its unique key."""

    label: str
    module: ModuleType
    path: str
    body: dict
    table: str
    key_column: str
    key: str
    rival_sql: str
    rival_row: tuple
    witness_column: str  # the column that says which of the two callers wrote the surviving row
    rival_value: str
    our_value: str
    refusal: str


CREATIONS = (
    _Creation(
        label="POST /api/docker/endpoints",
        module=docker_api,
        path="/api/docker/endpoints",
        body={"name": "lab", "docker_host": "tcp://192.168.1.10:2375"},
        table="docker_endpoints",
        key_column="docker_host",
        key="tcp://192.168.1.10:2375",
        rival_sql=(
            "INSERT INTO docker_endpoints (name, docker_host, enabled, is_default) "
            "VALUES (?,?,1,0)"
        ),
        rival_row=("rival", "tcp://192.168.1.10:2375"),
        witness_column="name",
        rival_value="rival",
        our_value="lab",
        refusal="Docker endpoint host already exists",
    ),
    _Creation(
        label="POST /api/environments",
        module=environments_api,
        path="/api/environments",
        body={"name": "prod", "color": "blue"},
        table="environments",
        key_column="name",
        key="prod",
        rival_sql="INSERT INTO environments (name, color) VALUES (?,?)",
        rival_row=("prod", "red"),
        witness_column="color",
        rival_value="red",
        our_value="blue",
        refusal="An environment with this name already exists",
    ),
    _Creation(
        label="POST /api/tags",
        module=tags_api,
        path="/api/tags",
        body={"name": "edge", "color": "blue"},
        table="tags",
        key_column="name",
        key="edge",
        rival_sql="INSERT INTO tags (name, color) VALUES (?,?)",
        rival_row=("edge", "red"),
        witness_column="color",
        rival_value="red",
        our_value="blue",
        refusal="A tag with this name already exists",
    ),
)


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "race.test.db")
        models.init_db()

        for target, attribute, value in (
            (auth, "APP_PASSWORD", ""),
            (scheduler, "start", lambda interval_minutes=0: None),
            (scheduler, "configure", lambda interval_minutes=0: None),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- asking the real application, so the number measured is the one an operator reads ----

    def _post(self, creation: _Creation, connection_factory=None):
        """POST the creation body, optionally through a connection the test controls."""
        if connection_factory is None:
            with TestClient(app_main.app, raise_server_exceptions=False) as client:
                return client.post(creation.path, json=creation.body)

        with patch.object(creation.module, "get_db", connection_factory):
            with TestClient(app_main.app, raise_server_exceptions=False) as client:
                return client.post(creation.path, json=creation.body)

    def _rows(self, creation: _Creation) -> list[sqlite3.Row]:
        conn = models.get_db()
        try:
            return conn.execute(
                f"SELECT * FROM {creation.table} WHERE {creation.key_column}=?", (creation.key,)
            ).fetchall()
        finally:
            conn.close()

    def _detail(self, response) -> str:
        try:
            return str(response.json().get("detail", ""))
        except ValueError:
            return response.text


class ADuplicateThatArrivesDuringTheWriteTests(_IsolatedDB):
    """The three routes, each losing the race it used to answer 500 for."""

    def _race(self, creation: _Creation):
        """POST while a second connection commits the same key between the SELECT and the INSERT."""

        def competitor() -> None:
            conn = models.get_db()
            try:
                conn.execute(creation.rival_sql, creation.rival_row)
                conn.commit()
            finally:
                conn.close()

        opened = models.get_db
        self._racing = _RacingConnection(opened(), competitor)
        return self._post(creation, connection_factory=lambda: self._racing)

    # -- the defect ---------------------------------------------------------------------------

    def test_a_duplicate_that_lands_between_the_lookup_and_the_insert_answers_409(self) -> None:
        """Measured 500 on all three before the handler existed, 409 after it."""
        for creation in CREATIONS:
            with self.subTest(route=creation.label):
                response = self._race(creation)
                self.assertTrue(self._racing.raced, "the competitor never got its turn")
                self.assertEqual(response.status_code, 409, response.text)

    def test_the_lost_race_names_the_conflict_and_not_a_server_fault(self) -> None:
        """The number reattributes the refusal; the sentence has to stay the readable one."""
        for creation in CREATIONS:
            with self.subTest(route=creation.label):
                response = self._race(creation)
                self.assertEqual(self._detail(response), creation.refusal)

    def test_the_caller_that_lost_the_race_writes_nothing(self) -> None:
        """A 409 that had left half a row behind would be a worse answer than the 500."""
        for creation in CREATIONS:
            with self.subTest(route=creation.label):
                self._race(creation)
                rows = self._rows(creation)
                self.assertEqual(len(rows), 1, f"{len(rows)} rows hold {creation.key!r}")
                self.assertEqual(rows[0][creation.witness_column], creation.rival_value)
                self.assertNotEqual(rows[0][creation.witness_column], creation.our_value)

    # -- and what the narrow handler must not swallow -------------------------------------------

    def test_a_write_that_fails_for_another_reason_is_still_our_own_500(self) -> None:
        """The control that keeps the handler from growing back into `except Exception`.

        None of these is a UNIQUE violation, and every one of them reaches the INSERT. A
        handler wide enough to catch them would hand the operator the old lie back: a 409
        sending them to look for a row nobody ever wrote.
        """
        faults = (
            ("a locked database", sqlite3.OperationalError("database is locked")),
            ("a missing column", sqlite3.OperationalError("table has no column named color")),
            ("a full disk", sqlite3.OperationalError("database or disk is full")),
        )
        for creation in CREATIONS:
            for label, error in faults:
                with self.subTest(route=creation.label, fault=label):
                    opened = models.get_db
                    response = self._post(
                        creation,
                        connection_factory=lambda o=opened, e=error: _InsertFails(o(), e),
                    )
                    self.assertEqual(response.status_code, 500, response.text)

    # -- the ordinary duplicate, which is what the lookup is for ---------------------------------

    def test_the_ordinary_duplicate_is_still_refused_by_the_lookup(self) -> None:
        """The other half of the fix: the index is the fallback, not the mechanism.

        Without this, the handler alone would pass every test above while the readable refusal
        quietly moved back into an exception message nobody chose.
        """
        for creation in CREATIONS:
            with self.subTest(route=creation.label):
                self.assertEqual(self._post(creation).status_code, 201)

                opened = models.get_db
                watched = _RacingConnection(opened(), lambda: None)
                response = self._post(creation, connection_factory=lambda w=watched: w)

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self._detail(response), creation.refusal)
                attempted = [s for s in watched.statements if s.lstrip().upper().startswith("INSERT")]
                self.assertEqual(attempted, [], "the lookup let the write through")

    def test_a_key_nobody_holds_is_still_created(self) -> None:
        """The witness: a route that refused everything would satisfy every test above."""
        for creation in CREATIONS:
            with self.subTest(route=creation.label):
                response = self._post(creation)
                self.assertEqual(response.status_code, 201, response.text)
                rows = self._rows(creation)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][creation.witness_column], creation.our_value)


if __name__ == "__main__":
    unittest.main()
