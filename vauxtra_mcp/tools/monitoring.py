"""MCP tools — system health, logs, and certificates."""
from typing import Any
from vauxtra_mcp.app import mcp
from vauxtra_mcp import client


@mcp.tool()
def get_health() -> dict[str, Any]:
    """
    Get the overall Vauxtra system health.

    Returns database connectivity, API latency, and disk usage.
    """
    r = client.get("/health")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_logs(level: str | None = None, page: int = 1, per_page: int = 50) -> dict[str, Any]:
    """
    Retrieve recent operational logs.

    level: filter by 'ok', 'info', 'warn', or 'error'.
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if level:
        params["level"] = level
    r = client.get("/logs", params=params)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_certificates() -> list[dict[str, Any]]:
    """List SSL certificates managed by proxy providers (e.g., NPM)."""
    r = client.get("/certificates")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_certificate_expiry() -> dict[str, Any]:
    """
    List all SSL certificates with their expiry dates and remaining days.

    Returns {"certificates": [...], "expiring_soon_count": int, "total": int}.
    Flags certificates expiring within 30 days.
    """
    r = client.get("/certificates/expiry")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def check_all_services() -> dict[str, Any]:
    """Trigger a manual health check for all services and return the results."""
    r = client.post("/services/check-all")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_stats() -> dict[str, Any]:
    """Return global counters: number of services, providers, and log entries."""
    r = client.get("/stats")
    r.raise_for_status()
    return r.json()
