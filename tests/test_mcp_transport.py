"""What the bridge does between the tool and the API: sessions, errors, streams, docs.

Every tool in `vauxtra_mcp/tools/` goes through `vauxtra_mcp/client.py`, and four things
that layer was supposed to do it never did:

- `auth_login` received a `Set-Cookie`, closed its client, and dropped it. The tool answered
  `{"ok": true}` and authenticated nothing; only an API key ever worked.
- `raise_for_status()` reduced every failure to `Client error '400 Bad Request' for url ...`,
  throwing away the `detail` the API had written -- which since 1.1 is the useful half.
- `stream_logs_snapshot` let an `httpx.ReadTimeout` escape, so a quiet instance looked like
  a broken call and every event already collected was lost with it.
- `validate_provider_draft` sent no `name`, because there is nothing to name in a draft, and
  the route's model inherited a required one: a 422 on every single call, always.

The last test class watches the gate that watches the bridge.
"""

import contextlib
import http.cookiejar
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.api.providers import ProviderDraftValidationIn
from vauxtra_mcp import client
from vauxtra_mcp.tools import admin as admin_tools

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_parity_gate():
    """`scripts/` is not a package, so the gate is loaded by path."""
    path = REPO_ROOT / "scripts" / "check_api_mcp_parity.py"
    spec = importlib.util.spec_from_file_location("check_api_mcp_parity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _served_by(handler):
    """Answer the bridge's HTTP calls from `handler`, with no server involved.

    `httpx.Client` is patched rather than injected because the bridge builds its own client
    per call -- which is the shape being tested: the session has to survive that.
    """
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real_client(*args, **kwargs)

    with patch.object(httpx, "Client", factory):
        yield


class _CleanJar(unittest.TestCase):
    """The session jar is module state, so it is emptied around every test."""

    def setUp(self) -> None:
        client.clear_session()

    def tearDown(self) -> None:
        client.clear_session()


class TheSessionOutlivesTheRequestThatOpenedItTests(_CleanJar):
    def _login_handler(self, seen):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/api/auth/login":
                return httpx.Response(
                    200,
                    json={"ok": True},
                    headers={"set-cookie": "vauxtra_session=abc123; Path=/"},
                )
            return httpx.Response(200, json={"ok": True, "domains": []})

        return handler

    def test_the_cookie_is_carried_into_the_next_call(self) -> None:
        seen: list[httpx.Request] = []
        with _served_by(self._login_handler(seen)):
            admin_tools.auth_login("correct horse battery staple")
            admin_tools.list_domains()

        self.assertEqual(len(seen), 2)
        self.assertNotIn("cookie", seen[0].headers)  # nothing to send yet
        self.assertEqual(seen[1].headers.get("cookie"), "vauxtra_session=abc123")

    def test_the_bridge_knows_it_holds_one(self) -> None:
        with _served_by(self._login_handler([])):
            self.assertEqual(client.session_cookie_count(), 0)
            admin_tools.auth_login("correct horse battery staple")
            self.assertEqual(client.session_cookie_count(), 1)

    def test_logging_out_forgets_it_here_too(self) -> None:
        seen: list[httpx.Request] = []
        with _served_by(self._login_handler(seen)):
            admin_tools.auth_login("correct horse battery staple")
            admin_tools.auth_logout()
            admin_tools.list_domains()

        self.assertEqual(client.session_cookie_count(), 0)
        # The logout itself must be authenticated; the call after it must not be.
        self.assertEqual(seen[1].headers.get("cookie"), "vauxtra_session=abc123")
        self.assertNotIn("cookie", seen[2].headers)

    def test_a_bare_cookiejar_is_the_reason_this_works(self) -> None:
        """`httpx.Cookies` copies what it is handed -- except a raw jar, which it adopts.

        This is the whole fix, and it rests on a detail of someone else's library, so it is
        pinned here: if httpx ever starts copying the jar too, the sessions go silently back
        to being dropped and this test is what says so.
        """
        jar = http.cookiejar.CookieJar()
        self.assertIs(httpx.Cookies(jar).jar, jar)

        wrapped = httpx.Cookies()
        self.assertIsNot(httpx.Cookies(wrapped).jar, wrapped.jar)


class TheApiKeepsItsExplanationTests(_CleanJar):
    def _raises(self, response_factory):
        def handler(_request: httpx.Request) -> httpx.Response:
            return response_factory()

        with _served_by(handler), self.assertRaises(client.ApiError) as caught:
            client.check(client.post("/settings", json={}))
        return caught.exception

    def test_the_detail_is_what_the_caller_sees(self) -> None:
        error = self._raises(
            lambda: httpx.Response(
                400,
                json={"detail": "Nothing was saved -- check_interval: must be between 30 and 86400"},
            )
        )
        self.assertEqual(error.status_code, 400)
        self.assertEqual(
            error.detail,
            "Nothing was saved -- check_interval: must be between 30 and 86400",
        )
        self.assertIn("check_interval", str(error))

    def test_the_message_says_which_call_failed(self) -> None:
        error = self._raises(lambda: httpx.Response(409, json={"detail": "still in use"}))
        self.assertIn("POST", str(error))
        self.assertIn("/api/settings", str(error))

    def test_a_422_names_every_field_that_was_refused(self) -> None:
        error = self._raises(
            lambda: httpx.Response(
                422,
                json={
                    "detail": [
                        {"loc": ["body", "name"], "msg": "Field required"},
                        {"loc": ["body", "url"], "msg": "Input should be a valid string"},
                    ]
                },
            )
        )
        self.assertIn("name: Field required", error.detail)
        self.assertIn("url: Input should be a valid string", error.detail)

    def test_a_body_that_is_not_json_still_says_something(self) -> None:
        error = self._raises(lambda: httpx.Response(502, text="<html>Bad Gateway</html>"))
        self.assertEqual(error.status_code, 502)
        self.assertIn("Bad Gateway", error.detail)

    def test_an_empty_body_falls_back_to_the_status(self) -> None:
        error = self._raises(lambda: httpx.Response(500, text=""))
        self.assertEqual(error.status_code, 500)
        self.assertTrue(error.detail)  # never the empty string

    def test_a_success_is_returned_untouched(self) -> None:
        def handler(_request):
            return httpx.Response(200, json={"ok": True})

        with _served_by(handler):
            response = client.post("/settings", json={})
            self.assertIs(client.check(response), response)

    def test_no_tool_calls_raise_for_status_any_more(self) -> None:
        """One call site left behind is one error message thrown away.

        `client.py` is excluded on purpose: it names `raise_for_status` in the prose that
        explains why it stopped using it.
        """
        offenders = [
            path.name
            for path in (REPO_ROOT / "vauxtra_mcp" / "tools").glob("*.py")
            if "raise_for_status" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(sorted(offenders), [])


class SilenceIsAnAnswerTests(_CleanJar):
    @staticmethod
    def _sse(*lines, then_timeout=False):
        def content():
            for line in lines:
                yield f"data: {json.dumps(line)}\n\n".encode()
            if then_timeout:
                raise httpx.ReadTimeout("no more log lines came")

        return content

    def _snapshot(self, content, **kwargs):
        def handler(_request):
            return httpx.Response(200, content=content())

        with _served_by(handler):
            return admin_tools.stream_logs_snapshot(**kwargs)

    def test_a_quiet_instance_is_not_a_failed_call(self) -> None:
        result = self._snapshot(self._sse(then_timeout=True))
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["events"], [])
        self.assertTrue(result["timed_out"])

    def test_the_events_already_read_survive_the_timeout(self) -> None:
        result = self._snapshot(
            self._sse({"level": "info", "message": "up"},
                      {"level": "warn", "message": "drift"},
                      then_timeout=True)
        )
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["events"][1]["message"], "drift")

    def test_a_stream_that_ends_cleanly_did_not_time_out(self) -> None:
        result = self._snapshot(self._sse({"level": "info", "message": "up"}))
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["count"], 1)

    def test_max_events_is_a_bound_not_a_suggestion(self) -> None:
        result = self._snapshot(
            self._sse(*[{"n": i} for i in range(50)]), max_events=3
        )
        self.assertEqual(result["count"], 3)
        self.assertFalse(result["timed_out"])

    def test_an_error_status_still_carries_the_api_detail(self) -> None:
        """The streamed body is unread at that point; `check` needs it read first."""

        def handler(_request):
            return httpx.Response(403, json={"detail": "Insufficient scope"})

        with _served_by(handler), self.assertRaises(client.ApiError) as caught:
            admin_tools.stream_logs_snapshot()

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "Insufficient scope")


