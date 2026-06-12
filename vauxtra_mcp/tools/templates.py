"""MCP tools — service template CRUD."""

from typing import Any

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def list_templates() -> list[dict[str, Any]]:
    """List all service templates. Templates provide pre-configured defaults for new services."""
    r = client.get("/templates")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_template(template_id: int) -> dict[str, Any]:
    """Get full details of a single service template."""
    r = client.get(f"/templates/{template_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_template(
    name: str,
    description: str = "",
    forward_scheme: str = "http",
    target_port: int | None = None,
    websocket: bool = False,
    expose_mode: str = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    public_target_mode: str = "manual",
    domain: str = "",
    dns_ip: str = "",
    tag_ids: list[int] | None = None,
    icon_url: str = "",
) -> dict[str, Any]:
    """
    Create a service template.

    Templates capture the common settings for a class of services
    (e.g. 'Standard HTTPS app' = HTTPS scheme, port 443, AdGuard DNS, NPM proxy).
    """
    payload: dict[str, Any] = {
        "name": name,
        "description": description,
        "forward_scheme": forward_scheme,
        "target_port": target_port,
        "websocket": websocket,
        "expose_mode": expose_mode,
        "proxy_provider_id": proxy_provider_id,
        "dns_provider_id": dns_provider_id,
        "tunnel_provider_id": tunnel_provider_id,
        "public_target_mode": public_target_mode,
        "domain": domain,
        "dns_ip": dns_ip,
        "tag_ids": tag_ids or [],
        "icon_url": icon_url,
    }
    r = client.post("/templates", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_template(template_id: int) -> dict[str, Any]:
    """Delete a service template by ID. Does not affect existing services created from the template."""
    r = client.delete(f"/templates/{template_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def apply_template(
    template_id: int,
    subdomain: str,
    target_ip: str,
    target_port: int | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """
    Create a new service from a template.

    Fetches the template defaults, merges them with the provided subdomain / target_ip /
    target_port (user-supplied values take precedence), then creates the service.
    Returns the created service record.
    """
    # Fetch template defaults
    r = client.get(f"/templates/{template_id}/apply")
    r.raise_for_status()
    defaults = r.json()

    # Build the service payload
    payload: dict[str, Any] = {
        "subdomain": subdomain,
        "target_ip": target_ip,
        "target_port": target_port or defaults.get("target_port") or 80,
        "domain": domain or defaults.get("domain") or "",
        "forward_scheme": defaults.get("forward_scheme", "http"),
        "websocket": defaults.get("websocket", False),
        "expose_mode": defaults.get("expose_mode", "proxy_dns"),
        "proxy_provider_id": defaults.get("proxy_provider_id"),
        "dns_provider_id": defaults.get("dns_provider_id"),
        "tunnel_provider_id": defaults.get("tunnel_provider_id"),
        "public_target_mode": defaults.get("public_target_mode", "manual"),
        "dns_ip": defaults.get("dns_ip", ""),
        "tag_ids": defaults.get("tag_ids", []),
        "icon_url": defaults.get("icon_url", ""),
        "enabled": True,
    }

    svc_r = client.post("/services", json=payload)
    svc_r.raise_for_status()
    return svc_r.json()
