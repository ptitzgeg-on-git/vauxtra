"""An answer that names a cause the route never checked.

Two habits, one shape. Both were measured on a running instance before anything was changed.

`DELETE /api/webhooks/999999`, on a database holding no webhook 999999, answered
`200 {"ok": true}`: a receipt for a deletion that never happened. `PUT /api/webhooks/999999`
answered 404 for that same missing row, so the two verbs disagreed about whether the id was
real, and `vauxtra_mcp/tools/admin.py::delete_webhook` handed the `ok` back to its own caller
as proof the webhook was gone. Nine of the eleven delete routes already looked before they
wrote; this was one of the two that did not.

`DELETE /api/domains/absent.test` was the other, and it failed twice over. It never looked,
so it signed the same receipt for nothing; and it put the raw path segment into the DELETE
while `POST /api/domains` stores `normalize_domain(...)`, so `Example.test` matched nothing
on an instance holding `example.test` and said `ok` for that too. Both ends are measured
below, and the sweep now demands 404 from all eleven with no waiver.

`POST /api/docker/endpoints` wrapped its INSERT in a bare `except Exception` that raised
`409 Docker endpoint host already exists`. The handler had no way to know that was true: a
locked database, a column a half-applied migration never added, and a full disk all reached
the operator as that same sentence, sending them looking for a row that was never written.
`app/api/environments.py` had the identical bug and answers it by asking for the duplicate
before writing; this file measures that `app/api/docker.py` now asks the same way.

The first class puts the question to every delete route the application publishes, read off
its own OpenAPI schema rather than off a list typed here, so a delete route written next
year without a lookup is red the day it is written.
"""

import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.auth as auth
import app.db as _app_db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from app.api import docker as docker_api
from app.api import settings as settings_api
from app.api import webhooks as webhooks_api

_ROOT = Path(__file__).resolve().parents[1]

# There is no waiver list here any more. `DELETE /api/domains/{name}` was the one route this
# sweep used to excuse, and it now looks its row up like the other ten, so the assertion is
# an equality: a twelfth delete route written without a lookup is red the day it is written.


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _no_auth(_req, scope=None):
    """Stand in for `require_auth`, which is not what any of this measures."""
    return None


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "non.mesuree.test.db")
        models.init_db()

    def tearDown(self) -> None:
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()


# -- Every delete route, asked the same question --------------------------------------------


