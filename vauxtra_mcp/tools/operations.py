"""MCP tools — preflight, dry-run, drift detection, and reconcile."""
from typing import Any
from vauxtra_mcp.app import mcp
from vauxtra_mcp import client


@mcp.tool()
def run_preflight(
    subdomain: str,
    domain: str,
    target_ip: str,
    target_port: int,
    forward_scheme: str = "http",
    expose_mode: str = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    tunnel_hostname: str = "",
    public_target_mode: str = "manual",
    dns_ip: str = "",
    service_id: int | None = None,
) -> dict[str, Any]:
    """
    Run preflight checks before creating or updating a service.

    Returns blocking and non-blocking check results:
    - Route conflict detection
    - TCP reachability of the target
    - Provider connection tests
    - DNS target resolution
    """
    payload: dict[str, Any] = {
        "subdomain": subdomain,
        "domain": domain,
        "target_ip": target_ip,
        "target_port": target_port,
        "forward_scheme": forward_scheme,
        "expose_mode": expose_mode,
        "proxy_provider_id": proxy_provider_id,
        "dns_provider_id": dns_provider_id,
        "tunnel_provider_id": tunnel_provider_id,
        "tunnel_hostname": tunnel_hostname,
        "public_target_mode": public_target_mode,
        "dns_ip": dns_ip,
        "service_id": service_id,
        "tag_ids": [],
        "environment_ids": [],
        "icon_url": "",
        "extra_proxy_provider_ids": [],
        "extra_dns_provider_ids": [],
        "websocket": False,
        "enabled": True,
    }
    r = client.post("/services/preflight", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def dry_run_push(service_id: int) -> dict[str, Any]:
    """
    Simulate pushing a service to all configured providers without making any changes.

    Returns the list of planned proxy and DNS actions, and whether anything would change.
    """
    r = client.post(f"/services/{service_id}/push/dry-run")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def push_service(service_id: int) -> dict[str, Any]:
    """
    Push a service to all configured providers (proxy + DNS).

    Use dry_run_push first to preview changes.
    """
    r = client.post(f"/services/{service_id}/push")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def check_drift(service_id: int) -> dict[str, Any]:
    """
    Compare the expected service state against what is actually configured in providers.

    Returns a list of issues (errors and warnings) if discrepancies are detected.
    """
    r = client.get(f"/services/{service_id}/drift")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def reconcile_service(service_id: int) -> dict[str, Any]:
    """
    Run drift detection, push corrections to providers, then verify drift is resolved.

    Returns before/after drift states and push result.
    """
    r = client.post(f"/services/{service_id}/reconcile")
    r.raise_for_status()
    return r.json()
