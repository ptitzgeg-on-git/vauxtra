"""Second review pass on the MCP bridge.

- M16: behind a reverse proxy forwarding `Host: mcp.example.com`, every request got 421,
  and the README still said the HTTP transport had no authentication at all. With a token
  configured, the token now replaces the Host and Origin checks.
- M17: `run_preflight` was annotated read-only although it makes the server open a TCP
  connection to an arbitrary host and port (the API demands `write` for it).
- M18: listening off loopback without a token only printed a warning, so any LAN client
  without an `Origin` header got the full rights of VAUXTRA_API_KEY. It now refuses to
  start, with no opt-out.
- B24: the bearer token was decoded as latin-1 (a non-ASCII token never matched), the Host
  check was switched off on a wildcard bind, and `stream_logs_snapshot` could outlive its
  `timeout_seconds` by a whole extra read timeout.
"""

import asyncio
import contextlib
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from vauxtra_mcp import server as mcp_server
from vauxtra_mcp.tools import admin as admin_tools

REPO_ROOT = Path(__file__).resolve().parents[1]

_MCP_VARS = ("VAUXTRA_MCP_HOST", "VAUXTRA_MCP_PORT", "VAUXTRA_MCP_TOKEN")


@contextlib.contextmanager
def _mcp_env(**values):
    with patch.dict(os.environ, {}, clear=False):
        for key in _MCP_VARS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield


def _call(headers: list[tuple[bytes, bytes]], bind: str = "127.0.0.1", token: str = "") -> int:
    reached = []
    sent = []

    async def _app(scope, receive, send):
        reached.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def _send(message):
        sent.append(message)

    async def _receive():
        return {"type": "http.request", "body": b""}

    guard = mcp_server._HttpGuard(_app, bind, token)
    asyncio.run(guard({"type": "http", "headers": headers}, _receive, _send))
    status = sent[0]["status"]
    assert bool(reached) == (status == 200)
    return status


_TOKEN = "s3cret-test"
_BEARER = (b"authorization", b"Bearer " + _TOKEN.encode())