class ADraftHasNothingToNameTests(unittest.TestCase):
    def test_the_payload_the_bridge_sends_is_accepted(self) -> None:
        # Exactly what `validate_provider_draft` builds. It used to be a 422, every time.
        ProviderDraftValidationIn(
            type="npm",
            url="http://npm.lan:81",
            username="admin@example.com",
            password="secret",
            extra={},
            hostname_hint="",
            write_probe=False,
        )

    def test_the_name_is_not_required(self) -> None:
        model = ProviderDraftValidationIn(type="adguard", url="http://dns.lan:3000")
        self.assertEqual(model.name, "(draft)")

    def test_creating_a_provider_still_demands_one(self) -> None:
        """The default belongs to the draft route only: a stored provider needs a real name."""
        from pydantic import ValidationError

        from app.api.providers import ProviderIn

        with self.assertRaises(ValidationError):
            ProviderIn(type="adguard", url="http://dns.lan:3000")


class TheGateWatchesTheBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = _load_parity_gate()

    def test_the_readme_lists_exactly_the_tools_that_exist(self) -> None:
        tools = self.gate.collect_mcp_tools(REPO_ROOT)
        documented = self.gate.collect_documented_tools(REPO_ROOT)
        self.assertEqual(sorted(tools - documented), [], "undocumented tools")
        self.assertEqual(sorted(documented - tools), [], "tools that do not exist")
        self.assertGreater(len(tools), 80)

    def test_a_route_is_a_method_and_a_path_not_a_path(self) -> None:
        """`PUT /api/templates/{}` counted as covered by a GET and a DELETE on the same path."""
        mcp_routes = self.gate.collect_mcp_routes(REPO_ROOT)
        api_routes = self.gate.collect_api_routes(REPO_ROOT)
        self.assertIn(("PUT", "/api/templates/{}"), api_routes)
        self.assertIn(("PUT", "/api/templates/{}"), mcp_routes)

    def test_every_api_route_is_covered_or_deliberately_exempt(self) -> None:
        uncovered = (
            self.gate.collect_api_routes(REPO_ROOT)
            - self.gate.collect_mcp_routes(REPO_ROOT)
            - self.gate.ALLOWED_API_ONLY
        )
        self.assertEqual(sorted(uncovered), [])

    def test_no_exemption_outlives_its_route(self) -> None:
        """An allowlist entry for a route that is gone is an exemption nobody is watching."""
        stale = self.gate.ALLOWED_API_ONLY - self.gate.collect_api_routes(REPO_ROOT)
        self.assertEqual(sorted(stale), [])


if __name__ == "__main__":
    unittest.main()
