"""Where the wire ends is where the blame changes hands.

`GET /api/docker/containers` asks the daemon for its containers and then does about thirty
lines of its own work on the answer: `_extract_container_port` and `_extract_container_ip`
(`app/api/docker.py`), `analyze_container` (`app/services/docker_analyzer.py`), and the row
assembled out of all three. That work used to sit inside the `try` whose handler answers
`502 Failed to list Docker containers: ...`, under a comment claiming the block wrapped the
remote call.

Measured by injecting a fault into each of those helpers in turn: the route answered 502. 502
means "I asked someone else and what came back was not usable", so a `TypeError` of ours
reached the operator as an accusation against their Docker host, next to a sentence sending
them to inspect a daemon that had just answered correctly, on a screen whose only control is
Retry. The bug was ours and both the number and the words hid it.

The `try` now holds the statements that actually put a request on the socket, and nothing
else. There are two of them, not one: `containers.list()`, and the read of `Container.image`,
which docker-py resolves lazily through `client.images.get()` on the same daemon. Everything
in between -- parsing, extraction, analysis -- runs on a dictionary already in this process,
so when it breaks it leaves as an unhandled exception and the route answers 500 with our own
traceback, which is the one number that means "we broke".

Two kinds of test hold that line. The first six drive real faults through the real route and
read the status code. The last class reads the boundary out of the source, so a helper moved
back inside the guard is red the day it is moved, and its positive control proves the reader
can still see one when it is there.
"""

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as _app_db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from app.api import docker as docker_api

_ROOT = Path(__file__).resolve().parents[1]

#: Vauxtra's own code, running on an answer the daemon has already given.
_OURS = frozenset({"_extract_container_port", "_extract_container_ip", "analyze_container"})


# -- one running container, as docker-py hands it over ----------------------------------------


class _Image:
    tags = ["nas:latest"]
    short_id = "sha256:c0ffee"


class _Container:
    id = "c0ffee1234"
    name = "nas"
    status = "running"
    image = _Image()
    attrs = {
        "Config": {"Labels": {}, "ExposedPorts": {"8080/tcp": {}}},
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "192.168.1.20"}}},
    }


class _ContainerWhoseImageIsRefused(_Container):
    """`Container.image` is a lazy `client.images.get()`, so it can be refused on its own."""

    @property
    def image(self):
        raise OSError("connection reset by peer")


