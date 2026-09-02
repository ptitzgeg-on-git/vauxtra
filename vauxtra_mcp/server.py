"""
Vauxtra MCP Server

Exposes Vauxtra's DNS & proxy management capabilities as MCP tools
for integration with MCP-compatible clients (Claude Desktop, Cursor, etc.).

Environment variables:
  VAUXTRA_URL      — Base URL of the Vauxtra instance (default: http://localhost:8888)
  VAUXTRA_API_KEY  — API key created in Vauxtra Settings → API Keys (Bearer auth)

  VAUXTRA_MCP_HOST — interface --http binds to (default: 127.0.0.1)
  VAUXTRA_MCP_PORT — port --http binds to (default: 9000)

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

# Loopback, not 0.0.0.0. The HTTP transport carries no authentication of its own while
# holding an API key that can reach every Vauxtra route: binding every interface handed
# that key's privileges to anyone who could reach the port. Publishing it remains possible,
# but it now takes a deliberate VAUXTRA_MCP_HOST and a warning on stderr.
_DEFAULT_HTTP_HOST = "127.0.0.1"
_DEFAULT_HTTP_PORT = 9000

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _http_bind() -> tuple[str, int]:
    host = os.environ.get("VAUXTRA_MCP_HOST", _DEFAULT_HTTP_HOST).strip() or _DEFAULT_HTTP_HOST
    try:
        port = int(os.environ.get("VAUXTRA_MCP_PORT", "") or _DEFAULT_HTTP_PORT)
    except ValueError:
        port = _DEFAULT_HTTP_PORT
    if host not in _LOOPBACK_HOSTS:
        print(
            f"WARNING: the MCP bridge is listening on {host}:{port} with no authentication "
            "of its own. Anyone who can reach this port gets the full privileges of "
            "VAUXTRA_API_KEY. Put it behind a reverse proxy that authenticates, or keep it "
            "on 127.0.0.1 and tunnel to it.",
            file=sys.stderr,
        )
    return host, port


if __name__ == "__main__":
    if "--http" in sys.argv:
        _host, _port = _http_bind()
        mcp.run(transport="streamable-http", host=_host, port=_port)
    else:
        mcp.run()
