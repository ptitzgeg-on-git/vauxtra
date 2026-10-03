"""MCP tools — service CRUD and inspection."""
from typing import Annotated, Any, Literal

from pydantic import Field

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp

# Exactly the keys ServiceIn accepts: an allowlist, so computed columns from a GET are
# never sent back.
_SERVICE_WRITABLE_KEYS = (
    "subdomain", "domain", "target_ip", "target_port", "forward_scheme", "websocket",
    "enabled", "dns_provider_id", "proxy_provider_id", "tunnel_provider_id",
    "expose_mode", "public_target_mode", "auto_update_dns", "tunnel_hostname", "dns_ip",
    "icon_url", "extra_proxy_provider_ids", "extra_dns_provider_ids",
)


def _service_to_payload(current: dict[str, Any]) -> dict[str, Any]:
    """Turn a GET /services/{id} body into a valid PUT body.

    The GET serializes tags and environments as objects under `tags`/`environments`; the
    PUT expects `tag_ids`/`environment_ids` and treats an omitted list as "remove them
    all", so the ids are rebuilt here to keep the labels.
    """
    payload = {k: current[k] for k in _SERVICE_WRITABLE_KEYS if k in current}
    payload["tag_ids"] = [
        int(t["id"]) for t in current.get("tags") or [] if isinstance(t, dict) and "id" in t
    ]
    payload["environment_ids"] = [
        int(e["id"]) for e in current.get("environments") or [] if isinstance(e, dict) and "id" in e
    ]
    return payload


@mcp.tool()
def list_services() -> list[dict[str, Any]]:
    """List all configured services with their current health status and routing info."""
    r = client.get("/services")
    client.check(r)
    return r.json()


