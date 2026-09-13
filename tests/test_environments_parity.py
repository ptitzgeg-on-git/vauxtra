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

import json
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
        return environments_api.add_environment(
            _request(), environments_api.EnvironmentIn(name=name, color=color)
        )


def _update(kind: str, ident: int, name: str, color: str = "blue"):
    if kind == "tags":
        with patch.object(tags_api, "require_auth", _no_auth):
            body = tags_api.TagIn(name=name, color=color)
            return tags_api.update_tag(ident, _request("PUT"), body)
    with patch.object(environments_api, "require_auth", _no_auth):
        return environments_api.update_environment(
            ident, _request("PUT"), environments_api.EnvironmentIn(name=name, color=color)
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


def _logs() -> list[tuple[str, str]]:
    """Every journal line written so far, oldest first, as `(level, message)` pairs."""
    conn = models.get_db()
    try:
        return [
            (r["level"], r["message"])
            for r in conn.execute("SELECT level, message FROM logs ORDER BY id")
        ]
    finally:
        conn.close()


def _hostnames() -> list[str]:
    """Every service still in the base, so a deletion can be shown not to have taken them."""
    conn = models.get_db()
    try:
        return sorted(
            f"{r['subdomain']}.{r['domain']}".strip(".")
            for r in conn.execute("SELECT subdomain, domain FROM services")
        )
    finally:
        conn.close()


def _hold(
    kind: str, label_id: int, hostnames: tuple[str, ...], templates: tuple[str, ...] = ()
) -> None:
    """Give the label its holders: one service per hostname, one template per name.

    A service template names tags and never names an environment (`TemplateIn`,
    `app/api/templates.py`), so `templates` stays empty for that half of the panel. That
    asymmetry is the reason the two journal lines are not one helper, and
    `test_an_environment_never_names_a_template` is what keeps it honest.
    """
    conn = models.get_db()
    try:
        for host in hostnames:
            sub, _, dom = host.partition(".")
            sid = conn.execute(
                "INSERT INTO services (subdomain, domain, target_ip, target_port, enabled) "
                "VALUES (?, ?, '10.0.0.4', 80, 1)",
                (sub, dom),
            ).lastrowid
            if kind == "tags":
                conn.execute(
                    "INSERT INTO service_tags (service_id, tag_id) VALUES (?, ?)",
                    (sid, label_id),
                )
            else:
                conn.execute(
                    "INSERT INTO service_environments (service_id, environment_id) "
                    "VALUES (?, ?)",
                    (sid, label_id),
                )
        for name in templates:
            conn.execute(
                "INSERT INTO service_templates (name, tag_ids_json) VALUES (?, ?)",
                (name, json.dumps([label_id])),
            )
        conn.commit()
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


class DeletionJournalTests(_IsolatedDB):
    """Deleting a label changed rows nobody was looking at and left no trace of it.

    These two were the only destructive routes in the API writing nothing to the journal.
    Services, providers, root domains, Docker endpoints and API keys all report what they
    removed; a tag or an environment unlinked itself from every service carrying it and said
    nothing. And it is the one deletion whose damage cannot be reconstructed afterwards: the
    id is gone from the base, `service_tags` and `service_environments` went with it in the
    cascade, so a bare count would be a number with nowhere left to resolve it. The line
    therefore names what it counted.
    """

    def test_a_label_nothing_holds_is_a_plain_info_line(self) -> None:
        """Nothing changed but the label itself, so there is nothing to warn about."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-unused")
                _delete(kind, created["id"])
                level, message = _logs()[-1]
                self.assertEqual(level, "info")
                self.assertIn(f"{kind}-unused", message)
                self.assertNotIn("--", message)

    def test_a_held_label_warns_and_names_the_services_it_unlinks(self) -> None:
        """A count alone would be unresolvable: the link rows go in the same cascade."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-held")
                _hold(kind, created["id"], (f"api.{kind}.test", f"web.{kind}.test"))
                _delete(kind, created["id"])
                level, message = _logs()[-1]
                self.assertEqual(level, "warning")
                self.assertIn(f"{kind}-held", message)
                self.assertIn("2 services", message)
                self.assertIn(f"api.{kind}.test", message)
                self.assertIn(f"web.{kind}.test", message)

    def test_one_holder_is_said_in_the_singular(self) -> None:
        """What a log line nobody proofreads ends up saying: "1 service(s) carried it"."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-lone")
                _hold(kind, created["id"], (f"only.{kind}.test",))
                _delete(kind, created["id"])
                message = _logs()[-1][1]
                self.assertIn("1 service ", message)
                self.assertIn("keeps working without it", message)
                self.assertNotIn("service(s)", message)

    def test_the_services_are_still_there_afterwards(self) -> None:
        """The witness. A line reporting rows it had deleted would be a different product."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-kept")
                _hold(kind, created["id"], (f"kept.{kind}.test",))
                _delete(kind, created["id"])
                self.assertIsNone(_stored(kind, created["id"]))
                self.assertIn(f"kept.{kind}.test", _hostnames())

    def test_the_names_stop_at_five_and_count_the_rest(self) -> None:
        """Forty hostnames on one line is a line nobody reads, so it names five and counts."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                created = _create(kind, f"{kind}-many")
                hosts = tuple(f"svc{n}.{kind}.test" for n in range(1, 8))
                _hold(kind, created["id"], hosts)
                _delete(kind, created["id"])
                message = _logs()[-1][1]
                self.assertIn("7 services", message)
                self.assertIn("and 2 more", message)
                for named in hosts[:5]:
                    self.assertIn(named, message)
                for unnamed in hosts[5:]:
                    self.assertNotIn(unnamed, message)

    def test_a_tag_gives_its_templates_a_sentence_of_their_own(self) -> None:
        """Two different disappearances, so two sentences rather than one count over both.

        The services are unlinked by the cascade and go on routing. The templates keep the
        dead id until the next read and `_drop_dead_tags` removes it then, so what changes
        there is the next service built from one. Joined into a single list the rarer half is
        also the half "and 3 more" hides, and it is the half nothing else in the product says.
        """
        created = _create("tags", "tags-both")
        _hold("tags", created["id"], ("api.tpl.test",), ("Reverse proxy", "Static site"))
        _delete("tags", created["id"])
        message = _logs()[-1][1]
        self.assertIn("1 service carried it", message)
        self.assertIn("2 service templates named it", message)
        self.assertIn("Reverse proxy", message)
        self.assertIn("Static site", message)
        self.assertIn("starts without the tag", message)

    def test_a_tag_held_only_by_a_template_still_warns(self) -> None:
        """No service carries it, and the next service built from the template still loses it."""
        created = _create("tags", "tags-template-only")
        _hold("tags", created["id"], (), ("Compose stack",))
        _delete("tags", created["id"])
        level, message = _logs()[-1]
        self.assertEqual(level, "warning")
        self.assertIn("1 service template named it", message)
        self.assertNotIn("carried it", message)

    def test_an_environment_never_names_a_template(self) -> None:
        """The asymmetry, stated adversarially: the same id exists on both sides here.

        `tags` and `environments` are separate AUTOINCREMENT tables, so in a fresh base the
        first row of each is id 1, and a template naming tag 1 would be named by an
        environment deletion that scanned `tag_ids_json` the way the tag route has to. It
        does not scan it, because no template names an environment, and a line that said
        otherwise would be telling the operator something untrue about a row that is fine.
        """
        tag = _create("tags", "shared-id-tag")
        env = _create("environments", "shared-id-env")
        self.assertEqual(tag["id"], env["id"])
        _hold("environments", env["id"], ("only.env.test",), ("Reverse proxy",))
        _delete("environments", env["id"])
        message = _logs()[-1][1]
        self.assertIn("only.env.test", message)
        self.assertNotIn("template", message)
        self.assertNotIn("Reverse proxy", message)

if __name__ == "__main__":
    unittest.main()