class _Containers:
    def __init__(self, outcome):
        self._outcome = outcome

    def list(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _Daemon:
    """Answers `ping`, then lists or breaks as `outcome` says."""

    def __init__(self, outcome):
        self.containers = _Containers(outcome)

    def ping(self):
        return True


def _our_bug(*_args, **_kwargs):
    """The shape of a defect in code that never leaves the process."""
    raise TypeError("'NoneType' object is not subscriptable")


class AFaultOfOursIsNotTheDaemonsFaultTests(unittest.TestCase):
    """`GET /api/docker/containers`, asked through the application so the code is the real one."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "boundary.test.db")
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

    def _list(self, daemon, injected: str | None = None):
        """Ask the route for its containers, with `injected` replaced by a fault of ours."""
        patches = [patch.object(docker_api, "_docker_client", lambda _host=None: daemon)]
        if injected is not None:
            patches.append(patch.object(docker_api, injected, _our_bug))
        for patcher in patches:
            patcher.start()
        try:
            with TestClient(app_main.app, raise_server_exceptions=False) as client:
                return client.get("/api/docker/containers")
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    # -- our own code, which is a 500 ---------------------------------------------------------

    def test_a_fault_in_our_port_extraction_is_our_500_not_the_daemons_502(self) -> None:
        """`_extract_container_port` reads a dictionary. Nothing it does can be the daemon's fault."""
        self.assertEqual(
            self._list(_Daemon([_Container()]), "_extract_container_port").status_code, 500
        )

    def test_a_fault_in_our_ip_extraction_is_our_500_not_the_daemons_502(self) -> None:
        self.assertEqual(
            self._list(_Daemon([_Container()]), "_extract_container_ip").status_code, 500
        )

    def test_a_fault_in_our_suggestion_analysis_is_our_500_not_the_daemons_502(self) -> None:
        """`analyze_container` lives in `app/services/docker_analyzer.py` and reaches nobody."""
        self.assertEqual(
            self._list(_Daemon([_Container()]), "analyze_container").status_code, 500
        )

    def test_our_500_does_not_send_the_operator_to_look_at_their_daemon(self) -> None:
        """The number is half the answer; the sentence that named their host was the other half."""
        response = self._list(_Daemon([_Container()]), "_extract_container_port")
        self.assertNotIn("Failed to list Docker containers", response.text)

    # -- the daemon, which is a 502 -------------------------------------------------------------

    def test_a_daemon_that_refuses_the_listing_is_still_the_daemons_502(self) -> None:
        """The control without which narrowing the guard would just be deleting it."""
        response = self._list(_Daemon(OSError("connection reset by peer")))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("Failed to list Docker containers", response.text)

    def test_a_daemon_that_refuses_the_image_lookup_is_still_the_daemons_502(self) -> None:
        """The second statement that reaches the socket, and the reason the guard is not one line.

        docker-py resolves `Container.image` on demand, so this read is a second round trip. A
        boundary drawn around `containers.list()` alone would have answered 500 for a daemon
        that dropped the connection between two of its own calls.
        """
        response = self._list(_Daemon([_ContainerWhoseImageIsRefused()]))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("nas", response.text)

    # -- and the healthy case -------------------------------------------------------------------

    def test_a_reachable_daemon_still_lists_its_containers(self) -> None:
        """Green before the change and green after it: a boundary, not a new failure."""
        response = self._list(_Daemon([_Container()]))
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([row["name"] for row in rows], ["nas"])
        self.assertEqual(rows[0]["target_ip"], "192.168.1.20")
        self.assertEqual(rows[0]["target_port"], 8080)
        self.assertEqual(rows[0]["image"], "nas:latest")


# -- the boundary, read out of the source ------------------------------------------------------


def _answers_502(handler: ast.ExceptHandler) -> bool:
    """A handler that hands the failure to somebody else."""
    for node in ast.walk(handler):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "HTTPException":
            continue
        first = node.exc.args[0] if node.exc.args else None
        if isinstance(first, ast.Constant) and first.value == 502:
            return True
        if any(
            kw.arg == "status_code" and isinstance(kw.value, ast.Constant) and kw.value.value == 502
            for kw in node.exc.keywords
        ):
            return True
    return False


def _guarded_by_502(source: str, function_name: str) -> set[str]:
    """Every name called inside a `try` of `function_name` whose handler answers 502."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != function_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Try) or not any(
                _answers_502(handler) for handler in inner.handlers
            ):
                continue
            for statement in inner.body:
                for sub in ast.walk(statement):
                    if isinstance(sub, ast.Call):
                        called = sub.func
                        found.add(
                            called.id
                            if isinstance(called, ast.Name)
                            else getattr(called, "attr", "")
                        )
    return found


#: What the route looked like when the faults were measured: our helper under the daemon's number.
_THE_SHAPE_THAT_WAS_MEASURED = '''
def list_docker_containers(request):
    client = _docker_client(host)
    containers = []
    try:
        for c in client.containers.list():
            port = _extract_container_port(c.attrs)
            containers.append(analyze_container({}, c.name, port))
    except Exception as e:
        raise HTTPException(502, f"Failed to list Docker containers: {e}")
    return containers
'''


class TheGuardCoversTheWireAndNothingElseTests(unittest.TestCase):
    """A behavioural test proves today's code. This one refuses the next widening."""

    def setUp(self) -> None:
        self.source = (_ROOT / "app/api/docker.py").read_text(encoding="utf-8")

    def test_no_code_of_ours_sits_under_the_daemons_number(self) -> None:
        guarded = _guarded_by_502(self.source, "list_docker_containers")
        self.assertEqual(sorted(guarded & _OURS), [])

    def test_the_call_that_reaches_the_daemon_is_still_guarded(self) -> None:
        """Without this, deleting the `try` altogether would pass the test above."""
        self.assertIn("list", _guarded_by_502(self.source, "list_docker_containers"))

    def test_it_would_catch_one(self) -> None:
        """The positive control: a reader that matches nothing passes the first test for free."""
        guarded = _guarded_by_502(_THE_SHAPE_THAT_WAS_MEASURED, "list_docker_containers")
        self.assertEqual(
            sorted(guarded & _OURS), ["_extract_container_port", "analyze_container"]
        )


if __name__ == "__main__":
    unittest.main()
