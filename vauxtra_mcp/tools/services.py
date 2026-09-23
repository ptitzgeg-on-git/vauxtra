"""MCP tools — service CRUD and inspection."""
from typing import Annotated, Any, Literal

from pydantic import Field

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp

# Exactly the keys `ServiceIn` accepts. An allowlist, not a blocklist: a GET also returns
# computed columns, provider names and monitoring state, and a new column added to the
# table would otherwise start leaking into every PUT.
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
    all". Feeding the GET straight back therefore used to erase every tag and every
    environment on each update or toggle.
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
    """
    Create a new service (DNS + proxy route).

    expose_mode: 'proxy_dns' for NPM/Traefik + DNS, 'tunnel' for Cloudflare Tunnel.
    public_target_mode: 'manual' (use dns_ip) or 'auto' (detect WAN IP).

    target_port 0 is a service published in DNS alone -- a machine's A record, a VPN
    endpoint -- which nothing forwards to and nothing probes. It is refused with a proxy or
    in tunnel mode, since both forward to a port.

    tag_ids and environment_ids attach labels. Both default to none, and every id has to
    name a row that exists: `list_tags` and `list_environments` are where they come from,
    and the route refuses the whole call with 400 naming the id it could not find, so a
    wrong id creates nothing rather than a service missing the label it was asked for.

    The bridge used to send `tag_ids: []` here with no parameter to fill it, and
    `update_service` had none either. Eight tools could build a taxonomy that nothing
    could then apply, and a service an agent created stayed unlabelled for good.

    The three `Literal` sets repeat, by hand, the values `ServiceIn` validates. Nothing
    derives them: FastMCP builds the schema from this signature, and a normal install
    publishes no OpenAPI document to read the model from. Declared as plain `str` they
    let a caller spend a round trip discovering that 'ftp' is not a forward scheme --
    and this schema is the only place an agent can learn the answer before calling.
    `scripts/check_api_mcp_parity.py` fails the build if these sets drift from the model.

    The answer carries an `errors` list, and an empty one is the only thing that means the
    service is actually reachable. Storing the row and publishing it are separate steps: the
    route creates the tunnel route, the proxy host and the DNS record first, collects every
    refusal into `errors`, and inserts the row regardless, answering 207 instead of 201 when
    the list is not empty. So a service can come back with an id and an fqdn while nothing
    routes to that fqdn -- no proxy host, or no DNS record, or a tunnel provider that was not
    found. Report a non-empty `errors` as a service that exists in Vauxtra but was never
    published, and name what failed; `push_service` is what retries the publication.
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
    """
    Update specific fields of an existing service.

    Only provided (non-None) fields are changed; omitted fields keep their current values.
    The current service state is fetched first and merged with your overrides.

    tag_ids and environment_ids follow that rule with one edge worth naming: omitted, the
    service keeps the labels it has; a list replaces them all, because `PUT /api/services`
    replaces rather than merges. So `tag_ids=[3]` on a service carrying 1 and 2 leaves it
    carrying 3 alone, and `tag_ids=[]` strips every label. Read the service back with
    `get_service` and send its ids plus the new one to add rather than replace.

    To change labels and nothing else, use `set_service_labels`. This tool sends the
    whole service back through `PUT`, which publishes it again on its proxy or tunnel,
    and a label is not something any provider holds.

    `forward_scheme` carries the same `Literal` as `create_service`: the route validates
    the merged body with `ServiceIn`, so an override it refuses fails the whole update,
    including the fields that were valid.

    The service comes back with an extra `errors` key. Vauxtra's own row is saved before the
    providers are touched, so the fields you sent are stored whatever that list holds; each
    sentence in it is a proxy host, DNS record or tunnel rule that could not be brought in
    line with what was just saved. That leaves the two out of step -- Vauxtra describing the
    service one way and the provider still publishing it another -- so report the list
    rather than the saved row alone.
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
    """Set a service's tags, environments or icon, and call no provider to do it.

    These three are Vauxtra's own metadata: no proxy host, DNS record or tunnel rule carries
    them. `update_service` sends the whole service back through `PUT`, which publishes it
    again on its proxy or tunnel, so a tag change rewrote the tunnel rule as well, and the
    tunnel provider used to rebuild that rule with its origin settings (`noTLSVerify` among
    them) cleared. This tool goes through `PATCH /api/services/{id}`, which writes the three
    columns and nothing else.

    An argument left out, or None, keeps what the service has. A list replaces the whole set
    rather than adding to it: `tag_ids=[3]` on a service carrying 1 and 2 leaves it carrying
    3 alone, `tag_ids=[]` strips every tag, and `icon_url=""` removes the icon. To add a tag,
    read the service with `get_service` and send its ids plus the new one.

    Refused with nothing written: 400 when no argument is given, 400 naming every id that
    matches no tag or environment (`list_tags` and `list_environments` are where they come
    from), 404 for a service that does not exist. The answer is the service as `get_service`
    returns it, with no `errors` key, because no provider was asked anything.
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

    The answer carries an `errors` list, and on this tool it is the part that matters most.
    Disabling a service does not only flip a flag. A tunnel-mode service loses its ingress
    rule. A proxy-mode one has its proxy host suspended, or deleted where the provider has
    no suspension, and its DNS record withdrawn; on the extra proxies and DNS servers it is
    also published on, the host and the record are deleted. Enabling puts them back. The
    database row is written either way, so a withdrawal that failed leaves the service off
    in Vauxtra while its public hostname is still live and still serving traffic.

    A withdrawal the provider refused lands in `errors`, and so does an error raised by the
    tunnel or the DNS server. An error raised by the service's proxy provider does not: it
    is written to the journal (`get_logs`) as a warning and appears nowhere in this answer.
    A non-empty `errors` after disabling means the service is off in Vauxtra and still
    reachable from the internet; say so rather than reporting the toggle done.
    """
    current = client.get(f"/services/{service_id}")
    client.check(current)
    payload: dict[str, Any] = {**_service_to_payload(current.json()), "enabled": enabled}
    r = client.put(f"/services/{service_id}", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def sync_services_from_providers() -> dict[str, Any]:
    """
    Discover existing services from all enabled providers.

    Returns proxy_hosts (from NPM, Zoraxy, Traefik, Cloudflare Tunnel) and dns_rewrites
    (from Pi-hole, AdGuard, Cloudflare DNS) that can be imported into Vauxtra, plus:
      providers        one line per integration asked, in the order they were added:
                       id, name, type, ok, count, and when ok is false the error (its
                       routes are missing from this scan, the rest is not a full picture);
      declared_domains the domains declared in Settings > DNS domains.

    Every row carries _zone (the zone its name is filed under) and _declared (whether that
    zone is a declared domain). A DNS token can read zones the operator never declared:
    import the rows whose _declared is true unless told otherwise. A name answered by two
    integrations appears once per integration; the import keeps the first and refuses the
    others by name.
    """
    r = client.post("/services/sync")
    client.check(r)
    return r.json()


@mcp.tool()
def import_services_from_sync(proxy_hosts: list[dict[str, Any]] | None = None, dns_rewrites: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Import services discovered by sync_services_from_providers.

    Pass the proxy_hosts and/or dns_rewrites arrays from the sync result.

    Returns four outcomes, and every submitted row lands in one of them:
      imported (int)      new services created;
      linked   (int)      existing services that gained the DNS half they were missing;
      skipped  (list[str]) rows passed over on purpose, nothing is wrong with them: a name
                          Vauxtra already tracks (a link never replaces the DNS half a
                          service already has, and a tunnel service takes none), or the
                          2nd..Nth name of a proxy host that answers for several, since a
                          service carries one name;
      errors   (list[str]) rows that are wrong and that the operator has somewhere to fix.

    A skipped list is not a failure: re-importing a scan Vauxtra already knows fills it and
    leaves errors empty. The one row that is not counted separately is a DNS record whose
    name matches a proxy host in the same payload: it is folded into that host and the pair
    counts once under imported.
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
    """
    Import Docker containers as services.

    domain: target domain (e.g., "example.com")
    containers: list of containers from discover_docker_containers
    proxy_provider_id: optional reverse proxy provider
    dns_provider_id: optional DNS provider
    dns_ip: public IP for DNS records (optional)

    The answer says what became of each container, and the count alone will mislead you:
    `imported` is how many became services, `skipped` names the ones already tracked under
    that name -- the nominal result of selecting a whole page -- and `errors` names the ones
    this route refused, for want of a reachable address or a port. Those two are separate
    lists of sentences precisely because they call for opposite responses: a `skipped` line
    is nothing to do, an `errors` line is a container that will never be imported until
    somebody fixes it. An import of six that answers `imported: 1` has five sentences to
    report, not a number.
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
    """
    Run bulk service actions: enable, disable, or delete.

    `action` was declared as a plain `str` while `POST /api/services/bulk` accepts three
    words, so every other value reached the API and came back a 400 -- after the request
    had been sent, and with no list of what would have worked. The `Literal` refuses it
    here, before the call, and publishes the three words in the tool's schema.

    The answer carries `affected` and `errors`, and they count different things. `affected`
    is how many service rows Vauxtra changed; it is written before the provider work starts,
    so it says nothing about whether the providers followed. `errors` is where that is
    recorded, one sentence per service and provider that refused. A bulk disable reporting
    `affected: 12` with two sentences in `errors` has twelve services marked disabled in
    Vauxtra and two hostnames still publicly served -- which is the outcome that matters, so
    read `errors` before reporting the action done.
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
