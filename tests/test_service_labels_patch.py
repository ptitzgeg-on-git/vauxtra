"""A label is not a route: putting a tag on a service must not republish it.

Found in production on 2026-09-22, and confirmed in the code: no route set a service's tags,
environments or icon alone, so the only way was `PUT /api/services/{sid}` with the whole
service. That call republishes the service on its proxy or tunnel every time, and on a tunnel
service the rule it wrote back had lost its `originRequest`. Two routes carried `noTLSVerify`,
so tagging either of them would have cleared it, for an edit that only touched a list of labels.

`PATCH /api/services/{sid}` writes those three fields and nothing else. The tests below hold
that from the outside: every way Vauxtra reaches a provider is replaced by a trap that records
its calls, on a tunnel service, and the route has to answer without springing one. They also
hold what the route still refuses, because writing less is no reason to check less: an id that
names nothing, a key it does not own, a body that asks for nothing, a key that may only read.
"""

import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from vauxtra_mcp import client as bridge_client
from vauxtra_mcp.tools import services as service_tools

SID = 1
HOST = "app.example.org"


class _LabelBench(unittest.TestCase):
    """A tunnel service carrying one tag, one environment and an icon, with traps set.

    `create_provider` is replaced where the service routes import it and where it is
    defined, and `requests` is replaced under every provider class: a provider reached by
    any path shows up in `provider_calls` or `http_calls`.
    """

    HEADERS = {"Authorization": "Bearer key-rw"}
    READ_HEADERS = {"Authorization": "Bearer key-ro"}

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "labels.test.db")
        models.init_db()

        self.provider_calls = MagicMock(name="create_provider")
        self.http_calls = MagicMock(name="Session.request")
        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            patch.object(auth, "APP_PASSWORD", "test-password"),
            patch("app.api.services.create_provider", self.provider_calls),
            patch("app.api.sync.create_provider", self.provider_calls),
            patch("app.providers.factory.create_provider", self.provider_calls),
            patch("requests.sessions.Session.request", self.http_calls),
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
        conn.executemany(
            "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
            [
                ("rw", hashlib.sha256(b"key-rw").hexdigest(), "rw", "write"),
                ("ro", hashlib.sha256(b"key-ro").hexdigest(), "ro", "read"),
            ],
        )
        conn.execute(
            "INSERT INTO providers (id, name, type, url, extra, enabled) VALUES (1,?,?,?,?,1)",
            ("Tunnel", "cloudflare_tunnel", "https://api.cloudflare.com",
             '{"account_id": "acc", "tunnel_id": "tid"}'),
        )
        conn.executemany("INSERT INTO tags (id, name) VALUES (?,?)", [(1, "prod"), (2, "media")])
        conn.executemany(
            "INSERT INTO environments (id, name) VALUES (?,?)", [(1, "home"), (2, "lab")]
        )
        conn.execute(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port,
                   tunnel_provider_id, expose_mode, tunnel_hostname, icon_url)
               VALUES (?,?,?,?,?,1,'tunnel',?,?)""",
            (SID, "app", "example.org", "10.0.0.5", 8080, HOST, "old.png"),
        )
        conn.execute("INSERT INTO service_tags (service_id, tag_id) VALUES (?,1)", (SID,))
        conn.execute(
            "INSERT INTO service_environments (service_id, environment_id) VALUES (?,1)", (SID,)
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- instruments ----------------------------------------------------------------------

    def _patch(self, body, sid: int = SID, headers=None):
        return self.client.patch(
            f"/api/services/{sid}", json=body, headers=headers or self.HEADERS
        )

    def _get(self) -> dict:
        r = self.client.get(f"/api/services/{SID}", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _labels(self) -> tuple[list[int], list[int], str]:
        s = self._get()
        return (
            sorted(t["id"] for t in s["tags"]),
            sorted(e["id"] for e in s["environments"]),
            s["icon_url"],
        )

    def _journal(self, prefix: str) -> list[str]:
        conn = models.get_db()
        try:
            return [
                r["message"]
                for r in conn.execute(
                    "SELECT message FROM logs WHERE message LIKE ? ORDER BY id", (prefix + "%",)
                )
            ]
        finally:
            conn.close()

    def assertNoProviderReached(self) -> None:
        self.assertEqual(self.provider_calls.call_count, 0, self.provider_calls.call_args_list)
        self.assertEqual(self.http_calls.call_count, 0, self.http_calls.call_args_list)


class ALabelChangeCallsNoProviderTests(_LabelBench):

    def test_tags_environments_and_icon_change_without_reaching_the_tunnel(self):
        r = self._patch({"tag_ids": [1, 2], "environment_ids": [2], "icon_url": "new.png"})

        self.assertEqual(r.status_code, 200, r.text)
        self.assertNoProviderReached()
        self.assertEqual(self._labels(), ([1, 2], [2], "new.png"))

    def test_the_answer_is_the_service_as_get_gives_it(self):
        r = self._patch({"tag_ids": [2]})

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), self._get())
        # Nothing outside the database was asked, so there is no partial failure to report.
        self.assertNotIn("errors", r.json())

    def test_the_journal_names_the_service_and_what_changed(self):
        self._patch({"tag_ids": [2], "icon_url": "new.png"})

        self.assertEqual(
            self._journal("Service labels updated:"),
            [f"Service labels updated: {HOST} (tags, icon), no provider called"],
        )


class AFieldLeftOutIsLeftAloneTests(_LabelBench):

    def test_a_field_left_out_keeps_its_value(self):
        r = self._patch({"tag_ids": [2]})

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._labels(), ([2], [1], "old.png"))

    def test_a_field_sent_as_null_keeps_its_value(self):
        r = self._patch({"tag_ids": None, "environment_ids": None, "icon_url": "new.png"})

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._labels(), ([1], [1], "new.png"))

    def test_an_empty_list_clears_and_an_empty_icon_clears(self):
        r = self._patch({"tag_ids": [], "environment_ids": [], "icon_url": ""})

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._labels(), ([], [], ""))


class WhatTheRouteRefusesTests(_LabelBench):

    def test_an_unknown_tag_is_refused_and_nothing_is_written(self):
        r = self._patch({"tag_ids": [2, 99], "icon_url": "new.png"})

        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("tag 99", r.json()["detail"])
        self.assertEqual(self._labels(), ([1], [1], "old.png"))

    def test_tag_0_names_nothing_and_is_refused(self):
        r = self._patch({"tag_ids": [0]})

        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("tag 0", r.json()["detail"])
        self.assertEqual(self._labels(), ([1], [1], "old.png"))

    def test_an_unknown_environment_is_refused_and_nothing_is_written(self):
        r = self._patch({"tag_ids": [2], "environment_ids": [42]})

        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("environment 42", r.json()["detail"])
        self.assertEqual(self._labels(), ([1], [1], "old.png"))

    def test_a_routing_field_is_a_422_and_the_labels_beside_it_are_not_written(self):
        for extra in ({"target_port": 81}, {"subdomain": "other"}, {"enabled": False}):
            with self.subTest(extra=extra):
                r = self._patch({"tag_ids": [2], **extra})
                self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self._labels(), ([1], [1], "old.png"))

    def test_the_serialized_relations_are_a_422_not_a_silent_wipe(self):
        # What a client that read the service back would send: `tags`, not `tag_ids`.
        r = self._patch({"tags": [{"id": 2, "name": "media"}]})

        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self._labels(), ([1], [1], "old.png"))

    def test_a_body_that_asks_for_nothing_is_a_400(self):
        for body in ({}, {"tag_ids": None}):
            with self.subTest(body=body):
                r = self._patch(body)
                self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self._journal("Service labels updated:"), [])

    def test_a_service_that_does_not_exist_is_a_404(self):
        r = self._patch({"tag_ids": [2]}, sid=999)

        self.assertEqual(r.status_code, 404, r.text)

    def test_a_read_key_may_not_write_labels(self):
        r = self._patch({"tag_ids": [2]}, headers=self.READ_HEADERS)

        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self._labels(), ([1], [1], "old.png"))


class TheFullUpdateChecksTag0FirstTests(_LabelBench):
    """The same hole on the route that does push, where it cost more.

    `_unknown_references` passed tag and environment ids through `if i` before asking the
    database, so `tag_ids: [0]` was never looked up: the providers were called, and
    `set_tags` then failed on the foreign key with a 500, after the route had moved.
    """

    def test_a_put_with_tag_0_is_refused_before_any_provider_is_called(self):
        body = {
            "subdomain": "app", "domain": "example.org",
            "target_ip": "10.0.0.5", "target_port": 8080,
            "expose_mode": "tunnel", "tunnel_provider_id": 1, "tunnel_hostname": HOST,
            "tag_ids": [0],
        }
        r = self.client.put(f"/api/services/{SID}", json=body, headers=self.HEADERS)

        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("tag 0", r.json()["detail"])
        self.assertNoProviderReached()


class TheBridgeLabelsWithoutAPushTests(_LabelBench):
    """`set_service_labels`, the MCP tool, is one PATCH carrying the arguments it was given.

    Its requests are answered by this application through the test client, so the tool is
    held end to end: the route, the database and the provider traps. Two ways to get it wrong
    are both quiet. Reading the service first and sending it back is what `update_service`
    does, and that is the push this tool exists to avoid. Testing an argument for truth rather
    than for None drops `tag_ids=[]` and `icon_url=""`, the only way to clear either, and the
    tool answers with the service unchanged.
    """

    def setUp(self) -> None:
        super().setUp()
        self.sent: list[httpx.Request] = []
        real_client = httpx.Client

        def through_the_app(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            answer = self.client.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={"Content-Type": "application/json", **self.HEADERS},
            )
            return httpx.Response(
                answer.status_code,
                content=answer.content,
                headers={"Content-Type": "application/json"},
            )

        def factory(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(through_the_app))
            return real_client(*args, **kwargs)

        bridge = patch.object(httpx, "Client", factory)
        bridge.start()
        self.addCleanup(bridge.stop)

    def _body(self, n: int = 0) -> dict:
        return json.loads(self.sent[n].content)

    def test_one_patch_and_nothing_read_first(self):
        answer = service_tools.set_service_labels(SID, tag_ids=[2])

        self.assertEqual(
            [(r.method, r.url.path) for r in self.sent], [("PATCH", f"/api/services/{SID}")]
        )
        self.assertEqual(self._body(), {"tag_ids": [2]})
        self.assertEqual([t["id"] for t in answer["tags"]], [2])
        self.assertNoProviderReached()

    def test_what_is_left_out_is_not_sent_and_is_kept(self):
        service_tools.set_service_labels(SID, icon_url="new.png")

        self.assertEqual(self._body(), {"icon_url": "new.png"})
        self.assertEqual(self._labels(), ([1], [1], "new.png"))

    def test_an_empty_list_and_an_empty_icon_are_sent_because_they_clear(self):
        service_tools.set_service_labels(SID, tag_ids=[], environment_ids=[], icon_url="")

        self.assertEqual(self._body(), {"tag_ids": [], "environment_ids": [], "icon_url": ""})
        self.assertEqual(self._labels(), ([], [], ""))

    def test_a_refusal_reaches_the_agent_with_its_reason(self):
        with self.assertRaises(bridge_client.ApiError) as caught:
            service_tools.set_service_labels(SID, tag_ids=[99])

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("tag 99", caught.exception.detail)
        self.assertEqual(self._labels(), ([1], [1], "old.png"))


class CrossOriginAllowsEveryMethodTests(unittest.TestCase):
    """The allowed methods are a list written by hand, and a route can outgrow it.

    Found while adding the label route: `allow_methods` named GET, POST, PUT and DELETE, so a
    cross-origin panel (the Vite dev server, which `DEBUG=true` allows) would have had every
    PATCH refused at the preflight, with nothing on the server side to say why.
    """

    @staticmethod
    def _walk_routes(router, prefix=""):
        """Every `APIRoute` under `router`, through the wrappers FastAPI 0.141 leaves in
        `app.routes` for an included router (see `tests/test_api_key_write_scope.py`)."""
        for route in getattr(router, "routes", []):
            original = getattr(route, "original_router", None)
            if original is not None:
                context = getattr(route, "include_context", None)
                yield from CrossOriginAllowsEveryMethodTests._walk_routes(
                    original, prefix + (getattr(context, "prefix", "") or "")
                )
                continue
            if isinstance(route, APIRoute):
                yield prefix + route.path, route

    def test_every_method_an_api_route_answers_to_is_allowed(self):
        cors = [m for m in app_main.app.user_middleware if m.cls is CORSMiddleware]
        self.assertEqual(len(cors), 1)
        allowed = set(cors[0].kwargs["allow_methods"])

        used = {
            method
            for path, route in self._walk_routes(app_main.app)
            if path.startswith("/api/")
            for method in route.methods
        } - {"HEAD"}

        # The witness for the walk: an empty set would pass the comparison below.
        self.assertIn("PATCH", used)
        self.assertEqual(used - allowed, set())


if __name__ == "__main__":
    unittest.main()
