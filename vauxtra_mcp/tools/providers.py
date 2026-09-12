"""MCP tools — provider management and health checks."""
from typing import Any, Literal

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def list_providers() -> list[dict[str, Any]]:
    """List all configured providers (NPM, Zoraxy, Traefik, Pi-hole, AdGuard, Cloudflare, etc.)."""
    r = client.get("/providers")
    client.check(r)
    return r.json()


@mcp.tool()
def get_provider_types() -> list[dict[str, Any]]:
    """Return all supported provider types with their capabilities and required fields."""
    r = client.get("/providers/types")
    client.check(r)
    return r.json()


@mcp.tool()
def create_provider(
    name: str,
    type: Literal[
        "adguard",
        "cloudflare",
        "cloudflare_tunnel",
        "desec",
        "npm",
        "pihole",
        "powerdns",
        "technitium",
        "traefik",
        "zoraxy",
    ],
    url: str = "",
    username: str = "",
    password: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create a new provider integration.

    Args:
        name: Display name for this provider.
        type: One of the ten types the API knows. The list used to be repeated in this
            docstring and had lost `powerdns` and `desec`, which the API has accepted for
            two releases; it is a `Literal` now, so the schema and the route cannot drift.
        url: Connection URL (e.g. http://npm:81). May be left empty for `cloudflare`,
            `cloudflare_tunnel` and `desec`, whose API endpoint the route fills in; every
            other type is refused with 422 without one.
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
    client.check(r)
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
    client.check(r)
    return r.json()


@mcp.tool()
def delete_provider(provider_id: int, force: bool = False) -> dict[str, Any]:
    """Delete a provider by ID.

    Refuses with 409 while services still reference it, and the error carries the list
    (`detail.services`, each with `id`, `fqdn` and the `roles` it fills). Call again with
    `force=True` to delete anyway: those services keep their public hostname but lose the
    link, so nothing is pushed for them until another provider is chosen.
    """
    r = client.delete(f"/providers/{provider_id}", params={"force": "true"} if force else None)
    client.check(r)
    return r.json()


@mcp.tool()
def test_provider(provider_id: int) -> dict[str, Any]:
    """
    Test the connection to a provider and validate its credentials/permissions.

    Returns a structured result with per-check pass/fail details.
    """
    r = client.post(f"/providers/{provider_id}/validate")
    client.check(r)
    return r.json()


@mcp.tool()
def test_provider_connection(provider_id: int) -> dict[str, Any]:
    """Run the provider connectivity test endpoint and return structured diagnostics."""
    r = client.post(f"/providers/{provider_id}/test")
    client.check(r)
    return r.json()


@mcp.tool()
def validate_provider_draft(
    type: Literal[
        "adguard",
        "cloudflare",
        "cloudflare_tunnel",
        "desec",
        "npm",
        "pihole",
        "powerdns",
        "technitium",
        "traefik",
        "zoraxy",
    ],
    url: str = "",
    username: str = "",
    password: str = "",
    extra: dict[str, Any] | None = None,
    hostname_hint: str = "",
    write_probe: bool = False,
) -> dict[str, Any]:
    """
    Validate a provider draft before creation (no DB write).

    Same fields as `create_provider` minus the name, which nothing stores here. `url` is
    optional for the same reason and in the same three cases: `cloudflare`,
    `cloudflare_tunnel` and `desec` have a known endpoint the route fills in. Declaring it
    required here would have forced a caller to type an address the API already knows.
    """
    r = client.post("/providers/validate-draft", json={
        "type": type,
        "url": url,
        "username": username,
        "password": password,
        "extra": extra or {},
        "hostname_hint": hostname_hint,
        "write_probe": write_probe,
    })
    client.check(r)
    return r.json()


@mcp.tool()
def get_provider_health(provider_id: int) -> dict[str, Any]:
    """Get the current health status of a specific provider."""
    r = client.get(f"/providers/{provider_id}/health")
    client.check(r)
    return r.json()


@mcp.tool()
def get_all_providers_health() -> dict[str, Any]:
    """Batch health check for all enabled providers. Returns status per provider ID."""
    r = client.get("/providers/health")
    client.check(r)
    return r.json()


@mcp.tool()
def get_tunnel_health() -> dict[str, Any]:
    """Get the aggregate health status of all Cloudflare Tunnel providers."""
    r = client.get("/providers/tunnels/health")
    client.check(r)
    return r.json()
