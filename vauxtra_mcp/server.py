"""
Vauxtra MCP Server

Exposes Vauxtra's DNS & proxy management capabilities as MCP tools
for integration with AI assistants (Claude Desktop, Cursor, etc.).

Environment variables:
  VAUXTRA_URL      — Base URL of the Vauxtra instance (default: http://localhost:8888)
  VAUXTRA_API_KEY  — API key created in Vauxtra Settings → API Keys (Bearer auth)

Usage:
  python -m vauxtra_mcp.server          # stdio transport (Claude Desktop)
  python -m vauxtra_mcp.server --http   # HTTP/SSE transport on port 9000

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
import sys

# Import the shared mcp instance first
from vauxtra_mcp.app import mcp  # noqa: F401

# Register all tool modules (decorators fire at import time)
import vauxtra_mcp.tools.services    # noqa: F401
import vauxtra_mcp.tools.providers   # noqa: F401
import vauxtra_mcp.tools.operations  # noqa: F401
import vauxtra_mcp.tools.monitoring  # noqa: F401

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=9000)
    else:
        mcp.run()
