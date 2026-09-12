"""Every route behind the `write` scope, driven from both sides, with no list to maintain.

`tests/test_api_key_scopes.py` already drives a write key, through five routes written out
by hand plus the single-service check. The application has forty-five. The measurement that
opened this file: a key whose stored scope column read `'read, write '` went from 403 to 200
on forty-two of them when segment stripping was added, and the five hand-written routes are
the only ones any test would have noticed. Whole categories -- creating a service, deleting
a provider, bulk-disabling everything, importing from Docker -- moved without a single
assertion watching.

So the list is not written here. `_write_routes()` walks the application's own router and
keeps every route whose gate asks for `write`; a route added next month is swept the day it
is added, by nobody remembering anything. `WriteRouteInventory` is the witness for that
walk: a sweep over an empty list passes every assertion in this file, so the walk is checked
before it is trusted. It has been empty before -- FastAPI 0.141 stopped copying an included
router's routes into `app.routes` and leaves a wrapper there instead, which is why the walk
below follows `original_router` rather than reading `app.routes`.

The two directions are driven differently, on purpose.

The refusal is a plain request: the gate raises before the body runs, so the route does
exactly what it does in production and no route code executes. The acceptance cannot be: a
write key on `POST /api/docker/import` really would import, and on `POST /api/providers/1/
test` really would open a socket. `_gate_only()` therefore lets the real gate decide and
raises immediately after it returns, so what is measured is the decision and nothing past
it. `test_the_stop_after_the_gate_decides_nothing_itself` is the witness for that stop.

`TheBulkRouteIsDrivenForReal` is the end of the rope: no stop, no patching, a service row
that is enabled before the request and disabled after it. `POST /api/services/bulk` with
`{"action": "delete"}` is the most destructive thing an API key can reach, and the row it
moves is read back out of SQLite rather than out of the response body.
"""

import contextlib
import functools
import hashlib
import inspect
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app.auth as auth
import app.db as app_db
import app.scheduler as scheduler
from app import models

PASSWORD = "test-password-long-enough"
REPO_ROOT = Path(__file__).resolve().parent.parent

# `require_auth(request, scope="admin")`: above `write`, so a write key must still be turned
# away here. Without it, a guard that granted everything to everyone would pass this file.
ADMIN_ROUTE = "/api/settings/api-keys"

_SCOPE_CALL = re.compile(r"require_auth(?:_or_setup)?\(\s*request\s*,\s*scope=[\"'](\w+)[\"']")
_PATH_PARAM = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")

# A body has to survive the model before the gate is reached: FastAPI validates the request
# body first, and a 422 says nothing about a scope. These are the fields whose own
# validators refuse a generic string -- a provider type, an IP, a Docker socket. Keyed by
# field name rather than by route, so a new route reusing a known model needs nothing here.
# A field this table does not cover shows up as a 422 in the sweep below, with a message
# naming the field to add.
_FIELD_SAMPLES = {
    "type": "npm",
    "target_ip": "127.0.0.1",
    "docker_host": "unix:///var/run/docker.sock",
}


class _GateOpened(Exception):
    """Raised the instant the real gate returns, so no route body ever runs."""


def _walk_routes(router, prefix=""):
    """Every `APIRoute` reachable from `router`, through the included-router wrappers."""
    for route in getattr(router, "routes", []):
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            yield from _walk_routes(original, prefix + (getattr(context, "prefix", "") or ""))
            continue
        if isinstance(route, APIRoute):
            yield prefix + route.path, route


