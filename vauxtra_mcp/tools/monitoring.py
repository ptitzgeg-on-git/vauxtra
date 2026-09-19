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
    """
    Retrieve recent operational logs. Needs an `admin` key, not a `write` or `read` one.

    This is the activity log, and it is the same file `clear_logs` empties: a refused
    sign-in, a successful one, a password change, a secure backup taken, and the name and
    the scopes of every API key created or revoked. It sits with backup and restore rather
    than with the tools that read the estate, so a key minted for a status page is refused
    with 403 and the message names the scope.

    level: filter by 'info', 'ok', 'warning' or 'error'. 'warn' is accepted and means
    'warning' -- the two spellings were both written for a while and the second is the one
    stored, so rows come back reading 'warning' whichever you asked for. Filtering by
    'warning' finds the old 'warn' rows too, so there is no spelling that loses entries.
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
    """
    List all SSL certificates with their expiry dates and remaining days.

    Returns {"certificates": [...], "total": int, "expiring_soon_count": int,
    "warn_threshold_days": int, "unreachable": [...]}. `expiring_soon_count` is
    everything that needs renewing: still valid but inside `warn_threshold_days`
    *plus* already past expiry. Those two are not equally urgent -- a lapsed
    certificate is serving an error to every client reaching that host right now --
    and the figure alone cannot say which it is made of, so read each row's own
    `expired` flag before reporting it as a deadline. `unreachable` names the enabled
    certificate providers this call could not read: the counts above cover only the
    rest, so a non-empty `unreachable` means the answer is partial, not that nothing
    is expiring.
    """
    r = client.get("/certificates/expiry")
    client.check(r)
    return r.json()


@mcp.tool()
def check_all_services() -> dict[str, Any]:
    """Trigger a manual health check for every service and return what it measured.

    Counters (`checked`, `ok`, `error`) plus `results`, one entry per probed service with
    its `status` and the `latency_ms` of the probe (`null` when the target never answered).
    Tunnel services are skipped, so `checked` can exceed `len(results)`.
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
