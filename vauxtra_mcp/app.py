"""Central FastMCP instance — imported by all tool modules to avoid circular imports."""
from fastmcp import FastMCP

# Tool annotations. A client can only tell a harmless read from a write by what the tool
# declares, so every tool that changes nothing says so, and the two that wipe data say that.
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True}

mcp = FastMCP(
    name="Vauxtra",
    instructions=(
        "You are connected to Vauxtra, a self-hosted DNS and reverse proxy management panel. "
        "You can list, create, update, and delete services (routed endpoints), test provider "
        "connections, detect configuration drift, and trigger reconciliation. "
        "Always run preflight checks before creating a service. "
        "Use dry_run_push to preview changes before pushing to providers."
    ),
)