class EveryDeleteRouteAnswersForARowThatIsNotThereTests(_IsolatedDB):
    """Walk the application's own schema; do not retype its routes here.

    A list written by hand covers what its author remembered. This one is read from
    `app.openapi()`, so a route added to any router is swept the moment it exists.
    """

    #: A path parameter is filled with a value no fresh instance can hold.
    MISSING_INT = "999999"
    MISSING_STR = "absent-from-this-instance"

    def setUp(self) -> None:
        super().setUp()
        for target, attribute, value in (
            (auth, "APP_PASSWORD", ""),
            (scheduler, "start", lambda interval_minutes=0: None),
            (scheduler, "configure", lambda interval_minutes=0: None),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()
        self.addCleanup(self._client_cm.__exit__, None, None, None)

    def _delete_paths(self) -> dict[str, dict[str, str]]:
        """Every DELETE the app publishes, with the declared type of each path parameter."""
        found = {}
        for path, operations in app_main.app.openapi()["paths"].items():
            operation = operations.get("delete")
            if operation is None:
                continue
            found[path] = {
                parameter["name"]: parameter.get("schema", {}).get("type", "string")
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "path"
            }
        return found

    def _url_for(self, path: str, types: dict[str, str]) -> str:
        out = path
        while "{" in out:
            start = out.index("{")
            end = out.index("}", start)
            name = out[start + 1 : end]
            filler = self.MISSING_INT if types.get(name) == "integer" else self.MISSING_STR
            out = out[:start] + filler + out[end + 1 :]
        return out

    def _sweep(self) -> dict[str, tuple[int, str]]:
        """The status and the detail each delete route gives for an id that does not exist."""
        answers = {}
        for path, types in self._delete_paths().items():
            response = self.client.delete(self._url_for(path, types))
            detail = ""
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                detail = str(body.get("detail", body))
            answers[path] = (response.status_code, detail)
        return answers

    def test_no_delete_route_reports_success_for_a_row_that_is_not_there(self) -> None:
        answers = self._sweep()
        wrong = {path: answer for path, answer in answers.items() if answer[0] != 404}
        self.assertEqual(
            wrong,
            {},
            f"a delete route answered for a row it never found: {wrong}",
        )

    def test_the_sweep_sees_every_delete_route_the_source_declares(self) -> None:
        """The control: a sweep that walks nothing passes the assertion above for free."""
        declared = sum(
            len(re.findall(r"@router\.delete\(", path.read_text(encoding="utf-8")))
            for path in sorted((_ROOT / "app" / "api").glob("*.py"))
        )
        self.assertGreater(declared, 0)
        self.assertEqual(len(self._sweep()), declared)

    def test_every_404_is_the_route_speaking_and_not_an_unmatched_path(self) -> None:
        """The other control: a URL built wrong would answer 404 too, and read as a pass.

        Starlette answers an unmatched path with exactly `{"detail": "Not Found"}`. A route
        that looked and found nothing says which thing it looked for.
        """
        for path, (status, detail) in sorted(self._sweep().items()):
            if status != 404:
                continue
            with self.subTest(route=path):
                self.assertNotEqual(detail, "Not Found")
                self.assertIn("not found", detail.lower())


# -- The webhook that was the eleventh --------------------------------------------------------


class DeletingAWebhookTests(_IsolatedDB):
    """`DELETE /api/webhooks/{wid}`, the route that answered `ok` over nothing."""

    URL = "json://collector.lan/hook"

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(webhooks_api, "require_auth", _no_auth)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _store(self, name: str = "collector") -> int:
        conn = models.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO webhooks (name, url, enabled) VALUES (?,?,1)", (name, self.URL)
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _rows(self, table: str) -> int:
        conn = models.get_db()
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

    def test_deleting_a_webhook_that_is_not_there_answers_404(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            webhooks_api.delete_webhook(999999, _request("DELETE"))
        self.assertEqual(caught.exception.status_code, 404)

    def test_the_two_verbs_agree_about_a_missing_row(self) -> None:
        """`update_webhook` already answered 404 here. Half a rule is not a rule."""
        statuses = {}
        for verb, call in (
            ("PUT", lambda: webhooks_api.update_webhook(
                999999, _request("PUT"), webhooks_api.WebhookUpdateIn(enabled=0)
            )),
            ("DELETE", lambda: webhooks_api.delete_webhook(999999, _request("DELETE"))),
        ):
            with self.assertRaises(HTTPException) as caught:
                call()
            statuses[verb] = caught.exception.status_code
        self.assertEqual(statuses, {"PUT": 404, "DELETE": 404})

    def test_a_webhook_that_is_there_is_still_deleted(self) -> None:
        """The witness: green before this change and green after it."""
        wid = self._store()
        self.assertEqual(webhooks_api.delete_webhook(wid, _request("DELETE")), {"ok": True})
        self.assertEqual(self._rows("webhooks"), 0)

    def test_deleting_a_webhook_still_takes_its_queued_sends_with_it(self) -> None:
        """The second witness, for the cascade the route leans on instead of a second DELETE."""
        wid = self._store()
        conn = models.get_db()
        try:
            conn.execute(
                """INSERT INTO webhook_delivery_log
                       (webhook_id, url, title, body, status, attempt, next_retry_at)
                   VALUES (?,?,?,?,'pending',1,datetime('now','-1 hour'))""",
                (wid, self.URL, "Vauxtra: service DOWN", "nas.example.com"),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._rows("webhook_delivery_log"), 1)

        webhooks_api.delete_webhook(wid, _request("DELETE"))
        self.assertEqual(self._rows("webhook_delivery_log"), 0)


# -- The domain that was the other one --------------------------------------------------------


class DeletingADomainTests(_IsolatedDB):
    """`DELETE /api/domains/{name}`: the receipt for nothing, and the name it never matched.

    `domains.name` is a TEXT primary key, and `services.domain` and
    `service_templates.domain` hold one of those names as plain text with no REFERENCES
    declared. So the deletion refuses nothing and breaks nothing, and the assertions below
    say so in both directions: the row goes, and every row built on the name stays exactly
    where it was.
    """

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(settings_api, "require_auth", _no_auth)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add(self, name: str) -> str:
        return settings_api.add_domain(_request("POST"), settings_api.DomainIn(name=name))["name"]

    def _domains(self) -> list[str]:
        return list(settings_api.list_domains(_request("GET")))

    def _service(self, subdomain: str, domain: str) -> None:
        conn = models.get_db()
        try:
            conn.execute(
                """INSERT INTO services (subdomain, domain, target_ip, target_port)
                   VALUES (?,?,'10.0.0.9',8080)""",
                (subdomain, domain),
            )
            conn.commit()
        finally:
            conn.close()

    def _template(self, name: str, domain: str) -> None:
        conn = models.get_db()
        try:
            conn.execute(
                "INSERT INTO service_templates (name, domain, target_port) VALUES (?,?,8080)",
                (name, domain),
            )
            conn.commit()
        finally:
            conn.close()

    def _journal(self) -> list[tuple[str, str]]:
        conn = models.get_db()
        try:
            return [
                (r["level"], r["message"])
                for r in conn.execute("SELECT level, message FROM logs ORDER BY id")
            ]
        finally:
            conn.close()

    def _count(self, table: str) -> int:
        conn = models.get_db()
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

    def test_deleting_a_domain_that_is_not_there_answers_404(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            settings_api.delete_domain("never-existed.test", _request("DELETE"))
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "Domain not found")

    def test_it_answers_404_without_writing_a_line_about_a_deletion(self) -> None:
        """A journal that records removals that did not happen is worse than a silent one."""
        with self.assertRaises(HTTPException):
            settings_api.delete_domain("never-existed.test", _request("DELETE"))
        self.assertEqual(self._journal(), [])

    def test_a_domain_that_is_there_is_still_deleted(self) -> None:
        """The witness: green before this change and green after it."""
        self._add("example.test")
        self.assertEqual(settings_api.delete_domain("example.test", _request("DELETE")), {"ok": True})
        self.assertEqual(self._domains(), [])

    def test_the_name_is_normalised_the_way_it_was_stored(self) -> None:
        """`add_domain` stores `normalize_domain(...)`; this route used the raw segment.

        On an instance holding `example.test`, `DELETE /api/domains/Example.TEST` matched no
        row and answered `{"ok": true}`. The list still held the domain, and nothing said so.
        """
        self.assertEqual(self._add("  Example.TEST.  "), "example.test")
        self.assertEqual(self._domains(), ["example.test"])
        self.assertEqual(settings_api.delete_domain("Example.TEST", _request("DELETE")), {"ok": True})
        self.assertEqual(self._domains(), [])

    def test_a_trailing_dot_names_the_same_domain(self) -> None:
        self._add("example.test")
        self.assertEqual(settings_api.delete_domain("example.test.", _request("DELETE")), {"ok": True})
        self.assertEqual(self._domains(), [])

    def test_removing_an_unused_domain_is_one_plain_line(self) -> None:
        self._add("unused.test")
        settings_api.delete_domain("unused.test", _request("DELETE"))
        self.assertEqual(self._journal(), [("info", "Domain deleted: unused.test")])

    def test_the_line_names_the_services_that_keep_working(self) -> None:
        self._add("example.test")
        self._service("nas", "example.test")
        self._service("git", "example.test")
        settings_api.delete_domain("example.test", _request("DELETE"))

        (level, message), = self._journal()
        self.assertEqual(level, "warning")
        self.assertIn("2 services still use the name and keep working", message)
        self.assertIn("git.example.test", message)
        self.assertIn("nas.example.test", message)

    def test_a_domain_only_a_template_holds_is_not_silent(self) -> None:
        """The hole the DNS tab had: it counted services and nothing else.

        A root domain no service used but a template did showed the neutral "0 services"
        badge and got the plain "Delete domain?" question, so the one thing still built on
        the name was the one thing never mentioned.
        """
        self._add("tpl-only.test")
        self._template("mon modele", "tpl-only.test")
        settings_api.delete_domain("tpl-only.test", _request("DELETE"))

        (level, message), = self._journal()
        self.assertEqual(level, "warning")
        self.assertIn("1 service template still uses the name and keeps working", message)
        self.assertIn("mon modele", message)

    def test_both_kinds_of_holder_are_counted_together(self) -> None:
        self._add("example.test")
        self._service("nas", "example.test")
        self._template("mon modele", "example.test")
        settings_api.delete_domain("example.test", _request("DELETE"))

        (_level, message), = self._journal()
        self.assertIn("1 service and 1 service template still use the name", message)

    def test_an_apex_route_is_not_named_with_a_leading_dot(self) -> None:
        """`subdomain` is empty for an apex route, and the naive join says `.example.test`."""
        self._add("example.test")
        self._service("", "example.test")
        settings_api.delete_domain("example.test", _request("DELETE"))

        (_level, message), = self._journal()
        self.assertIn("(example.test)", message)
        self.assertNotIn("(.example.test)", message)

    def test_past_five_holders_the_line_counts_instead_of_listing(self) -> None:
        self._add("example.test")
        for n in range(7):
            self._service(f"svc{n}", "example.test")
        settings_api.delete_domain("example.test", _request("DELETE"))

        (_level, message), = self._journal()
        self.assertIn("7 services still use the name", message)
        self.assertIn("and 2 more", message)
        self.assertNotIn("svc6", message)

    def test_nothing_built_on_the_name_is_touched(self) -> None:
        """The deletion refuses nothing and takes nothing with it, and that is the point.

        `services.domain` and `service_templates.domain` declare no reference, so there is
        no cascade to fire and nothing to blank. The rows keep the name and keep routing --
        which is what the dialog was rewritten to say, after years of warning that existing
        routes might break.
        """
        self._add("example.test")
        self._service("nas", "example.test")
        self._template("mon modele", "example.test")
        settings_api.delete_domain("example.test", _request("DELETE"))

        self.assertEqual(self._domains(), [])
        self.assertEqual(self._count("services"), 1)
        self.assertEqual(self._count("service_templates"), 1)
        conn = models.get_db()
        try:
            self.assertEqual(
                conn.execute("SELECT domain FROM services").fetchone()["domain"], "example.test"
            )
            self.assertEqual(
                conn.execute("SELECT domain FROM service_templates").fetchone()["domain"],
                "example.test",
            )
        finally:
            conn.close()

    def test_a_neighbouring_domain_keeps_its_own_holders_out_of_the_line(self) -> None:
        """The control for the two SELECTs: they filter on the name, not on the table."""
        self._add("example.test")
        self._add("other.test")
        self._service("nas", "example.test")
        self._service("git", "other.test")
        self._template("modele autre", "other.test")
        settings_api.delete_domain("example.test", _request("DELETE"))

        (_level, message), = self._journal()
        self.assertIn("1 service still uses the name", message)
        self.assertIn("nas.example.test", message)
        self.assertNotIn("git.other.test", message)
        self.assertNotIn("modele autre", message)
        self.assertEqual(self._domains(), ["other.test"])




class _InsertFails:
    """A connection that reads normally and refuses to insert, the way a locked base does.

    Only the INSERT fails, so a route that looks before it writes still gets its answer from
    the real table and trips only on the statement that actually stores something.
    """

    def __init__(self, conn: sqlite3.Connection, error: Exception) -> None:
        self._conn = conn
        self._error = error

    def execute(self, sql: str, *args):
        if sql.lstrip().upper().startswith("INSERT"):
            raise self._error
        return self._conn.execute(sql, *args)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class AddingADockerEndpointTests(_IsolatedDB):
    """`POST /api/docker/endpoints`, and the three faults it called a duplicate."""

    HOST = "tcp://192.168.1.10:2375"

    #: Not one of them is a UNIQUE violation, and every one of them reached the INSERT.
    NOT_DUPLICATES = (
        ("a locked database", sqlite3.OperationalError("database is locked")),
        ("a missing column", sqlite3.OperationalError("table docker_endpoints has no column x")),
        ("a full disk", sqlite3.OperationalError("database or disk is full")),
    )

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(docker_api, "require_auth", _no_auth)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add(self, name: str = "lab", host: str | None = None):
        body = docker_api.DockerEndpointIn(name=name, docker_host=host or self.HOST)
        return docker_api.add_docker_endpoint(_request(), body)

    def test_a_write_that_fails_for_another_reason_is_not_called_a_duplicate(self) -> None:
        for label, error in self.NOT_DUPLICATES:
            with self.subTest(fault=label):
                with patch.object(
                    docker_api, "get_db", lambda e=error: _InsertFails(models.get_db(), e)
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        self._add()

    def test_a_host_that_is_already_stored_is_still_a_409(self) -> None:
        """The witness for the other side: the sentence has to stay true when it is true."""
        self._add()
        with self.assertRaises(HTTPException) as caught:
            self._add(name="lab-again")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("already exists", str(caught.exception.detail))

    def test_a_host_nobody_stored_yet_is_still_written(self) -> None:
        """The witness: a route that refused everything would satisfy the test above."""
        created = self._add(host="tcp://192.168.1.11:2375")
        self.assertEqual(created["docker_host"], "tcp://192.168.1.11:2375")

        conn = models.get_db()
        try:
            stored = conn.execute(
                "SELECT name FROM docker_endpoints WHERE docker_host=?",
                ("tcp://192.168.1.11:2375",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(stored["name"], "lab")

    def test_two_endpoints_may_still_share_a_name(self) -> None:
        """`docker_host` is the UNIQUE column of `docker_endpoints`, and `name` is not."""
        self._add(name="lab", host="tcp://192.168.1.11:2375")
        self._add(name="lab", host="tcp://192.168.1.12:2375")

        conn = models.get_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM docker_endpoints WHERE name='lab'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
