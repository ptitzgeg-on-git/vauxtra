"""Every route that changes state or acts outward must refuse a read-only key.

Nothing in the suite used to exercise a 403 at all: `require_auth(request)` with no scope
lets any authenticated caller through, and a batch of POST routes were written that way.
A key minted for a monitoring dashboard could rewrite every service status, drive the
server into arbitrary TCP connections, send notifications, and try the admin password.
"""

import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models


class ApiKeyScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "scopes.test.db")
        models.init_db()

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            # A password must be configured, otherwise every request is granted `admin`.
            patch.object(auth, "APP_PASSWORD", "test-password"),
        ]
        for p in self._patchers:
            p.start()

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

        conn = models.get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                   prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                   created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
        )
        for name, scopes in (("ro", "read"), ("rw", "write"), ("adm", "admin")):
            conn.execute(
                "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                (name, hashlib.sha256(f"key-{name}".encode()).hexdigest(), name[:8], scopes),
            )
        conn.execute(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 80, 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (1, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer key-{key}"}

    # Routes that used to accept any authenticated caller, and the scope each now needs.
    WRITE_ROUTES = [
        ("/api/services/preflight", {"subdomain": "app", "domain": "example.com",
                                     "target_ip": "127.0.0.1", "target_port": 80}),
        ("/api/services/check-all", None),
        ("/api/providers/1/test", None),
        ("/api/providers/1/validate", None),
        ("/api/settings/test-webhook", None),
    ]

    def test_a_read_key_is_refused_on_every_write_route(self):
        for path, body in self.WRITE_ROUTES:
            with self.subTest(path=path):
                resp = self.client.post(path, json=body, headers=self._headers("ro"))
                self.assertEqual(resp.status_code, 403, resp.text)
                self.assertIn("Insufficient scope", resp.json().get("detail", ""))

    def test_a_write_key_gets_past_the_scope_check(self):
        """The call may still fail on its own merits -- it must not fail on the scope."""
        for path, body in self.WRITE_ROUTES:
            with self.subTest(path=path):
                resp = self.client.post(path, json=body, headers=self._headers("rw"))
                self.assertNotEqual(resp.status_code, 403, resp.text)

    def test_changing_the_admin_password_needs_the_admin_scope(self):
        body = {"current_password": "test-password", "new_password": "another-password"}

        refused = self.client.post("/api/auth/change-password", json=body, headers=self._headers("rw"))
        self.assertEqual(refused.status_code, 403, refused.text)

        allowed = self.client.post("/api/auth/change-password", json=body, headers=self._headers("adm"))
        self.assertNotEqual(allowed.status_code, 403, allowed.text)

    def test_the_read_only_diagnostics_stay_open_to_a_read_key(self):
        resp = self.client.post("/api/services/1/push/dry-run", headers=self._headers("ro"))
        self.assertNotEqual(resp.status_code, 403, resp.text)

    def test_the_single_service_check_needs_a_write_key_on_both_verbs(self):
        """It rewrites `status` and `last_checked`, appends history, and writes a log line.

        Until 1.5.0 it was a GET behind an unscoped `require_auth`, so a key minted for a
        dashboard could rewrite the state of any route -- and, being a GET, a prefetch or a
        crawler following the link did the write with nobody pressing anything. The probe
        itself is stubbed: what is under test here is the gate in front of it.
        """
        from unittest.mock import patch as _patch

        import app.api.services as services_api

        probe = {"id": 1, "status": "ok", "latency_ms": 1.0, "dns_resolved": []}
        for verb in ("post", "get"):
            with self.subTest(verb=verb):
                call = getattr(self.client, verb)

                refused = call("/api/services/1/check", headers=self._headers("ro"))
                self.assertEqual(refused.status_code, 403, refused.text)
                self.assertIn("Insufficient scope", refused.json().get("detail", ""))

                with _patch.object(services_api, "_check_one", return_value=probe):
                    allowed = call("/api/services/1/check", headers=self._headers("rw"))
                self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_the_get_form_of_the_check_is_a_deprecated_alias_of_the_post(self):
        """The POST is the canonical verb; the GET stays one version for existing scripts."""
        by_verb = {
            verb: route
            for route in app_main.app.routes
            if getattr(route, "path", "") == "/api/services/{sid}/check"
            for verb in getattr(route, "methods", ())
        }
        self.assertIn("POST", by_verb)
        self.assertFalse(by_verb["POST"].deprecated)
        self.assertTrue(by_verb["GET"].deprecated, "the GET alias must be marked deprecated")

    def test_an_unauthenticated_caller_still_gets_401_not_403(self):
        resp = self.client.post("/api/services/check-all")
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_no_write_route_is_left_without_a_scope(self):
        """A guard against the next POST route written as plain `require_auth(request)`.

        Unscoped means "any authenticated caller", which includes a key granted nothing at
        all -- `_scope_satisfies` is never consulted.
        """
        import re
        from pathlib import Path

        api_dir = Path(__file__).resolve().parent.parent / "app" / "api"
        route_re = re.compile(r'@router\.(post|put|delete|patch)\(')
        offenders = []
        for path in sorted(api_dir.glob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            pending = None
            for lineno, line in enumerate(lines, 1):
                if route_re.search(line):
                    pending = (lineno, line.strip())
                elif "require_auth" in line and "(" in line and pending:
                    if "scope=" not in line:
                        offenders.append(f"{path.name}:{lineno} {pending[1]}")
                    pending = None
        self.assertEqual(offenders, [], "write routes without an explicit scope: " + "; ".join(offenders))


    def test_no_get_route_writes_behind_an_unscoped_auth(self):
        """The textual guard above cannot see F18: `check_service` was a GET.

        A GET that writes is reachable by a prefetch, a crawler or an uptime probe, and an
        unscoped one answers a read-only key. This walks the AST instead of the text, and
        follows one level of module-level helper calls, since the body of a route is as
        likely to live in a `_helper()` as inline.
        """
        import ast
        import re
        from pathlib import Path

        write_sql = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.I)

        def writes(node, helpers, depth=2):
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and write_sql.search(child.value)
                ):
                    return True
            if depth:
                for child in ast.walk(node):
                    if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                            and child.func.id in helpers
                            and writes(helpers[child.func.id], helpers, depth - 1)):
                        return True
            return False

        def router_verbs(node):
            verbs = set()
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.value.id == "router"):
                    verbs.add(target.attr)
            return verbs

        offenders = []
        for path in sorted((Path(__file__).resolve().parent.parent / "app" / "api").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            helpers, routes = {}, []
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                verbs = router_verbs(node)
                if verbs:
                    routes.append((node, verbs))
                else:
                    helpers[node.name] = node

            for node, verbs in routes:
                if "get" not in verbs or not writes(node, helpers):
                    continue
                scoped = any(
                    "scope=" in (ast.get_source_segment(source, call) or "")
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "require_auth"
                )
                if not scoped:
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
        self.assertEqual(
            offenders, [], "GET routes that write without a scope: " + "; ".join(offenders)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
