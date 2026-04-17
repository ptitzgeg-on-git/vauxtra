"""MCP tools — provider management and health checks."""
from typing import Any
from vauxtra_mcp.app import mcp
from vauxtra_mcp import client


@mcp.tool()
def list_providers() -> list[dict[str, Any]]:
    """List all configured providers (NPM, Traefik, Pi-hole, AdGuard, Cloudflare, etc.)."""
    r = client.get("/providers")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_provider_types() -> list[dict[str, Any]]:
    """Return all supported provider types with their capabilities and required fields."""
    r = client.get("/providers/types")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_provider(
    name: str,
    type: str,
    url: str = "",
    username: str = "",
    password: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create a new provider integration.

    Args:
        name: Display name for this provider.
        type: Provider type (npm, traefik, cloudflare, cloudflare_tunnel, pihole, adguard).
        url: Connection URL (e.g. http://npm:81 or https://api.cloudflare.com).
        username: Username or email for authentication.
        password: Password or API token.
        extra: Additional provider-specific config (e.g. zone_id, account_id, tunnel_id).
    """
    r = client.post("/providers", json={
        "name": name,
        "type": type,
        "url": url,
        "username": username,
        "password": password,
        "extra": extra or {},
    })
    r.raise_for_status()
    return r.json()


@mcp.tool()
def update_provider(
    provider_id: int,
    name: str | None = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    enabled: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Update an existing provider. Only provided fields are changed.

    Args:
        provider_id: ID of the provider to update.
        name: New display name.
        url: New connection URL.
        username: New username/email.
        password: New password/token (re-encrypted on save).
        enabled: Enable or disable the provider.
        extra: Updated provider-specific config.
    """
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if url is not None:
        payload["url"] = url
    if username is not None:
        payload["username"] = username
    if password is not None:
        payload["password"] = password
    if enabled is not None:
        payload["enabled"] = enabled
    if extra is not None:
        payload["extra"] = extra
    r = client.put(f"/providers/{provider_id}", json=payload)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_provider(provider_id: int) -> dict[str, Any]:
    """Delete a provider by ID. Services referencing this provider will lose their link."""
    r = client.delete(f"/providers/{provider_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def test_provider(provider_id: int) -> dict[str, Any]:
    """
    Test the connection to a provider and validate its credentials/permissions.

    Returns a structured result with per-check pass/fail details.
    """
    r = client.post(f"/providers/{provider_id}/validate")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_provider_health(provider_id: int) -> dict[str, Any]:
    """Get the current health status of a specific provider."""
    r = client.get(f"/providers/{provider_id}/health")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_all_providers_health() -> dict[str, Any]:
    """Batch health check for all enabled providers. Returns status per provider ID."""
    r = client.get("/providers/health")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_tunnel_health() -> dict[str, Any]:
    """Get the aggregate health status of all Cloudflare Tunnel providers."""
    r = client.get("/providers/tunnels/health")
    r.raise_for_status()
    return r.json()