@mcp.tool()
def get_service(service_id: int) -> dict[str, Any]:
    """Get full details of a single service, including provider assignments and push targets."""
    r = client.get(f"/services/{service_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def create_service(
    subdomain: str,
    domain: str,
    target_ip: str,
    target_port: Annotated[int, Field(ge=0, le=65535)],
    forward_scheme: Literal["http", "https"] = "http",
    expose_mode: Literal["proxy_dns", "tunnel"] = "proxy_dns",
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tunnel_provider_id: int | None = None,
    public_target_mode: Literal["auto", "manual"] = "manual",
    dns_ip: str = "",
    websocket: bool = False,
    enabled: bool = True,
    tag_ids: list[int] | None = None,
    environment_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Create a service (DNS record and proxy or tunnel route).

    expose_mode: 'proxy_dns' (NPM/Traefik + DNS) or 'tunnel' (Cloudflare Tunnel).
    public_target_mode: 'manual' (use dns_ip) or 'auto' (detect WAN IP).
    target_port: 0 means DNS only; refused with a proxy or in tunnel mode.
    tag_ids, environment_ids: ids from list_tags / list_environments; an unknown id fails
    the whole call with 400. Allowed values are Literals checked against ServiceIn by
    scripts/check_api_mcp_parity.py.

    The answer has an `errors` list. Non-empty means the service row exists but was not
    fully published (HTTP 207); report what failed and use push_service to retry.
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
        "tag_ids": tag_ids or [],
        "environment_ids": environment_ids or [],
        "icon_url": "",
        "extra_proxy_provider_ids": [],
        "extra_dns_provider_ids": [],
    }
    r = client.post("/services", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def update_service(
    service_id: int,
    target_ip: str | None = None,
    target_port: Annotated[int, Field(ge=0, le=65535)] | None = None,
    forward_scheme: Literal["http", "https"] | None = None,
    subdomain: str | None = None,
    domain: str | None = None,
    dns_ip: str | None = None,
    enabled: bool | None = None,
    websocket: bool | None = None,
    proxy_provider_id: int | None = None,
    dns_provider_id: int | None = None,
    tag_ids: list[int] | None = None,
    environment_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Update specific fields of a service; omitted (None) fields keep their values.

    The current service is fetched and merged with your overrides, then sent as a PUT,
    which republishes it. tag_ids / environment_ids replace the whole set when given
    (`[]` clears); to add one, send the existing ids plus the new. For label-only changes
    use `set_service_labels`, which calls no provider.

    The answer has an `errors` list: the row is saved either way, and each entry is a
    provider that was not brought in line. Report them.
    """
    current = client.get(f"/services/{service_id}")
    client.check(current)
    payload: dict[str, Any] = _service_to_payload(current.json())
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
        "tag_ids": tag_ids,
        "environment_ids": environment_ids,
    }.items():
        if value is not None:
            payload[key] = value
    r = client.put(f"/services/{service_id}", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def set_service_labels(
    service_id: int,
    tag_ids: list[int] | None = None,
    environment_ids: list[int] | None = None,
    icon_url: str | None = None,
) -> dict[str, Any]:
    """Set a service's tags, environments or icon without calling any provider (PATCH).

    An omitted or None argument keeps its value; a list replaces the set (`tag_ids=[]`
    clears, `icon_url=""` removes the icon). Refused with nothing written: 400 with no
    argument or an unknown id, 404 for an unknown service. Returns the service as
    `get_service` does, with no `errors` key.
    """
    payload: dict[str, Any] = {}
    if tag_ids is not None:
        payload["tag_ids"] = tag_ids
    if environment_ids is not None:
        payload["environment_ids"] = environment_ids
    if icon_url is not None:
        payload["icon_url"] = icon_url
    r = client.patch(f"/services/{service_id}", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def delete_service(service_id: int) -> dict[str, Any]:
    """Delete a service and take its routes down from every provider that held one.

    `ok` is true whenever the service is gone from Vauxtra, which it is even when a provider
    refused -- answering false would only push a caller into retrying a delete that can now
    answer nothing but 404. So `ok` is not the result: `errors` is. Each sentence in it is a
    record this call could not withdraw, and every one of those is still live on its
    provider, still resolving, with nothing in Vauxtra left pointing at it. Report them.
    """
    r = client.delete(f"/services/{service_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def toggle_service(service_id: int, enabled: bool) -> dict[str, Any]:
    """Enable or disable a service without removing its configuration.

    Disabling withdraws its routes (tunnel rule, proxy host suspended or deleted, DNS
    record, extra targets); enabling restores them. The row is saved either way, so a
    non-empty `errors` after disabling means the service is off in Vauxtra but still
    reachable: say so. Errors raised by the primary proxy provider only go to the log
    (`get_logs`), not to `errors`.
    """
    current = client.get(f"/services/{service_id}")
    client.check(current)
    payload: dict[str, Any] = {**_service_to_payload(current.json()), "enabled": enabled}
    r = client.put(f"/services/{service_id}", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def sync_services_from_providers() -> dict[str, Any]:
    """Discover existing routes and records from all enabled providers.

    Returns proxy_hosts and dns_rewrites that can be imported, plus `providers` (one line
    per integration: id, name, type, ok, count, and the error when ok is false, meaning
    the scan is incomplete) and `declared_domains`. Each row has `_zone` and `_declared`;
    import only `_declared` rows unless told otherwise. A name answered by two
    integrations appears once per integration; the import keeps the first.
    """
    r = client.post("/services/sync")
    client.check(r)
    return r.json()


@mcp.tool()
def import_services_from_sync(proxy_hosts: list[dict[str, Any]] | None = None, dns_rewrites: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Import rows from sync_services_from_providers (proxy_hosts and/or dns_rewrites).

    Every row lands in one outcome: `imported` (new services), `linked` (an existing
    service gained its missing DNS half), `skipped` (passed over on purpose, e.g. already
    tracked or extra names of a multi-name host; not a failure) or `errors` (rows the
    operator must fix). A DNS row matching a proxy host in the same payload is merged
    and counted once.
    """
    payload = {
        "proxy_hosts": proxy_hosts or [],
        "dns_rewrites": dns_rewrites or [],
    }
    r = client.post("/services/import", json=payload)
    client.check(r)
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
    client.check(r)
    return r.json()


@mcp.tool()
def list_docker_endpoints() -> list[dict[str, Any]]:
    """List configured Docker endpoints (hosts) for container discovery."""
    r = client.get("/docker/endpoints")
    client.check(r)
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
    """Import Docker containers as services.

    domain: target domain; containers: from discover_docker_containers;
    proxy_provider_id, dns_provider_id, dns_ip: optional.
    Returns `imported` (count), `skipped` (already tracked, nothing to do) and `errors`
    (containers refused, e.g. no reachable address or port). Report the sentences, not
    just the count.
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
    client.check(r)
    return r.json()


@mcp.tool()
def get_services_history() -> dict[str, Any]:
    """Return the last 24h uptime history for all services."""
    r = client.get("/services/history")
    client.check(r)
    return r.json()


@mcp.tool()
def suggest_public_targets(proxy_provider_id: int | None = None) -> dict[str, Any]:
    """Suggest WAN/public targets for DNS based on current connectivity and provider context."""
    params = {"proxy_provider_id": proxy_provider_id} if proxy_provider_id is not None else {}
    r = client.get("/services/public-target/suggest", params=params)
    client.check(r)
    return r.json()


@mcp.tool()
def check_service_health(service_id: int) -> dict[str, Any]:
    """Run a live health/TCP and DNS check for one service."""
    # POST since 1.5.0: the route writes `status`, `last_checked` and an uptime event, so
    # it now asks for the `write` scope like every other mutation.
    r = client.post(f"/services/{service_id}/check")
    client.check(r)
    return r.json()


@mcp.tool()
def bulk_service_action(
    service_ids: list[int], action: Literal["enable", "disable", "delete"]
) -> dict[str, Any]:
    """Run a bulk action on services: enable, disable, or delete.

    `affected` counts rows changed in Vauxtra (written before provider work). `errors`
    has one sentence per provider refusal; after a disable those hostnames are still
    served. Read `errors` before reporting the action done.
    """
    r = client.post("/services/bulk", json={"ids": service_ids, "action": action})
    client.check(r)
    return r.json()


@mcp.tool()
def add_docker_endpoint(name: str, docker_host: str, enabled: bool = True) -> dict[str, Any]:
    """Create a Docker endpoint for container discovery."""
    r = client.post(
        "/docker/endpoints",
        json={
            "name": name,
            "docker_host": docker_host,
            "enabled": enabled,
        },
    )
    client.check(r)
    return r.json()


@mcp.tool()
def set_default_docker_endpoint(endpoint_id: int) -> dict[str, Any]:
    """Mark one Docker endpoint as default for discovery/import."""
    r = client.post(f"/docker/endpoints/{endpoint_id}/default")
    client.check(r)
    return r.json()


@mcp.tool()
def test_docker_endpoint(endpoint_id: int) -> dict[str, Any]:
    """Test connectivity to one Docker endpoint and return visible container count."""
    r = client.post(f"/docker/endpoints/{endpoint_id}/test")
    client.check(r)
    return r.json()


@mcp.tool()
def delete_docker_endpoint(endpoint_id: int) -> dict[str, Any]:
    """Delete a Docker endpoint (requires at least one endpoint to remain)."""
    r = client.delete(f"/docker/endpoints/{endpoint_id}")
    client.check(r)
    return r.json()
