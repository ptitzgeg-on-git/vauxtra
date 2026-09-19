"""Four things can happen to a ticked container, and the answer has to tell them apart.

`POST /api/docker/import` answered `{"imported": n, "skipped": n, "errors": [...]}` with
`skipped` a single integer standing for two outcomes that have nothing in common: a
container Vauxtra already tracks under that name, which is the ordinary result of ticking a
whole page, and a container this route *refused* -- no address, or no usable port -- which
is the one thing the operator has to go and fix. Neither was named, and neither reached the
journal, so a run that dropped five of six selected containers answered "5 skipped" and left
no record anywhere of which five or why.

`/api/services/import` had already been taken through this, so the tests here hold the
Docker import to the same contract: `skipped` and `errors` are lists of sentences, a refusal
names the container Docker named, a refusal is written to the journal, and rows passed over
on purpose get one line for the run rather than one line each.

The refusals are driven with real bodies rather than injected faults -- a container with no
address is what a `network_mode: host` container looks like coming out of discovery, and a
container with no published port is most of a `docker-compose` file -- with one exception:
`test_a_row_that_breaks_mid_import_is_named_too` reaches the `except` clause by writing the
same subdomain twice in one payload, which is the shape the operator produces by ticking two
containers that sanitise to one name.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import docker as docker_api


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/docker/import", "headers": []})


def _container(name: str, **over) -> dict:
    """A row shaped like one of `GET /api/docker/containers`, valid unless overridden."""
    row = {
        "id": f"id-{name}",
        "name": name,
        "subdomain": name,
        "target_ip": "10.0.0.7",
        "target_port": 8080,
        "forward_scheme": "http",
        "websocket": False,
    }
    row.update(over)
    return row


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "dockerimport.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()
        patcher = patch.object(docker_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _import(self, containers: list[dict], domain: str = "example.com") -> dict:
        return docker_api.import_docker_containers(
            _request(), {"domain": domain, "containers": containers}
        )

    def _subdomains(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT subdomain FROM services ORDER BY subdomain").fetchall()
        conn.close()
        return [r["subdomain"] for r in rows]

    def _logs(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT level, message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [f"{r['level']}: {r['message']}" for r in rows]


class AContainerWithNothingToPointAtIsRefusedTests(_IsolatedDB):
    """No address and no port are refusals, not rows passed over: nothing was created."""

    def test_a_container_with_no_address_is_named_in_errors(self) -> None:
        result = self._import([_container("grafana", target_ip="")])
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(len(result["errors"]), 1, result["errors"])
        self.assertIn("grafana", result["errors"][0])
        self.assertIn("no address", result["errors"][0])

    def test_a_container_with_no_port_is_named_in_errors(self) -> None:
        result = self._import([_container("grafana", target_port=0)])
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 1, result["errors"])
        self.assertIn("grafana", result["errors"][0])
        self.assertIn("port", result["errors"][0])

    def test_a_refusal_is_not_counted_as_a_row_passed_over(self) -> None:
        """The whole point. Both used to land on the same integer, under the same word."""
        result = self._import([_container("grafana", target_ip="")])
        self.assertEqual(result["skipped"], [])
        self.assertTrue(result["errors"])

    def test_a_refusal_reaches_the_journal(self) -> None:
        self._import([_container("grafana", target_ip="")])
        lines = [line for line in self._logs() if "grafana" in line]
        self.assertEqual(len(lines), 1, self._logs())
        self.assertIn("warning", lines[0])

    def test_the_refused_container_is_not_written(self) -> None:
        self._import([_container("grafana", target_ip="")])
        self.assertEqual(self._subdomains(), [])

    def test_a_container_docker_gave_no_name_is_still_named_something(self) -> None:
        """`name` is what the sentence carries, so an empty one must not produce
        `Import skipped : ...` with a hole where the container should be."""
        nameless = {**_container("x", subdomain="", target_ip=""), "name": ""}
        result = self._import([nameless])
        self.assertEqual(len(result["errors"]), 1, result["errors"])
        self.assertIn("unnamed container", result["errors"][0])


class AContainerAlreadyTrackedIsPassedOverTests(_IsolatedDB):
    """The nominal case of ticking a whole page. Nothing is wrong, so nothing is red."""

    def _tracked(self) -> None:
        self._import([_container("grafana")])

    def test_the_second_run_passes_it_over_rather_than_refusing_it(self) -> None:
        self._tracked()
        result = self._import([_container("grafana")])
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["skipped"]), 1, result["skipped"])
        self.assertIn("grafana", result["skipped"][0])

    def test_the_sentence_says_which_name_is_already_tracked(self) -> None:
        self._tracked()
        result = self._import([_container("grafana")])
        self.assertIn("grafana.example.com", result["skipped"][0])

    def test_the_run_gets_one_line_and_the_rows_get_none(self) -> None:
        """`check_all` settled this: one line for the run, not one per row. Twenty tracked
        containers would otherwise bury the rest of Recent activity on every click."""
        self._import([_container(f"svc{n}") for n in range(4)])
        before = len(self._logs())
        self._import([_container(f"svc{n}") for n in range(4)])
        added = self._logs()[before:]
        self.assertEqual(len(added), 1, added)
        self.assertIn("passed over 4 containers", added[0])
        self.assertIn("info", added[0])

    def test_nothing_is_logged_for_a_run_that_passed_nothing_over(self) -> None:
        before = len(self._logs())
        self._import([_container("grafana")])
        self.assertEqual(
            [line for line in self._logs()[before:] if "passed over" in line], []
        )


class OneRunHoldsSeveralOutcomesTests(_IsolatedDB):
    """The four are not exclusive, which is why they are four fields and not one verdict."""

    def test_an_import_a_refusal_and_a_row_passed_over_in_a_single_run(self) -> None:
        self._import([_container("grafana")])
        result = self._import(
            [
                _container("grafana"),
                _container("prometheus"),
                _container("loki", target_port=0),
            ]
        )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["skipped"]), 1, result["skipped"])
        self.assertEqual(len(result["errors"]), 1, result["errors"])
        self.assertIn("grafana", result["skipped"][0])
        self.assertIn("loki", result["errors"][0])
        self.assertEqual(self._subdomains(), ["grafana", "prometheus"])

    def test_a_refusal_does_not_stop_the_containers_after_it(self) -> None:
        result = self._import(
            [_container("loki", target_ip=""), _container("prometheus")]
        )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self._subdomains(), ["prometheus"])

    def test_two_containers_that_sanitise_to_one_name_do_not_reach_the_exception(self) -> None:
        """`services` carries a UNIQUE index on (subdomain, domain), so the obvious guess is
        that the second INSERT raises. It does not: the first is visible to the lookup on the
        next turn of the loop through the same connection, before any commit. So this is the
        ordinary already-tracked outcome, and the comment in the route says so."""
        result = self._import([_container("grafana"), _container("Grafana!")])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["skipped"]), 1, result["skipped"])
        self.assertIn("Grafana!", result["skipped"][0])
        self.assertEqual(self._subdomains(), ["grafana"])

    def test_a_row_that_breaks_mid_import_is_named_too(self) -> None:
        """A port the panel passed through as text. `int()` raises, and `str(e)` alone says
        `invalid literal for int() with base 10: \'eighty\'` -- which names neither the
        container nor anything to do about it."""
        result = self._import(
            [_container("grafana"), _container("loki", target_port="eighty")]
        )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["errors"]), 1, result["errors"])
        self.assertIn("loki", result["errors"][0])
        self.assertIn("ValueError", result["errors"][0])

    def test_the_rows_that_did_import_are_kept_when_another_one_breaks(self) -> None:
        """The commit is after the loop, so a row refused mid-run must not take the good
        ones with it. Read back out of SQLite rather than off the response."""
        self._import([_container("grafana"), _container("loki", target_port="eighty")])
        self.assertEqual(self._subdomains(), ["grafana"])


class TheTwoImportsAnswerInTheSameShapeTests(_IsolatedDB):
    """Read as a pair on purpose: the two routes drifting apart is the defect itself.

    Both are "take an inventory somebody else holds and make services out of it", and the
    only difference between their answers should be that the service import can also *link*
    a record to a service that already exists, which has no Docker equivalent.
    """

    def test_both_routes_report_through_the_same_two_functions(self) -> None:
        """By identity, not by resemblance. Two routes that merely *look* alike drift; two
        routes holding the same function object cannot, which is the whole reason
        `app/importing.py` exists rather than a second copy beside each route."""
        import app.importing as importing
        from app.api import sync as sync_api

        for name, module in (
            ("/api/docker/import", docker_api),
            ("/api/services/import", sync_api),
        ):
            with self.subTest(route=name):
                self.assertIs(module.refuse_import, importing.refuse_import)
                self.assertIs(module.set_aside, importing.set_aside)

    def test_both_routes_answer_with_skipped_and_errors_as_lists(self) -> None:
        """Driven, not read. An empty inventory is the one payload both accept unchanged."""
        for name, answer in (
            ("/api/docker/import", self._docker_answer()),
            ("/api/services/import", self._services_answer()),
        ):
            with self.subTest(route=name):
                self.assertIsInstance(answer["skipped"], list, answer)
                self.assertIsInstance(answer["errors"], list, answer)
                self.assertIsInstance(answer["imported"], int, answer)

    def _docker_answer(self) -> dict:
        return self._import([_container("grafana")])

    def _services_answer(self) -> dict:
        from app.api import sync as sync_api

        with patch.object(sync_api, "require_auth_or_setup", lambda _req, scope=None: None):
            return sync_api.import_services(
                _request(), {"dns_rewrites": [], "proxy_hosts": []}
            )


if __name__ == "__main__":
    unittest.main()
