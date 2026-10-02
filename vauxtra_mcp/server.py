"""Vauxtra MCP server: exposes Vauxtra's DNS and proxy management as MCP tools.

Environment variables:
  VAUXTRA_URL       base URL of the Vauxtra instance (default: http://localhost:8888)
  VAUXTRA_API_KEY   API key from Settings > API Keys (Bearer auth)
  VAUXTRA_MCP_HOST  interface --http binds to (default: 127.0.0.1)
  VAUXTRA_MCP_PORT  port --http binds to (default: 9000)

Usage:
  python -m vauxtra_mcp.server          # stdio transport (Claude Desktop)
  python -m vauxtra_mcp.server --http   # HTTP transport on 127.0.0.1:9000

Client configuration examples are in vauxtra_mcp/README.md.
"""
import os
import sys

# Imported for their @mcp.tool side effects; order does not matter.
import vauxtra_mcp.tools.admin  # noqa: F401
import vauxtra_mcp.tools.monitoring  # noqa: F401
import vauxtra_mcp.tools.operations  # noqa: F401
import vauxtra_mcp.tools.providers  # noqa: F401
import vauxtra_mcp.tools.services  # noqa: F401
import vauxtra_mcp.tools.templates  # noqa: F401

# The instance main() runs below, carrying every tool the imports above registered on it.
from vauxtra_mcp.app import mcp

# Loopback by default: the HTTP transport has no auth of its own but holds an API key.
# Exposing it needs an explicit VAUXTRA_MCP_HOST and prints a warning.
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


def _http_run_kwargs() -> dict:
    host, port = _http_bind()
    # Loopback does not stop DNS rebinding from the operator's own browser, so "auto"
    # refuses a Host or Origin that is not the loopback address.
    return {
        "transport": "streamable-http",
        "host": host,
        "port": port,
        "host_origin_protection": "auto",
    }


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(**_http_run_kwargs())
    else:
        mcp.run()
