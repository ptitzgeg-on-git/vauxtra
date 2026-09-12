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
    icon_url: str = "",
) -> dict[str, Any]:
    """
    Create a service template.

    Templates capture the common settings for a class of services
    (e.g. 'Standard HTTPS app' = HTTPS scheme, port 443, AdGuard DNS, NPM proxy).

    Constrained fields, refused with 422 otherwise: `forward_scheme` is http or https,
    `expose_mode` is proxy_dns or tunnel, `public_target_mode` is manual or auto,
    `target_port` is 1-65535. `domain` and `dns_ip` may be left empty -- that is what makes
    a template a template -- but a value that is present must be a valid one. Every
    `proxy_provider_id`, `dns_provider_id`, `tunnel_provider_id` and `tag_ids` entry has to
    name a row that exists; the route refuses the whole call with 400 and names the id.

    The first three are declared as `Literal` and the port as a bounded `int`, so the schema
    carries what the prose above says and a wrong value is refused here rather than after a
    round trip. Nothing derives them from `TemplateIn`: FastMCP reads this signature, and a
    normal install publishes no OpenAPI document. `scripts/check_api_mcp_parity.py` fails the
    build if they drift from the model.
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
    icon_url: str = "",
) -> dict[str, Any]:
    """Replace a service template's settings.

    This is a full replacement, not a patch: every field takes the value passed here, so read
    the template with `get_template` first and send back what should not change. Without this
    tool the only way to edit a template through the bridge was to delete and recreate it,
    which changes the id anything else refers to.

    Constrained fields, refused with 422 otherwise: `forward_scheme` is http or https,
    `expose_mode` is proxy_dns or tunnel, `public_target_mode` is manual or auto,
    `target_port` is 1-65535. `domain` and `dns_ip` may be left empty -- that is what makes
    a template a template -- but a value that is present must be a valid one. Every
    `proxy_provider_id`, `dns_provider_id`, `tunnel_provider_id` and `tag_ids` entry has to
    name a row that exists; the route refuses the whole call with 400 and names the id.

    The first three are declared as `Literal` and the port as a bounded `int`, so the schema
    carries what the prose above says and a wrong value is refused here rather than after a
    round trip. Nothing derives them from `TemplateIn`: FastMCP reads this signature, and a
    normal install publishes no OpenAPI document. `scripts/check_api_mcp_parity.py` fails the
    build if they drift from the model.
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
    """
    Create a new service from a template.

    Fetches the template defaults, merges them with the provided subdomain / target_ip /
    target_port / domain (an argument wins over the template), then creates the service.
    Returns the created service record.

    `POST /api/services` requires a domain and a port, and a template is allowed to carry
    neither -- that is what lets one template serve several domains. So both stay optional
    here and are merged from the template, and when neither side supplies one the tool
    refuses and sends nothing. It used to fill the gap with `or 80` and `or ""`: the empty
    domain came back as a 422 ("a domain is required"), but 80 did not, because 80 is a
    valid port. Every call that named no port, against a template that sets none, created a
    service pointing at a port nobody had chosen -- and reported success.
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
        "icon_url": defaults.get("icon_url", ""),
        "enabled": True,
    }

    svc_r = client.post("/services", json=payload)
    client.check(svc_r)
    return svc_r.json()
