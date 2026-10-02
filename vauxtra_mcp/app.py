"""Central FastMCP instance — imported by all tool modules to avoid circular imports."""
from fastmcp import FastMCP

from vauxtra_mcp import __version__

mcp = FastMCP(
    name="Vauxtra",
    # Report Vauxtra's version, not FastMCP's default.
    version=__version__,
    instructions=(
        "You are connected to Vauxtra, a self-hosted DNS and reverse proxy management panel. "
        "You can list, create, update, and delete services (routed endpoints), test provider "
        "connections, detect configuration drift, and trigger reconciliation. "
        "Always run preflight checks before creating a service. "
        "Use dry_run_push to preview changes before pushing to providers."
    ),
)
