"""
Vauxtra MCP Server

Exposes Vauxtra's DNS & proxy management capabilities as MCP tools
for integration with MCP-compatible clients (Claude Desktop, Cursor, etc.).

Environment variables:
  VAUXTRA_URL      — Base URL of the Vauxtra instance (default: http://localhost:8888)
  VAUXTRA_API_KEY  — API key created in Vauxtra Settings → API Keys (Bearer auth)

  VAUXTRA_MCP_HOST — interface --http binds to (default: 127.0.0.1); any
                     non-loopback address also requires VAUXTRA_MCP_TOKEN
  VAUXTRA_MCP_PORT — port --http binds to (default: 9000)
  VAUXTRA_MCP_TOKEN — when set, --http requires `Authorization: Bearer <token>`

Usage:
  python -m vauxtra_mcp.server          # stdio transport (Claude Desktop)
  python -m vauxtra_mcp.server --http   # HTTP transport on 127.0.0.1:9000

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "vauxtra": {
        "command": "python",
        "args": ["-m", "vauxtra_mcp.server"],
        "cwd": "/path/to/vauxtra",
        "env": {
          "VAUXTRA_URL": "http://localhost:8888",
          "VAUXTRA_API_KEY": "vx_..."
        }
      }
    }
  }
"""
import hmac
import os
import sys

import vauxtra_mcp.tools.admin  # noqa: F401
import vauxtra_mcp.tools.monitoring  # noqa: F401
import vauxtra_mcp.tools.operations  # noqa: F401
import vauxtra_mcp.tools.providers  # noqa: F401

# Register all tool modules (decorators fire at import time)
import vauxtra_mcp.tools.services  # noqa: F401
import vauxtra_mcp.tools.templates  # noqa: F401

# Import the shared mcp instance first
from vauxtra_mcp.app import mcp  # noqa: F401

# Loopback, not 0.0.0.0. The HTTP transport holds an API key that can reach every Vauxtra
# route and authenticates nobody unless VAUXTRA_MCP_TOKEN is set: binding every interface
# handed that key's privileges to anyone who could reach the port. Publishing it takes a
# deliberate VAUXTRA_MCP_HOST and a VAUXTRA_MCP_TOKEN.
_DEFAULT_HTTP_HOST = "127.0.0.1"
_DEFAULT_HTTP_PORT = 9000

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

_MIN_TOKEN_LENGTH = 32


def _http_bind() -> tuple[str, int]:
    host = os.environ.get("VAUXTRA_MCP_HOST", _DEFAULT_HTTP_HOST).strip() or _DEFAULT_HTTP_HOST
    try:
        port = int(os.environ.get("VAUXTRA_MCP_PORT", "") or _DEFAULT_HTTP_PORT)
    except ValueError:
        port = _DEFAULT_HTTP_PORT
    token = os.environ.get("VAUXTRA_MCP_TOKEN", "").strip()
    if token and len(token) < _MIN_TOKEN_LENGTH:
        # B18: with a token the Host and Origin checks are skipped and nothing limits the
        # attempts, so the token alone stands between the network and VAUXTRA_API_KEY.
        raise SystemExit(
            f"VAUXTRA_MCP_TOKEN is too short: use at least {_MIN_TOKEN_LENGTH} random "
            "characters (for example `openssl rand -hex 32`)."
        )
    if host not in _LOOPBACK_HOSTS and not token:
        # A warning was not enough: on a LAN address any client that sends no `Origin`
        # (anything but a browser) got the full rights of VAUXTRA_API_KEY.
        raise SystemExit(
            f"Refusing to listen on {host}:{port} without VAUXTRA_MCP_TOKEN: anyone who can "
            "reach this port would get the full privileges of VAUXTRA_API_KEY. Set "
            "VAUXTRA_MCP_TOKEN to a long random value, or keep VAUXTRA_MCP_HOST on 127.0.0.1."
        )
    return host, port


def _host_only(value: str) -> str:
    """`Host` or `Origin` reduced to the bare hostname, lowercased."""
    value = value.strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class _HttpGuard:
    """ASGI wrapper for the HTTP transport.

    Loopback alone did not protect the bridge: any web page open in a browser on the same
    machine can reach 127.0.0.1, and with DNS rebinding its requests even pass as same-origin.
    So, without a token: the `Host` header must name the interface the bridge listens on (or
    loopback), and a browser `Origin` must be local.

    With a token (`VAUXTRA_MCP_TOKEN`), every request must carry it and the Host and Origin
    checks are skipped: a rebound or cross-site page cannot know the token, and a reverse
    proxy forwarding its public name (`Host: mcp.example.com`) must not get 421.
    """

    def __init__(self, app, bind_host: str, token: str = ""):
        self.app = app
        # Kept as bytes: the header arrives as bytes, and decoding it as latin-1 made a
        # non-ASCII token unusable.
        self.token = token.strip().encode("utf-8")
        self.allowed_hosts = set(_LOOPBACK_HOSTS)
        # A wildcard bind is not a name a client can send, so it adds nothing: listening on
        # every interface used to switch the Host check off entirely, which is exactly the
        # setup where DNS rebinding matters.
        if bind_host not in ("0.0.0.0", "::"):
            self.allowed_hosts.add(_host_only(bind_host))

    async def _deny(self, scope, send, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            # Closing before the accept is how ASGI refuses a handshake (the server answers 403).
            await send({"type": "websocket.close", "code": 1008})
            return
        body = message.encode()
        headers = [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            # RFC 6750: a 401 names the scheme the client is expected to use.
            headers.append((b"www-authenticate", b'Bearer realm="vauxtra-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        # WebSocket handshakes go through the same checks: the transport does not use them
        # today, but nothing should reach the app without them if it ever does.
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if self.token:
            presented = b""
            for key, value in scope.get("headers", []):
                if key.lower() == b"authorization":
                    presented = value
                    break
            scheme, _, value = presented.partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(value.strip(), self.token):
                return await self._deny(scope, send, 401, "Missing or invalid bearer token")
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = _host_only(headers.get("host", ""))
        if host not in self.allowed_hosts:
            return await self._deny(scope, send, 421, "Unexpected Host header")
        origin = headers.get("origin", "")
        if origin and _host_only(origin) not in self.allowed_hosts:
            return await self._deny(scope, send, 403, "Cross-origin requests are refused")
        return await self.app(scope, receive, send)


def _run_http() -> None:
    import uvicorn

    host, port = _http_bind()
    app = _HttpGuard(
        mcp.http_app(transport="streamable-http"),
        host,
        os.environ.get("VAUXTRA_MCP_TOKEN", ""),
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    if "--http" in sys.argv:
        _run_http()
    else:
        mcp.run()
