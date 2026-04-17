"""MCP tools — service CRUD and inspection."""
from typing import Any
from vauxtra_mcp.app import mcp
from vauxtra_mcp import client


@mcp.tool()
def list_services() -> list[dict[str, Any]]:
    """List all configured services with their current health status and routing info."""
    r = client.get("/services")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_service(service_id: int) -> dict[str, Any]:
    """Get full details of a single service, including provider assignments and push targets."""
    r = client.get(f"/services/{service_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_service(
    subdomain: str,
    domain: str,
    target_ip: str,
    target_port: int,
    forward_scheme: str = "http",
    expose_mode: str = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    public_target_mode: str = "manual",
    dns_ip: str = "",
    websocket: bool = False,
    enabled: bool = True,
) -> dict[str, Any]:
    """
    Create a new service (DNS + proxy route).

    expose_mode: 'proxy_dns' for NPM/Traefik + DNS, 'tunnel' for Cloudflare Tunnel.
    public_target_mode: 'manual' (use dns_ip) or 'auto' (detect WAN IP).
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
        "public_target_mode": public_target_mode,
        "dns_ip": dns_ip,
        "websocket": websocket,
        "enabled": enabled,
        "tag_ids": [],
        "environment_ids": [],
        "icon_url": "",
        "extra_proxy_provider_ids": [],
        "extra_dns_provider_ids": [],
    }
    r = client.post("/services", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def update_service(
    service_id: int,
    target_ip: str | None = None,
    target_port: int | None = None,
    forward_scheme: str | None = None,
    subdomain: str | None = None,
    domain: str | None = None,
    dns_ip: str | None = None,
    enabled: bool | None = None,
    websocket: bool | None = None,
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
) -> dict[str, Any]:
    """
    Update specific fields of an existing service.

    Only provided (non-None) fields are changed; omitted fields keep their current values.
    The current service state is fetched first and merged with your overrides.
    """
    current = client.get(f"/services/{service_id}")
    current.raise_for_status()
    payload: dict[str, Any] = current.json()
    for key, value in {
        "target_ip": target_ip,
        "target_port": target_port,
        "forward_scheme": forward_scheme,
        "subdomain": subdomain,
        "domain": domain,
        "dns_ip": dns_ip,
        "enabled": enabled,
        "websocket": websocket,
        "proxy_provider_id": proxy_provider_id,
        "dns_provider_id": dns_provider_id,
    }.items():
        if value is not None:
            payload[key] = value
    r = client.put(f"/services/{service_id}", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_service(service_id: int) -> dict[str, Any]:
    """Delete a service and remove its routes from all configured providers."""
    r = client.delete(f"/services/{service_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def toggle_service(service_id: int, enabled: bool) -> dict[str, Any]:
    """Enable or disable a service without removing its provider routes."""
    current = client.get(f"/services/{service_id}")
    current.raise_for_status()
    payload: dict[str, Any] = {**current.json(), "enabled": enabled}
    r = client.put(f"/services/{service_id}", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def sync_services_from_providers() -> dict[str, Any]:
    """
    Discover existing services from all enabled providers.

    Returns proxy_hosts (from NPM, Traefik, Cloudflare Tunnel) and dns_rewrites
    (from Pi-hole, AdGuard, Cloudflare DNS) that can be imported into Vauxtra.
    """
    r = client.post("/services/sync")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def import_services_from_sync(proxy_hosts: list[dict[str, Any]] | None = None, dns_rewrites: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Import services discovered by sync_services_from_providers.

    Pass the proxy_hosts and/or dns_rewrites arrays from the sync result.
    Returns {"imported": int, "errors": list[str]}.
    """
    payload = {
        "proxy_hosts": proxy_hosts or [],
        "dns_rewrites": dns_rewrites or [],
    }
    r = client.post("/services/import", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_service_topology() -> dict[str, Any]:
    """
    Get a topology view mapping services to their providers.

    Returns which services are assigned to which proxy, DNS, and tunnel providers.
    """
    r = client.get("/services/topology")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def discover_docker_containers(endpoint_id: int | None = None) -> list[dict[str, Any]]:
    """
    Discover running Docker containers from a Docker endpoint.

    Returns container info with suggested subdomain, port, and routing config.
    endpoint_id: optional, uses default endpoint if not specified.
    """
    params = {"endpoint_id": endpoint_id} if endpoint_id else {}
    r = client.get("/docker/containers", params=params)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_docker_endpoints() -> list[dict[str, Any]]:
    """List configured Docker endpoints (hosts) for container discovery."""
    r = client.get("/docker/endpoints")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def import_docker_containers(
    domain: str,
    containers: list[dict[str, Any]],
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    dns_ip: str = "",
    endpoint_id: int | None = None,
) -> dict[str, Any]:
    """
    Import Docker containers as services.

    domain: target domain (e.g., "example.com")
    containers: list of containers from discover_docker_containers
    proxy_provider_id: optional reverse proxy provider
    dns_provider_id: optional DNS provider
    dns_ip: public IP for DNS records (optional)
    """
    payload = {
        "domain": domain,
        "containers": containers,
        "proxy_provider_id": proxy_provider_id,
        "dns_provider_id": dns_provider_id,
        "dns_ip": dns_ip,
        "endpoint_id": endpoint_id,
    }
    r = client.post("/docker/import", json=payload)
    r.raise_for_status()
    return r.json()