class M16AProxiedNameIsAccepted(unittest.TestCase):
    def test_a_proxied_host_with_the_token_gets_through(self) -> None:
        for bind in ("0.0.0.0", "10.0.0.4", "127.0.0.1"):
            with self.subTest(bind=bind):
                self.assertEqual(
                    _call([(b"host", b"mcp.example.com"), _BEARER], bind=bind, token=_TOKEN), 200
                )
                self.assertEqual(
                    _call(
                        [(b"host", b"mcp.example.com:443"),
                         (b"origin", b"https://mcp.example.com"), _BEARER],
                        bind=bind,
                        token=_TOKEN,
                    ),
                    200,
                )

    def test_the_proxied_host_without_the_token_is_refused(self) -> None:
        self.assertEqual(_call([(b"host", b"mcp.example.com")], bind="0.0.0.0", token=_TOKEN), 401)
        self.assertEqual(
            _call([(b"host", b"mcp.example.com"), (b"authorization", b"Bearer wrong")],
                  bind="0.0.0.0", token=_TOKEN),
            401,
        )

    def test_without_a_token_the_host_check_still_applies(self) -> None:
        self.assertEqual(_call([(b"host", b"mcp.example.com")]), 421)
        self.assertEqual(
            _call([(b"host", b"127.0.0.1:9000"), (b"origin", b"https://attacker.example")]), 403
        )

    def test_the_readme_no_longer_claims_there_is_no_authentication(self) -> None:
        text = (REPO_ROOT / "vauxtra_mcp" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("no authentication of its own", text)
        self.assertIn("VAUXTRA_MCP_TOKEN", text)
        self.assertIn("mcp.example.com", text)

    def test_no_undocumented_variable_was_added(self) -> None:
        source = (REPO_ROOT / "vauxtra_mcp" / "server.py").read_text(encoding="utf-8")
        for name in ("VAUXTRA_MCP_ALLOWED_HOSTS", "VAUXTRA_MCP_ALLOW_UNAUTHENTICATED"):
            self.assertNotIn(name, source)


class M17PreflightIsNotARead(unittest.TestCase):
    def test_the_annotation_says_it_reaches_out(self) -> None:
        import vauxtra_mcp.tools.operations  # noqa: F401  (registers the tool)
        from vauxtra_mcp.app import mcp

        annotations = asyncio.run(mcp.get_tool("run_preflight")).annotations
        self.assertFalse(annotations.readOnlyHint)
        self.assertTrue(annotations.openWorldHint)
        self.assertFalse(annotations.destructiveHint)


class M18NoSilentUnauthenticatedPublish(unittest.TestCase):
    def test_a_lan_bind_without_token_refuses_to_start(self) -> None:
        for host in ("0.0.0.0", "192.168.1.10", "::"):
            with self.subTest(host=host), _mcp_env(VAUXTRA_MCP_HOST=host):
                with self.assertRaises(SystemExit) as caught:
                    mcp_server._http_bind()
                self.assertIn("VAUXTRA_MCP_TOKEN", str(caught.exception.code))

    def test_a_blank_token_is_no_token(self) -> None:
        with _mcp_env(VAUXTRA_MCP_HOST="0.0.0.0", VAUXTRA_MCP_TOKEN="   "):
            with self.assertRaises(SystemExit):
                mcp_server._http_bind()

    def test_a_token_is_enough(self) -> None:
        with _mcp_env(VAUXTRA_MCP_HOST="0.0.0.0", VAUXTRA_MCP_TOKEN="t" * 32):
            self.assertEqual(mcp_server._http_bind()[0], "0.0.0.0")

    def test_a_short_token_refuses_to_start(self) -> None:
        # B18 (fourth pass): the token is all that guards a published bridge.
        for host in ("0.0.0.0", "127.0.0.1"):
            with self.subTest(host=host), _mcp_env(VAUXTRA_MCP_HOST=host, VAUXTRA_MCP_TOKEN="t" * 31):
                with self.assertRaises(SystemExit) as caught:
                    mcp_server._http_bind()
                self.assertIn("too short", str(caught.exception.code))

    def test_loopback_needs_neither(self) -> None:
        with _mcp_env():
            self.assertEqual(mcp_server._http_bind(), ("127.0.0.1", 9000))


class B24SmallDefects(unittest.TestCase):
    def test_a_non_ascii_token_matches(self) -> None:
        token = "s3crét-ü"
        host = (b"host", b"127.0.0.1:9000")
        good = (b"authorization", b"Bearer " + token.encode("utf-8"))
        self.assertEqual(_call([host, good], token=token), 200)
        bad = (b"authorization", b"Bearer " + token.encode("latin-1"))
        self.assertEqual(_call([host, bad], token=token), 401)

    def test_a_wildcard_bind_still_checks_the_host(self) -> None:
        self.assertEqual(_call([(b"host", b"10.0.0.4:9000")], bind="0.0.0.0"), 421)
        self.assertEqual(_call([(b"host", b"attacker.example")], bind="::"), 421)
        self.assertEqual(_call([(b"host", b"127.0.0.1:9000")], bind="0.0.0.0"), 200)

    def test_the_snapshot_keeps_its_deadline_when_a_read_blocks(self) -> None:
        """One event, then a read that blocks: the call must end near `timeout_seconds`."""
        release = threading.Event()

        def content():
            yield b'data: {"message": "up"}\n\n'
            release.wait(30)  # a read that never trips httpx's own timeout in time

        def handler(_request):
            return httpx.Response(200, content=content())

        real_client = httpx.Client

        def factory(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            return real_client(*args, **kwargs)

        try:
            with patch.object(httpx, "Client", factory):
                started = time.monotonic()
                result = admin_tools.stream_logs_snapshot(timeout_seconds=1)
                elapsed = time.monotonic() - started
        finally:
            release.set()

        self.assertLess(elapsed, 3.0)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["events"], [{"message": "up"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
