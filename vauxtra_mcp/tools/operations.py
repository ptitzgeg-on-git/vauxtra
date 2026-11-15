"""MCP tools — preflight, dry-run, drift detection, and reconcile."""
from typing import Annotated, Any, Literal

from pydantic import Field

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def run_preflight(
    subdomain: str,
    domain: str,
    target_ip: str,
    target_port: Annotated[int, Field(ge=0, le=65535)],
    forward_scheme: Literal["http", "https"] = "http",
    expose_mode: Literal["proxy_dns", "tunnel"] = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    tunnel_hostname: str = "",
    public_target_mode: Literal["auto", "manual"] = "manual",
    dns_ip: str = "",
    service_id: int | None = None,
) -> dict[str, Any]:
    """Run preflight checks before creating or updating a service.

    Returns blocking and non-blocking results: route conflicts, target TCP reachability,
    provider connections and DNS target resolution. The body is validated like
    `create_service` (same allowed values and port bounds), plus `service_id` for an edit.
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
    client.check(r)
    return r.json()


@mcp.tool()
def dry_run_push(service_id: int) -> dict[str, Any]:
    """
    Simulate pushing a service to all configured providers without making any changes.

    Returns the list of planned proxy and DNS actions, and whether anything would change.
    A disabled service is withheld rather than published, so its plan describes the
    withdrawal instead: `withheld` is true and the actions are `suspend` and `delete`. On an enabled service whose
    proxy host is switched off, the action is `resume` rather than `update`.
    """
    r = client.post(f"/services/{service_id}/push/dry-run")
    client.check(r)
    return r.json()


@mcp.tool()
def push_service(service_id: int) -> dict[str, Any]:
    """Push a service to all configured providers (proxy + DNS). Preview with `dry_run_push`.

    A disabled service is withdrawn instead (primary proxy host suspended, other routes
    removed): enable it first to publish. On an enabled service the push also lifts a
    suspension it finds.
    """
    r = client.post(f"/services/{service_id}/push")
    client.check(r)
    return r.json()


@mcp.tool()
def check_drift(service_id: int) -> dict[str, Any]:
    """Compare a service's expected state with what its providers hold.

    Returns `issues` (errors and warnings). For a disabled service the issues name
    providers still serving it (`proxy_route_still_served`, `dns_rewrite_still_served`);
    `proxy_route_suspended` is an enabled route switched off, which a push repairs.
    Other DNS integrations answering the name give the warnings `dns_answered_elsewhere`
    or `dns_elsewhere_check_failed`, which `reconcile_service` leaves alone. `ok` is false
    only for errors, so read `issues` to know whether anything differs.
    """
    r = client.get(f"/services/{service_id}/drift")
    client.check(r)
    return r.json()


@mcp.tool()
def reconcile_service(service_id: int) -> dict[str, Any]:
    """
    Run drift detection, push corrections to providers, then verify drift is resolved.

    Returns before/after drift states and push result. The push writes only to the
    integrations the service is published to, so a `dns_answered_elsewhere` warning is
    still in `after`: that record belongs to another integration.
    """
    r = client.post(f"/services/{service_id}/reconcile")
    client.check(r)
    return r.json()
