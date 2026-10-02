"""MCP tools — service template CRUD."""

from typing import Annotated, Any, Literal

from pydantic import Field

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def list_templates() -> list[dict[str, Any]]:
    """List all service templates. Templates provide pre-configured defaults for new services."""
    r = client.get("/templates")
    client.check(r)
    return r.json()


@mcp.tool()
def get_template(template_id: int) -> dict[str, Any]:
    """Get full details of a single service template."""
    r = client.get(f"/templates/{template_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def create_template(
    name: str,
    description: str = "",
    forward_scheme: Literal["http", "https"] = "http",
    target_port: Annotated[int, Field(ge=1, le=65535)] | None = None,
    websocket: bool = False,
    expose_mode: Literal["proxy_dns", "tunnel"] = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    public_target_mode: Literal["auto", "manual"] = "manual",
    domain: str = "",
    dns_ip: str = "",
    tag_ids: list[int] | None = None,
    environment_ids: list[int] | None = None,
    icon_url: str = "",
) -> dict[str, Any]:
    """Create a service template (common settings for a class of services).

    Refused with 422: forward_scheme not http/https, expose_mode not proxy_dns/tunnel,
    public_target_mode not manual/auto, target_port outside 1-65535, or an unknown key.
    domain and dns_ip may be empty, but a present value must be valid. Provider ids,
    tag_ids and environment_ids must exist (400 naming the id). Kept in sync with
    TemplateIn by scripts/check_api_mcp_parity.py.
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
        "environment_ids": environment_ids or [],
        "icon_url": icon_url,
    }
    r = client.post("/templates", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def update_template(
    template_id: int,
    name: str,
    description: str = "",
    forward_scheme: Literal["http", "https"] = "http",
    target_port: Annotated[int, Field(ge=1, le=65535)] | None = None,
    websocket: bool = False,
    expose_mode: Literal["proxy_dns", "tunnel"] = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    public_target_mode: Literal["auto", "manual"] = "manual",
    domain: str = "",
    dns_ip: str = "",
    tag_ids: list[int] | None = None,
    environment_ids: list[int] | None = None,
    icon_url: str = "",
) -> dict[str, Any]:
    """Replace a service template's settings (full replacement, not a patch).

    Read it with `get_template` and send back what should not change. Same validation as
    `create_template`: 422 for an invalid forward_scheme, expose_mode, public_target_mode,
    target_port or an unknown key; 400 for an unknown provider, tag or environment id.
    domain and dns_ip may be empty.
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
        "environment_ids": environment_ids or [],
        "icon_url": icon_url,
    }
    r = client.put(f"/templates/{template_id}", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def delete_template(template_id: int) -> dict[str, Any]:
    """Delete a service template by ID. Does not affect existing services created from the template."""
    r = client.delete(f"/templates/{template_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def apply_template(
    template_id: int,
    subdomain: str,
    target_ip: str,
    target_port: Annotated[int, Field(ge=1, le=65535)] | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Create a service from a template; arguments override the template's values.

    Refused without sending anything when neither side provides a domain or a port.
    Returns what POST /api/services answers: id, fqdn and an `errors` list. Non-empty
    `errors` (HTTP 207) means the service exists but some provider step failed, e.g. a
    provider the template names was deleted: report which step.
    """
    r = client.get(f"/templates/{template_id}/apply")
    client.check(r)
    defaults = r.json()

    resolved_port = target_port if target_port is not None else defaults.get("target_port")
    resolved_domain = domain if domain is not None else defaults.get("domain")
    missing = [
        field_name
        for field_name, value in (("target_port", resolved_port), ("domain", resolved_domain))
        if value is None or value == ""
    ]
    if missing:
        template_name = defaults.get("_template_name") or template_id
        raise ValueError(
            f"template {template_name!r} sets no {' and no '.join(missing)}; pass "
            f"{' and '.join(missing)} to apply_template, or give the template a default"
        )

    payload: dict[str, Any] = {
        "subdomain": subdomain,
        "target_ip": target_ip,
        "target_port": resolved_port,
        "domain": resolved_domain,
        "forward_scheme": defaults.get("forward_scheme", "http"),
        "websocket": defaults.get("websocket", False),
        "expose_mode": defaults.get("expose_mode", "proxy_dns"),
        "proxy_provider_id": defaults.get("proxy_provider_id"),
        "dns_provider_id": defaults.get("dns_provider_id"),
        "tunnel_provider_id": defaults.get("tunnel_provider_id"),
        "public_target_mode": defaults.get("public_target_mode", "manual"),
        "dns_ip": defaults.get("dns_ip", ""),
        "tag_ids": defaults.get("tag_ids", []),
        "environment_ids": defaults.get("environment_ids", []),
        "icon_url": defaults.get("icon_url", ""),
        "enabled": True,
    }

    svc_r = client.post("/services", json=payload)
    client.check(svc_r)
    return svc_r.json()