def _sample(name, schema, defs):
    """A value the model will accept for one property of a request body."""
    while "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in schema:
            options = [o for o in schema[combinator] if o.get("type") != "null"]
            return _sample(name, options[0] if options else schema[combinator][0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "string":
        return _FIELD_SAMPLES.get(name, "x")
    if kind == "integer":
        return _FIELD_SAMPLES.get(name, 1)
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    if kind == "array":
        items = schema.get("items")
        return [_sample(name, items, defs)] if schema.get("minItems") and items else []
    if kind == "object" or "properties" in schema:
        return _sample_body(schema, defs)
    return "x"


def _sample_body(schema, defs):
    """Only the required properties: the smallest document the model will accept."""
    body = {}
    for name in schema.get("required", []):
        prop = schema.get("properties", {}).get(name)
        if prop is not None:
            body[name] = _sample(name, prop, defs)
    return body


@functools.cache
def _write_routes():
    """(method, declared path, requestable path, body) for every route behind `write`.

    The scope is read from the route's own source rather than from a list: the gate is a
    call inside the endpoint, not a dependency, so there is nothing on the route object to
    ask. Routes that also reach for a stronger scope somewhere in the body (`POST
    /api/settings` asks `admin` for one field) are left out -- a write key is refused there
    for a reason this file is not about.
    """
    import app.main as app_main

    found = []
    for path, route in _walk_routes(app_main.app.router):
        try:
            source = inspect.getsource(route.endpoint)
        except (OSError, TypeError):
            continue
        if sorted(set(_SCOPE_CALL.findall(source))) != ["write"]:
            continue
        body = {}
        if route.body_field is not None:
            model = route.body_field.field_info.annotation
            if isinstance(model, type) and issubclass(model, BaseModel):
                schema = model.model_json_schema()
                body = _sample_body(schema, schema.get("$defs", {}))
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found.append((method, path, _PATH_PARAM.sub("1", path), body))
    return tuple(found)


@contextlib.contextmanager
def _gate_only():
    """Let the real gate decide, then stop the request before the route body does anything.

    The gate is imported by name into each `app.api.*` module, so the wrapper is installed
    on each binding rather than on `app.auth`. It calls the real function and adds nothing
    to the decision: a refusal still raises the real `HTTPException` from inside it, and the
    sentinel is only reached when the real gate has already returned.

    Yields the list of scopes the gates accepted, in the order they were reached.
    """
    accepted: list[str | None] = []
    patchers = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("app.api."):
            continue
        for attribute in ("require_auth", "require_auth_or_setup"):
            real = getattr(module, attribute, None)
            if real is None:
                continue

            def wrapper(request, scope=None, _real=real):
                _real(request, scope=scope)
                accepted.append(scope)
                raise _GateOpened(scope)

            patchers.append(patch.object(module, attribute, wrapper))
    for p in patchers:
        p.start()
    try:
        yield accepted
    finally:
        for p in reversed(patchers):
            p.stop()


class _ScopedKeyFixture(unittest.TestCase):
    """A temp database with one key per scope. `tests/` is not a package, hence the repeat."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "write-scope.test.db")
        models.init_db()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            # Without a password every request is granted `admin`, and no scope is weighed.
            patch.object(auth, "APP_PASSWORD", PASSWORD),
            # The sweep is longer than any per-minute cap on the routes it walks.
            patch.object(_limiter, "_check_request_limit", _no_rate_limit),
        ]
        for p in self._patchers:
            p.start()

        import app.main as app_main

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()
        # `_gate_only` stops a request by raising, and the client above re-raises anything a
        # route raises instead of answering. This one answers 500, which is the shape the
        # sweeps read. Built without entering it: the lifespan belongs to the client above.
        self.gate_client = TestClient(app_main.app, raise_server_exceptions=False)

        conn = models.get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                       prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                       created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
            )
            # `padded` is the row the whole file comes from: the stored column that moved
            # forty-two routes from 403 to 200 the day segments started being stripped.
            for name, scopes in (
                ("ro", "read"),
                ("rw", "write"),
                ("adm", "admin"),
                ("padded", "read, write "),
            ):
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

    def _headers(self, name: str) -> dict:
        return {"Authorization": f"Bearer key-{name}"}


class WriteRouteInventory(unittest.TestCase):
    """The witness for the walk. A sweep over nothing passes, so the walk is checked first."""

    def test_the_walk_finds_the_write_routes(self):
        routes = _write_routes()
        self.assertGreaterEqual(
            len(routes),
            40,
            "the router walk found almost nothing, so every sweep in this file is measuring "
            "an empty list; FastAPI's included-router shape has probably changed again",
        )

    def test_the_walk_finds_the_routes_this_file_names_by_hand(self):
        """Three that must be in it, one of each verb, so a partial walk is caught too."""
        found = {(method, path) for method, path, _concrete, _body in _write_routes()}
        for expected in (
            ("POST", "/api/services/bulk"),
            ("PUT", "/api/services/{sid}"),
            ("DELETE", "/api/providers/{pid}"),
        ):
            self.assertIn(expected, found, f"the walk lost {expected[0]} {expected[1]}")

    def test_no_module_with_a_write_gate_is_missed_by_the_walk(self):
        """A whole router dropping out of the walk is the failure that hides the most."""
        walked = set()
        import app.main as app_main

        for _path, route in _walk_routes(app_main.app.router):
            try:
                source = inspect.getsource(route.endpoint)
            except (OSError, TypeError):
                continue
            if "write" in set(_SCOPE_CALL.findall(source)):
                walked.add(Path(inspect.getsourcefile(route.endpoint)).name)

        declared = {
            path.name
            for path in sorted((REPO_ROOT / "app" / "api").glob("*.py"))
            if "write" in set(_SCOPE_CALL.findall(path.read_text(encoding="utf-8")))
        }
        self.assertEqual(
            declared - walked,
            set(),
            "these modules declare a write gate but no route of theirs is reachable from "
            f"the router walk: {sorted(declared - walked)}",
        )


class EveryWriteRouteWeighsTheScope(_ScopedKeyFixture):
    """Both directions, over the whole discovered inventory."""

    def test_a_read_only_key_is_refused_on_every_write_route(self):
        """A plain request: the gate raises before the body, so nothing here runs a route."""
        for method, path, concrete, body in _write_routes():
            with self.subTest(route=f"{method} {path}"):
                resp = self.client.request(
                    method, concrete, json=body, headers=self._headers("ro")
                )
                self.assertEqual(
                    resp.status_code,
                    403,
                    f"{method} {path} answered {resp.status_code} to a read-only key. A 422 "
                    "means the request body was refused before the gate was reached -- add "
                    "the field it names to _FIELD_SAMPLES. Anything else is the gate: "
                    f"{resp.text[:200]}",
                )
                self.assertIn("Insufficient scope", resp.json().get("detail", ""))

    def test_a_write_key_opens_the_gate_on_every_write_route(self):
        """Stopped the instant the gate returns, so no route body imports, deletes or dials."""
        for method, path, concrete, body in _write_routes():
            with self.subTest(route=f"{method} {path}"):
                with _gate_only() as accepted:
                    self.gate_client.request(
                        method, concrete, json=body, headers=self._headers("rw")
                    )
                self.assertEqual(
                    accepted,
                    ["write"],
                    f"{method} {path} did not let a write key through its first gate "
                    f"(gates accepted: {accepted})",
                )

    def test_the_padded_scope_column_opens_them_too(self):
        """The measured row itself: `'read, write '` has to mean what `'write'` means."""
        for method, path, concrete, body in _write_routes():
            with self.subTest(route=f"{method} {path}"):
                with _gate_only() as accepted:
                    self.gate_client.request(
                        method, concrete, json=body, headers=self._headers("padded")
                    )
                self.assertEqual(accepted, ["write"], f"{method} {path} refused 'read, write '")

    def test_the_stop_after_the_gate_decides_nothing_itself(self):
        """The witness for `_gate_only`: it defers, it does not admit.

        If the wrapper let a caller through on its own, the sweep above would pass against a
        guard that had been deleted. Two callers the real gate must turn away are driven
        through the same stop: nothing is recorded and the real status comes back.
        """
        method, _path, concrete, body = next(
            (m, p, c, b) for m, p, c, b in _write_routes() if m == "POST"
        )
        with _gate_only() as accepted:
            anonymous = self.gate_client.request(method, concrete, json=body)
            refused = self.gate_client.request(
                method, concrete, json=body, headers=self._headers("ro")
            )
        self.assertEqual(anonymous.status_code, 401, anonymous.text)
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(accepted, [], "the stop reported a gate the real one never opened")


class TheBulkRouteIsDrivenForReal(_ScopedKeyFixture):
    """`POST /api/services/bulk`: no stop, no patching, the row read back out of SQLite.

    The service row carries no provider ids, so the enable/disable path touches the database
    and nothing outside it -- the provider loop skips a row with neither a proxy nor a DNS
    provider attached.
    """

    BULK = "/api/services/bulk"

    def setUp(self) -> None:
        super().setUp()
        conn = models.get_db()
        try:
            conn.execute(
                """INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled)
                   VALUES (1, 'app', 'example.com', '127.0.0.1', 80, 1)"""
            )
            conn.commit()
        finally:
            conn.close()

    def _enabled(self) -> int:
        conn = models.get_db()
        try:
            return conn.execute("SELECT enabled FROM services WHERE id=1").fetchone()["enabled"]
        finally:
            conn.close()

    def test_the_row_starts_enabled(self):
        """The witness: a disable that proves something has to start from the other state."""
        self.assertEqual(self._enabled(), 1)

    def test_a_read_only_key_cannot_disable_a_service(self):
        resp = self.client.post(
            self.BULK, json={"ids": [1], "action": "disable"}, headers=self._headers("ro")
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("Insufficient scope", resp.json().get("detail", ""))
        self.assertEqual(self._enabled(), 1, "a refused request changed the row anyway")

    def test_a_write_key_disables_the_service(self):
        resp = self.client.post(
            self.BULK, json={"ids": [1], "action": "disable"}, headers=self._headers("rw")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["affected"], 1, resp.text)
        self.assertEqual(self._enabled(), 0, "the route answered 200 and wrote nothing")

    def test_a_key_whose_column_is_padded_disables_it_as_well(self):
        """`'read, write '` is the stored string the measurement was taken on."""
        resp = self.client.post(
            self.BULK, json={"ids": [1], "action": "disable"}, headers=self._headers("padded")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._enabled(), 0)

    def test_a_write_key_can_delete_a_service_outright(self):
        """The most destructive thing a write key reaches, driven rather than described."""
        resp = self.client.post(
            self.BULK, json={"ids": [1], "action": "delete"}, headers=self._headers("rw")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conn = models.get_db()
        try:
            self.assertIsNone(conn.execute("SELECT id FROM services WHERE id=1").fetchone())
        finally:
            conn.close()

    def test_a_read_only_key_cannot_delete_one(self):
        resp = self.client.post(
            self.BULK, json={"ids": [1], "action": "delete"}, headers=self._headers("ro")
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        conn = models.get_db()
        try:
            self.assertIsNotNone(conn.execute("SELECT id FROM services WHERE id=1").fetchone())
        finally:
            conn.close()

    def test_a_write_key_is_still_refused_where_admin_is_required(self):
        """The ladder holds: `write` is not a skeleton key, it is one rung."""
        resp = self.client.get(ADMIN_ROUTE, headers=self._headers("rw"))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("Insufficient scope", resp.json().get("detail", ""))

    def test_an_admin_key_reaches_the_write_route_from_above(self):
        """Positive witness for the same ladder, read the other way."""
        resp = self.client.post(
            self.BULK, json={"ids": [1], "action": "disable"}, headers=self._headers("adm")
        )
        self.assertEqual(resp.status_code, 200, resp.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
