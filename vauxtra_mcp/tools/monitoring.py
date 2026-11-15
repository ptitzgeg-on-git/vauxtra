"""MCP tools — system health, logs, and certificates."""
from typing import Any

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def get_health() -> dict[str, Any]:
    """
    Get the overall Vauxtra system health.

    Returns database connectivity, API latency, and disk usage.
    """
    r = client.get("/health")
    client.check(r)
    return r.json()


@mcp.tool()
def get_logs(level: str | None = None, page: int = 1, per_page: int = 50) -> dict[str, Any]:
    """Retrieve recent activity logs. Needs an `admin` key (403 for `write` or `read`).

    The log records sign-ins, password changes, backups and API key creation/revocation.
    level: 'info', 'ok', 'warning' or 'error'; 'warn' is accepted as 'warning' and old
    'warn' rows are included either way.
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if level:
        params["level"] = level
    r = client.get("/logs", params=params)
    client.check(r)
    return r.json()


@mcp.tool()
def get_certificates() -> list[dict[str, Any]]:
    """List SSL certificates managed by proxy providers (e.g., NPM)."""
    r = client.get("/certificates")
    client.check(r)
    return r.json()


@mcp.tool()
def get_certificate_expiry() -> dict[str, Any]:
    """List all SSL certificates with their expiry dates and remaining days.

    Returns {"certificates", "total", "expiring_soon_count", "warn_threshold_days",
    "unreachable"}. `expiring_soon_count` includes already expired certificates; check each
    row's `expired` flag before reporting a deadline. A non-empty `unreachable` lists
    certificate providers that could not be read, so the answer is partial.
    """
    r = client.get("/certificates/expiry")
    client.check(r)
    return r.json()


@mcp.tool()
def check_all_services() -> dict[str, Any]:
    """Trigger a manual health check for every service and return what it measured.

    Counters (`checked`, `ok`, `error`, `skipped`) plus `results`, one entry per probed
    service with its `status` and the `latency_ms` of the probe (`null` when the target never
    answered). Two kinds of service are not probed and are counted in `skipped`, and apart
    in `skipped_tunnel` and `skipped_no_port`: tunnels, checked through their provider, and
    services with `target_port` 0, published in DNS alone with nothing to connect to. So
    `checked` is `ok + error + skipped`, and can exceed `len(results)`.
    """
    r = client.post("/services/check-all")
    client.check(r)
    return r.json()


@mcp.tool()
def get_stats() -> dict[str, Any]:
    """Return the global counters the dashboard is built on.

    `services`, `providers`, `logs` and `tags` are sizes of the estate and count
    everything. `services_ok` and `services_error` are health, and health is only
    counted over services that are enabled: the scheduler checks those alone, and a
    service keeps its last `status` after being disabled, so a disabled failure is a
    frozen reading rather than a live fault. `services_ok + services_error` is
    therefore at most the number of enabled services, and usually less -- a service
    that has never been checked yet is in neither counter.
    """
    r = client.get("/stats")
    client.check(r)
    return r.json()
