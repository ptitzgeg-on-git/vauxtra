from fastapi import APIRouter, Body, HTTPException, Request

from app.auth import require_auth, require_auth_or_setup
from app.importing import refuse_import, set_aside
from app.models import add_log, get_db
from app.providers.base import supports_suspension
from app.providers.factory import PROVIDER_TYPES, create_provider, host_id_is_hostname
from app.public_target import describe_public_target_failure, resolve_public_target
from app.security import redact_query_secrets
from app.text import plural
from app.validators import NO_PORT

router = APIRouter()


def _has_column(row, name: str) -> bool:
    """Whether *row* carries a column called *name*.

    `name in row` is the obvious spelling and it asks a `sqlite3.Row` the wrong question:
    a row is a sequence, so `in` searches its VALUES, not its column names. Every service
    row read here answered False to `"expose_mode" in svc` -- no column of a service ever
    holds the string "expose_mode" -- so the six sites that asked fell through to their
    default and nothing ever contradicted them.

    Tunnel services paid for it. Read as `proxy_dns`, which has no tunnel target, they were
    collected against no provider at all: the drift check compared them to nothing and
    answered "in sync" about a route that had never been published, and the push plan beside
    it said there was nothing to do. `public_target_mode` was read the same way, so `auto`
    never re-resolved the public address it exists to follow.

    A dict answers on its keys, and so does this.
    """
    keys = getattr(row, "keys", None)
    return name in (keys() if callable(keys) else row)


def _service_public_host(service_row) -> str:
    mode = (service_row["expose_mode"] or "proxy_dns").strip().lower() if _has_column(service_row, "expose_mode") else "proxy_dns"
    if mode == "tunnel":
        tunnel_hostname = str(service_row["tunnel_hostname"] or "").strip().lower() if _has_column(service_row, "tunnel_hostname") else ""
        if tunnel_hostname:
            return tunnel_hostname
    return f"{service_row['subdomain']}.{service_row['domain']}".strip(".").lower()


def _refused(kind: str, provider_name: str, attempted: str) -> str:
    """Turn a provider's bare `False` into something an operator can act on.

    `update_host`, `create_host`, `add_rewrite` and `delete_rewrite` all answer with a plain
    boolean or `None`: the reason -- expired credentials, a revoked token, a host that no
    longer exists on the other side -- never leaves the provider. Saying that much is still
    worth more than a bare "failed", because it says where to look.
    """
    return (
        f"{kind} ({provider_name}): the provider refused the {attempted}. "
        "It gave no reason -- check its credentials, that it is reachable, "
        "and the permissions of the token Vauxtra uses."
    )


def _collect_push_targets(conn, svc, sid: int) -> tuple[str, str, list, list]:
    public_host = _service_public_host(svc)
    expose_mode = (svc["expose_mode"] or "proxy_dns").strip().lower() if _has_column(svc, "expose_mode") else "proxy_dns"

    extra_targets = conn.execute(
        """
        SELECT spt.role, p.*
        FROM service_push_targets spt
        JOIN providers p ON p.id = spt.provider_id
        WHERE spt.service_id=? AND p.enabled=1
        """,
        (sid,),
    ).fetchall()

    proxy_targets = []
    dns_targets = []

    seen_proxy_ids = set()
    seen_dns_ids = set()

    primary_proxy_provider_id = svc["proxy_provider_id"]
    if expose_mode == "tunnel":
        primary_proxy_provider_id = svc["tunnel_provider_id"]

    if primary_proxy_provider_id:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (primary_proxy_provider_id,)).fetchone()
        if row and row["enabled"]:
            proxy_targets.append(row)
            seen_proxy_ids.add(row["id"])

    if svc["dns_provider_id"]:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["dns_provider_id"],)).fetchone()
        if row and row["enabled"]:
            dns_targets.append(row)
            seen_dns_ids.add(row["id"])

    for t in extra_targets:
        if t["role"] == "proxy" and t["id"] not in seen_proxy_ids:
            proxy_targets.append(t)
            seen_proxy_ids.add(t["id"])
        if t["role"] == "dns" and t["id"] not in seen_dns_ids:
            dns_targets.append(t)
            seen_dns_ids.add(t["id"])

    return expose_mode, public_host, proxy_targets, dns_targets


def _host_serves(host: dict, wanted: str) -> bool:
    """Whether a provider's host record answers for `wanted`, already lowercased.

    Shared rather than written out at each site, because it was written out at each site and
    they did not agree: the push compared case-insensitively and the drift check did not, so
    a rule Zoraxy kept under the spelling it was typed with came back as a missing route with
    a Reconcile button beside it -- and the push that button fires found the host, updated it,
    and left the same error on screen afterwards.
    """
    domains = host.get("domains") or host.get("domain_names") or []
    return any(str(d).strip().lower() == wanted for d in domains)


def _rewrite_for(rewrites, public_host: str) -> dict | None:
    """The record a DNS provider holds for `public_host`, compared the same way.

    AdGuard and Technitium echo the name as it was written, and a rewrite is the same rewrite
    whatever case it is spelled in. Reading past one is not harmless here: the withdrawal then
    falls back to the address Vauxtra last stored, and the push reads "no record" and adds a
    second one beside the first.
    """
    wanted = (public_host or "").strip().lower()
    return next(
        (r for r in rewrites or [] if str(r.get("domain") or "").strip().lower() == wanted),
        None,
    )


def _is_dns_type(ptype: str) -> bool:
    """Whether a provider type answers names, by the rule the scan uses."""
    meta = PROVIDER_TYPES.get(ptype, {})
    return bool((meta.get("capabilities") or {}).get("dns")) or meta.get("category") == "dns"


def _records_named(provider, name: str) -> list[dict]:
    """What `provider` holds for `name`, through `records_for` when it has one.

    `register_provider_type` checks no base class, so a plugin written before `records_for`
    existed still answers here, through the listing every DNS provider has.
    """
    ask = getattr(provider, "records_for", None)
    if callable(ask):
        return ask(name) or []
    wanted = (name or "").strip().strip(".").lower()
    return [
        r
        for r in provider.list_rewrites() or []
        if str(r.get("domain") or "").strip().strip(".").lower() == wanted
    ]


def _dns_record_kept(dns, host: str, answer: str) -> bool:
    """Whether `dns` still holds `host` -> `answer` after refusing to delete it.

    A refused `delete_rewrite` is not always a record left behind. The Cloudflare one
    answers False when nothing matched, and a disabled service is withdrawn again on every
    edit, long after the first withdrawal took its record. Only the listing tells the two
    apart. A listing that fails tells nothing, and that counts as kept: "withdrawn" is
    the one claim nothing here has established.
    """
    wanted = (answer or "").strip().rstrip(".").lower()
    try:
        held = _records_named(dns, host)
    except Exception:
        return True
    return any(str(r.get("answer") or "").strip().rstrip(".").lower() == wanted for r in held)


def _answers_elsewhere(conn, public_host: str, published: str, attached: set) -> list[dict]:
    """Warnings for the DNS integrations that answer `public_host` without being pushed to.

    The drift check read the integrations a service is attached to and nothing else, so a
    record anywhere else could not show. Measured in production on 2026-09-22:
    a media server was imported with AdGuard attached, whose rewrite gave it a LAN address;
    a grey Cloudflare A record gave the same name a public one, and the drift answer was
    `ok: true, issues: []`.

    Two answers for one name is what split-horizon means, and it is also what a leftover
    looks like. Nothing here can tell them apart, so it says what it found and stops there:
    a warning, never an error. `ok` does not move: auto-reconcile acts on it and pushes only
    to the integrations the service is attached to, so an error here would set off a push
    every round that could never clear it. Reconcile has nothing to offer about one either.
    An integration that cannot be read is a warning of its own, because its silence would
    read as nobody else answering.
    """
    issues: list[dict] = []
    for row in conn.execute("SELECT * FROM providers WHERE enabled=1 ORDER BY id").fetchall():
        if row["id"] in attached or not _is_dns_type(row["type"]):
            continue
        try:
            records = _records_named(create_provider(row), public_host)
        except Exception as e:
            issues.append(
                {
                    "severity": "warn",
                    "type": "dns_elsewhere_check_failed",
                    "provider": row["name"],
                    "detail": redact_query_secrets(str(e)),
                }
            )
            continue
        answers = {str(r.get("answer") or r.get("ip") or "").strip().lower() for r in records}
        found = ", ".join(sorted(answers - {"", published}))
        if found:
            issues.append(
                {
                    "severity": "warn",
                    "type": "dns_answered_elsewhere",
                    "provider": row["name"],
                    "detail": (
                        f"{public_host} also resolves to {found} here, "
                        f"while the service publishes {published}"
                    ),
                    "detail_key": "answered_elsewhere",
                    "detail_params": {"host": public_host, "found": found, "expected": published},
                }
            )
    return issues


def _imported_name(value) -> str:
    """A hostname read off a provider, reduced to the spelling a service is stored under.

    `ServiceIn` lowercases the subdomain and `normalize_domain` lowercases the domain, so
    every service the editor writes is stored in lower case. The import route wrote what the
    provider spelled. Nothing downstream noticed, because `_service_fqdn` lowercases the
    public hostname it derives -- so a service stored as `NAS.maison.lan` pushed, and drifted,
    under `nas.maison.lan`, exactly like the service already tracking that name.

    The three consequences were all silent. The row was offered as new on every scan, since
    `_already_imported` compared against the stored spelling. Ticking it inserted a second
    service, since the "Vauxtra already tracks this name" lookup compared the same way and so
    does the unique index on `(subdomain, domain)`. And a proxy host and a DNS record for one
    name, spelled differently by their two providers, stopped pairing: two services, each
    holding half of what one should have held.
    """
    return str(value or "").strip().lower()


def _longest_zone(name: str, zones) -> str:
    """The longest of *zones* that *name* is, or sits under, at a label boundary; "" if none."""
    best = ""
    for zone in zones:
        if zone and (name == zone or name.endswith("." + zone)) and len(zone) > len(best):
            best = zone
    return best


def _zone_of(name: str, declared, hint="") -> tuple[str, bool]:
    """The zone a scanned name belongs to, and whether the operator declared it.

    Three answers, in this order:

    - The longest declared domain the name sits under. It wins over a longer zone the
      provider reports, because it is where the operator said their services live: a
      record read from a delegated `maison.example.org` still files under a declared
      `example.org`, as `nas.maison`, which is how the editor would have stored it.
    - The zone the provider read the record from (`zone` on a DNS row), when the name
      really is under it.
    - Everything after the first dot, which is how every name used to be split, and what
      is left when neither of the above knows the name. It is right only for a name one
      label below its zone: `a.b.example.net` lands under `b.example.net`, which an import
      would declare as a domain of its own, and a zone apex `example.net` under `net`.

    A single-label name has no zone at all ("").
    """
    zone = _longest_zone(name, declared)
    if zone:
        return zone, True
    hint = _imported_name(hint).strip(".")
    if hint and (name == hint or name.endswith("." + hint)):
        return hint, False
    return (name.split(".", 1)[1] if "." in name else ""), False


def _declared_domains(conn) -> list[str]:
    """The domains the operator declared (Settings > DNS domains), as the scan compares them."""
    names = (_imported_name(r["name"]).strip(".") for r in conn.execute("SELECT name FROM domains").fetchall())
    return sorted({n for n in names if n})


def _tracked_id(conn, fqdn: str, *, tunnels: bool = False):
    """The id of the service already published under *fqdn*, or None.

    Compared on the whole name. The import used to ask for its own split of the name,
    `subdomain=? AND domain=?`, and a split is not a name: a service the editor stored as
    `nas.maison` under `example.org` was not found under `nas` + `maison.example.org`. The
    scan compared whole names and marked that row tracked, but "Quick import" sent it back all
    the same, and the import made a second one. *tunnels* also matches a tunnel's own
    hostname, which is the name that service publishes.
    """
    query = "SELECT id FROM services WHERE lower(subdomain || '.' || domain) = ?"
    params: tuple = (fqdn,)
    if tunnels:
        query += " OR lower(COALESCE(tunnel_hostname, '')) = ?"
        params = (fqdn, fqdn)
    row = conn.execute(query, params).fetchone()
    return row["id"] if row else None


def _tracked_row(conn, fqdn: str):
    """The service already published under *fqdn*, with the two columns a link has to read.

    A tunnel's own hostname counts, as it does for `_tracked_id`: the CNAME a tunnel writes is
    a record a DNS scan lists under that very name. Without it, a tunnel service published
    under a hostname other than its subdomain and domain is not found, and its own record
    comes in as a second service. The screens mark that row tracked (`trackServices`) and
    never send it, but a caller that sends the scan as it is, the MCP bridge for one, has
    only this test to stop it.
    """
    return conn.execute(
        "SELECT id, expose_mode, dns_provider_id FROM services "
        "WHERE lower(subdomain || '.' || domain) = ? OR lower(COALESCE(tunnel_hostname, '')) = ?",
        (fqdn, fqdn),
    ).fetchone()


def _split_for_import(fqdn: str, declared, hint="") -> tuple[str, str, str]:
    """`(subdomain, domain, "")` for a name that can become a service, `("", "", why)` if not.

    Split at the zone `_zone_of` finds, not at the first dot. The first dot filed
    `a.b.example.net` under a domain `b.example.net`, declared it in passing, and stored a
    service the editor would have written as `a.b` under `example.net`.
    """
    if "." not in fqdn:
        return "", "", "a domain needs at least one dot, so this name has no subdomain to split off"
    zone, _declared = _zone_of(fqdn, declared, hint)
    if fqdn == zone:
        return "", "", (
            f"it is the zone {zone} itself: a service is a name published under a domain, "
            "and this one has nothing in front of the domain to publish"
        )
    return fqdn[: -(len(zone) + 1)], zone, ""


def _find_host(proxy, public_host: str) -> dict | None:
    """The provider's host record for `public_host`, or None.

    Hostnames compare case-insensitively: Zoraxy keeps a rule under the spelling it was
    typed with, and a rule created as "App.Example.com" is the same host as the service
    Vauxtra spells "app.example.com".

    The whole record rather than its id, because `enabled` lives nowhere else. A host that
    is held and not served is the state a disabled service leaves behind, and the id alone
    cannot tell it from a host that answers.
    """
    wanted = (public_host or "").strip().lower()
    try:
        for h in proxy.list_hosts() or []:
            if _host_serves(h, wanted):
                return h
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _host_is_served(host: dict | None) -> bool:
    """Whether a host that exists is also answering.

    NPM and Zoraxy suspend rather than delete, so the rule stays in the listing and this
    flag is the only thing that says it stopped serving. Providers with no suspension do not
    report it at all, and `True` is the right default for them: read the other way, every
    route they hold would be called suspended.
    """
    return bool(host is None or host.get("enabled", True))


def _find_host_id(proxy, public_host: str):
    """The provider's id for the host serving `public_host`, or None."""
    host = _find_host(proxy, public_host)
    return host.get("id") if host else None


def _stored_host_id(hostname_keyed: bool, svc, public_host: str):
    """The stored primary-proxy id, unless the provider keys hosts on a name that moved.

    For a provider whose id is the hostname, an id that no longer spells the service's
    public host names a rule that is gone -- a service renamed before the rename was
    written back, or renamed by hand. Returning None sends the caller to the live
    lookup instead of to a rule that does not exist.
    """
    host_id = svc["npm_host_id"]
    if not host_id:
        return None
    if hostname_keyed and str(host_id).strip().lower() != public_host:
        return None
    return host_id


def _all_route_holders(conn, svc, sid: int) -> tuple[str, str, list, list]:
    """Every provider that may still hold a route for this service, enabled or not.

    `_collect_push_targets` skips disabled providers on purpose: republishing to a target
    the operator turned off would be wrong. Withdrawing is the mirror case -- disabling a
    provider inside Vauxtra does not take the route it published off the internet -- so a
    removal has to try all of them.
    """
    public_host = _service_public_host(svc)
    expose_mode = (svc["expose_mode"] or "proxy_dns").strip().lower() if _has_column(svc, "expose_mode") else "proxy_dns"

    proxy_rows: list = []
    dns_rows: list = []
    seen_proxy: set = set()
    seen_dns: set = set()

    def _add_proxy(provider_id) -> None:
        if not provider_id or provider_id in seen_proxy:
            return
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if row:
            proxy_rows.append(row)
            seen_proxy.add(row["id"])

    # Both columns, whichever mode the service is in now. A service switched from proxy to
    # tunnel keeps its `proxy_provider_id`, and the host it published there is still live.
    _add_proxy(svc["tunnel_provider_id"] if expose_mode == "tunnel" else svc["proxy_provider_id"])
    _add_proxy(svc["proxy_provider_id"] if expose_mode == "tunnel" else svc["tunnel_provider_id"])

    if svc["dns_provider_id"]:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["dns_provider_id"],)).fetchone()
        if row:
            dns_rows.append(row)
            seen_dns.add(row["id"])

    extras = conn.execute(
        """
        SELECT spt.role, p.*
        FROM service_push_targets spt
        JOIN providers p ON p.id = spt.provider_id
        WHERE spt.service_id=?
        """,
        (sid,),
    ).fetchall()
    for t in extras:
        if t["role"] == "proxy" and t["id"] not in seen_proxy:
            proxy_rows.append(t)
            seen_proxy.add(t["id"])
        elif t["role"] == "dns" and t["id"] not in seen_dns:
            dns_rows.append(t)
            seen_dns.add(t["id"])

    return expose_mode, public_host, proxy_rows, dns_rows


def withdraw_service_routes(
    conn, svc, sid: int, *, only_provider_ids: set[int] | None = None,
    log_prefix: str = "[Delete]",
) -> list[str]:
    """Take a service's public route off every provider that may still serve it.

    Deleting a service walked `proxy_provider_id` and `dns_provider_id` and stopped there.
    The extra targets in `service_push_targets` -- the second proxy, the second DNS server,
    the ones a push writes to on every cycle -- were never told: their routes outlived the
    deletion, the hostname stayed resolvable, the proxy kept forwarding, and nothing was
    left in Vauxtra to show for it.

    `only_provider_ids` narrows the withdrawal to part of the holders, for the three cases
    where the service itself survives: a multi-sync target dropped from an edit, a provider
    being deleted while other targets go on serving the same hostname, and a service being
    disabled.

    `log_prefix` is the why, and the journal is where an operator reconstructs it. Three of
    the four callers really are deleting something; a disabled service still exists, and a
    line reading `[Delete] Proxy route removed` beside it would be read as the deletion that
    never happened -- the primary path says `DNS record withheld (service disabled)` for the
    same reason.

    Returns one message per failure; an empty list means everything is withdrawn.
    """
    expose_mode, public_host, proxy_rows, dns_rows = _all_route_holders(conn, svc, sid)
    if only_provider_ids is not None:
        proxy_rows = [r for r in proxy_rows if r["id"] in only_provider_ids]
        dns_rows = [r for r in dns_rows if r["id"] in only_provider_ids]
    errors: list[str] = []

    for row in proxy_rows:
        if PROVIDER_TYPES.get(row["type"], {}).get("read_only"):
            continue  # nothing was ever pushed there
        try:
            proxy = create_provider(row)
            host_id = None
            if host_id_is_hostname(proxy, row["type"]):
                # Cloudflare Tunnel and Zoraxy address their rules by hostname, not by a
                # numeric id, so the service's own hostname is the route to withdraw --
                # whatever an older rename may have left in `npm_host_id`.
                host_id = public_host
            elif row["id"] == svc["proxy_provider_id"]:
                host_id = svc["npm_host_id"]
            if not host_id:
                # The id is only ever stored for the primary proxy; anywhere else the route
                # is found by the hostname it serves, exactly as the push finds it.
                host_id = _find_host_id(proxy, public_host)
            if not host_id:
                continue  # nothing there under this hostname

            if proxy.delete_host(host_id):
                add_log("info", f"{log_prefix} Proxy route removed on {row['name']}: {public_host}", conn)
            else:
                errors.append(f"Failed to delete proxy host on {row['name']}")
        except Exception as e:
            errors.append(f"Proxy ({row['name']}): {redact_query_secrets(str(e))}")

    for row in dns_rows:
        try:
            dns = create_provider(row)
            actual_ip = ""
            try:
                entry = _rewrite_for(dns.list_rewrites(), public_host)
                actual_ip = (entry or {}).get("ip") or (entry or {}).get("answer") or ""
            except Exception:
                actual_ip = ""
            # The record on the server is what has to go, and it may have drifted away from
            # the address Vauxtra last stored; `dns_ip` is the fallback, not the reference.
            ip = actual_ip or (svc["dns_ip"] or "")
            if not ip:
                continue

            # A refusal is read against the listing before it is reported. A disabled service
            # is withdrawn from its extra DNS servers again on every edit, and the Cloudflare
            # integration refuses a deletion that matched nothing: every save of such a
            # service reported a failure about a record that had been gone since the first.
            if dns.delete_rewrite(public_host, ip):
                add_log("info", f"{log_prefix} DNS record removed on {row['name']}: {public_host}", conn)
            elif _dns_record_kept(dns, public_host, ip):
                errors.append(f"Failed to delete DNS rewrite on {row['name']}")
        except Exception as e:
            errors.append(f"DNS ({row['name']}): {redact_query_secrets(str(e))}")

    if expose_mode == "tunnel" and not proxy_rows and only_provider_ids is None:
        # Only when the whole service is being withdrawn. A narrowed withdrawal legitimately
        # leaves the tunnel out of the selection, and reporting that as a missing provider
        # would turn "this DNS server no longer serves it" into a failure.
        errors.append("No tunnel provider left to remove the route from")

    return errors


def withhold_service_routes(
    conn, svc, sid: int, *, only_provider_ids: set[int] | None = None
) -> list[str]:
    """Take a disabled service out of service, the way disabling it does.

    A push converges the providers on what the record says, and for a disabled service the
    record says "not reachable". `withdraw_service_routes` is the wrong tool for the primary
    proxy: it deletes the host, and the disable path in `update_service` suspends it. The
    difference is not cosmetic. NPM keeps the custom locations, the advanced configuration
    and the certificate binding that Vauxtra does not model, and `npm_host_id` stays valid so
    the re-enable can toggle the same host back on. Deleting it here would leave that column
    pointing at a host that no longer exists, and the re-enable would then fail on a dead id.
    That failure is reported now -- the enable paths tell a refused toggle from an unsupported
    one -- but it would be reported about a service whose configuration is already gone, which
    is why the deletion is reserved for providers that genuinely cannot suspend.

    Everything else is removed rather than suspended, for the reasons the two neighbours
    already carry: DNS has no suspension, and Vauxtra never stores the host ids of the extra
    proxies, so a suspension there could never be lifted -- `withdraw_extra_targets` spells
    that out.

    Returns one message per failure, in the shape both push routes already put in `errors`.
    """
    expose_mode, public_host, proxy_rows, dns_rows = _all_route_holders(conn, svc, sid)
    if only_provider_ids is not None:
        proxy_rows = [r for r in proxy_rows if r["id"] in only_provider_ids]
        dns_rows = [r for r in dns_rows if r["id"] in only_provider_ids]
    errors: list[str] = []

    # Tunnel mode has no primary proxy to suspend: Cloudflare Tunnel addresses its rules by
    # hostname and does not implement `toggle_host`, so the withdrawal below is the whole
    # answer there, which is also what the tunnel branch of `update_service` does.
    primary_id = svc["proxy_provider_id"] if expose_mode != "tunnel" else None
    primary_row = next((r for r in proxy_rows if r["id"] == primary_id), None) if primary_id else None
    stored_host_id = svc["npm_host_id"]
    suspended: set = set()

    if primary_row is not None and stored_host_id \
            and not PROVIDER_TYPES.get(primary_row["type"], {}).get("read_only"):
        suspended.add(primary_row["id"])
        try:
            proxy = create_provider(primary_row)
            if proxy.toggle_host(stored_host_id, False):
                add_log("info", f"[Disable] Proxy suspended on {primary_row['name']}: {public_host}", conn)
            elif not supports_suspension(proxy):
                # No suspension on this provider, so the route has to go -- and the column has
                # to go with it, or the re-enable toggles an id that is not there any more.
                if proxy.delete_host(stored_host_id):
                    conn.execute("UPDATE services SET npm_host_id=NULL WHERE id=?", (sid,))
                    add_log("info", f"[Disable] Proxy route removed on {primary_row['name']} (suspend unsupported): {public_host}", conn)
                else:
                    errors.append(f"Failed to remove the proxy host on {primary_row['name']}")
            else:
                # The fallback is for a provider that has no suspension, never for one whose
                # suspension refused the call. NPM and Zoraxy answer the same `False` either
                # way, and reading it as "unsupported" turned a provider that hiccupped into
                # a deletion of the host -- with the custom locations, the advanced
                # configuration and the certificate binding this suspension exists to keep,
                # and `npm_host_id` blanked so nothing could be put back.
                errors.append(f"Failed to suspend the proxy host on {primary_row['name']}")
        except Exception as e:
            # Deliberately not falling through to the deletion: the provider just failed to
            # answer, and a delete would fail the same way or, worse, half-succeed.
            errors.append(f"Proxy ({primary_row['name']}): {redact_query_secrets(str(e))}")

    rest = ({r["id"] for r in proxy_rows} | {r["id"] for r in dns_rows}) - suspended
    if rest:
        errors.extend(
            withdraw_service_routes(conn, svc, sid, only_provider_ids=rest, log_prefix="[Disable]")
        )

    return errors


def _build_withhold_plan(conn, svc, sid: int) -> dict:
    """What the push would do to a service the record says is off.

    The dry-run is the documented way to find out what a push will write -- `docs/TROUBLESHOOTING.md`
    says to run it first -- so it has to describe the withdrawal, not the publication that
    used to happen. Read as if every service were published, the plan promised to create the
    route on every provider and the push then removed it from every provider, which is the
    one answer a dry-run must never give.

    It reaches the providers only to ask what they are, never what they hold: `create_provider`
    builds the client, and whether the primary can be suspended is a property of its class.
    The publication plan calls out to look a host up because "create" and "update" are
    different writes; here they are not, so the plan costs no round trip.

    As in the publication plan, an action is what the push will attempt rather than a diff
    against the provider, and `would_change` follows it -- the same reading that makes an
    `update` on an unchanged host count as a change.
    """
    expose_mode, public_host, proxy_rows, dns_rows = _all_route_holders(conn, svc, sid)

    proxy_actions: list[dict] = []
    dns_actions: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    primary_id = svc["proxy_provider_id"] if expose_mode != "tunnel" else None
    stored_host_id = svc["npm_host_id"]

    for row in proxy_rows:
        if PROVIDER_TYPES.get(row["type"], {}).get("read_only"):
            proxy_actions.append(
                {
                    "provider_id": row["id"],
                    "provider_name": row["name"],
                    "provider_type": row["type"],
                    "action": "skip_read_only",
                    "target_host": public_host,
                }
            )
            warnings.append(f"Proxy {row['name']} is read-only")
            continue

        action = "delete"
        if row["id"] == primary_id and stored_host_id:
            try:
                proxy = create_provider(row)
                # The fallback in `withhold_service_routes` is not a guess: a provider that
                # does not override `toggle_host` inherits the base's `return False`, so the
                # suspension it would try can only fail, and the route has to be deleted.
                if supports_suspension(proxy):
                    action = "suspend"
            except Exception as e:
                errors.append(f"Proxy ({row['name']}): {redact_query_secrets(str(e))}")
                continue

        proxy_actions.append(
            {
                "provider_id": row["id"],
                "provider_name": row["name"],
                "provider_type": row["type"],
                "action": action,
                "target_host": public_host,
            }
        )

    for row in dns_rows:
        # No suspension exists in DNS, and the tunnel branch of the withdrawal removes the
        # rewrites the same way the proxy branch does.
        dns_actions.append(
            {
                "provider_id": row["id"],
                "provider_name": row["name"],
                "provider_type": row["type"],
                "action": "delete",
                "domain": public_host,
                "target": "",
            }
        )

    return {
        "service_id": sid,
        "mode": expose_mode,
        "public_host": public_host,
        "proxy_actions": proxy_actions,
        "dns_actions": dns_actions,
        "service_updates": [],
        "warnings": warnings,
        "errors": errors,
        "dns_target": "",
        "dns_target_source": "",
        "would_change": bool(
            [a for a in proxy_actions if a.get("action") != "skip_read_only"] or dns_actions
        ),
        "ok": len(errors) == 0,
        "withheld": True,
    }


def _build_push_plan(conn, svc, sid: int) -> dict:
    if not svc["enabled"]:
        return _build_withhold_plan(conn, svc, sid)

    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)

    proxy_actions: list[dict] = []
    dns_actions: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    dns_target = ""
    dns_target_source = ""
    if expose_mode != "tunnel":
        dns_target_mode = (svc["public_target_mode"] or "manual") if _has_column(svc, "public_target_mode") else "manual"
        manual_value = svc["dns_ip"] if dns_target_mode != "auto" else ""
        dns_target, dns_target_source = resolve_public_target(
            conn,
            mode=dns_target_mode,
            manual_value=manual_value,
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=svc["dns_ip"] or "",
        )

    for row in proxy_targets:
        provider_meta = PROVIDER_TYPES.get(row["type"], {})
        if provider_meta.get("read_only"):
            proxy_actions.append(
                {
                    "provider_id": row["id"],
                    "provider_name": row["name"],
                    "provider_type": row["type"],
                    "action": "skip_read_only",
                    "target_host": public_host,
                }
            )
            warnings.append(f"Proxy {row['name']} is read-only")
            continue

        try:
            proxy = create_provider(row)
            host_id = None
            if expose_mode != "tunnel" and row["id"] == svc["proxy_provider_id"]:
                host_id = _stored_host_id(host_id_is_hostname(proxy, row["type"]), svc, public_host)
            # The listing is read even when the stored id already answers, because `enabled`
            # lives nowhere else and the push now acts on it. It costs the primary proxy one
            # call the plan did not make before, and it is the call that stops the plan from
            # answering "update" about a provider it cannot reach -- which is how it already
            # behaves for every other proxy in this loop.
            live = _find_host(proxy, public_host)
            if not host_id:
                host_id = live.get("id") if live else None

            if not host_id:
                action = "create"
            elif not _host_is_served(live) and supports_suspension(proxy):
                # The push updates this host and then lifts its suspension, and those are
                # different answers to "what will happen". Naming the second one is the
                # difference between a plan that explains the drift report beside it and one
                # that leaves the operator to assume an update also turns a route back on.
                action = "resume"
            else:
                action = "update"

            proxy_actions.append(
                {
                    "provider_id": row["id"],
                    "provider_name": row["name"],
                    "provider_type": row["type"],
                    "action": action,
                    "target_host": public_host,
                    "target_origin": f"{svc['forward_scheme']}://{svc['target_ip']}:{svc['target_port']}",
                }
            )
        except Exception as e:
            errors.append(f"Proxy ({row['name']}): {redact_query_secrets(str(e))}")

    if expose_mode != "tunnel":
        if not dns_target and dns_targets:
            errors.append(describe_public_target_failure(dns_target_source)[1])

        if dns_target:
            for row in dns_targets:
                dns_actions.append(
                    {
                        "provider_id": row["id"],
                        "provider_name": row["name"],
                        "provider_type": row["type"],
                        "action": "upsert",
                        "domain": public_host,
                        "target": dns_target,
                    }
                )

    would_change = bool(
        [a for a in proxy_actions if a.get("action") not in {"skip_read_only"}] or dns_actions
    )

    service_updates = []
    if expose_mode != "tunnel" and dns_target and dns_target != (svc["dns_ip"] or ""):
        service_updates.append(
            {
                "field": "dns_ip",
                "old": svc["dns_ip"] or "",
                "new": dns_target,
                "source": dns_target_source,
            }
        )

    return {
        "service_id": sid,
        "mode": expose_mode,
        "public_host": public_host,
        "proxy_actions": proxy_actions,
        "dns_actions": dns_actions,
        "service_updates": service_updates,
        "warnings": warnings,
        "errors": errors,
        "dns_target": dns_target,
        "dns_target_source": dns_target_source,
        "would_change": would_change,
        "ok": len(errors) == 0,
        # Always present, so the panel reads one key rather than inferring the state from the
        # action words: false here, true on the plan a disabled service gets.
        "withheld": False,
    }


#: Drift details used to be English sentences built here, which is the one place they can
#: never be translated. Each templated shape now also carries the key its sentence was
#: written from and the values it was built out of, so the UI can say the same thing in the
#: reader's language. `detail` stays as the plain-text fallback (logs, older clients).
def _compute_service_drift(conn, svc, sid: int, *, look_elsewhere: bool = True) -> dict:
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
    issues: list[dict] = []

    expected_origin = f"{svc['forward_scheme']}://{svc['target_ip']}:{svc['target_port']}"
    # Drift is the distance between the record and the providers, and a disabled service has
    # the opposite expectation rather than no expectation. Read as if every service were
    # published, a correctly disabled one came back as "out of sync, 2 errors" -- with a
    # Reconcile button beside it whose only honest meaning would have been "publish it again".
    service_enabled = bool(svc["enabled"])

    for row in proxy_targets:
        try:
            provider = create_provider(row)
            hosts = provider.list_hosts() or []
            hit = next((h for h in hosts if _host_serves(h, public_host)), None)

            if not service_enabled:
                # NPM and Zoraxy suspend a host rather than delete it, so the route stays in
                # the listing and `enabled` is the only thing that says whether it still
                # answers. Providers with no suspension do not report the flag at all, and
                # `True` is the right default for them: a rule that exists is a rule serving.
                if hit and _host_is_served(hit):
                    issues.append(
                        {
                            "severity": "error",
                            "type": "proxy_route_still_served",
                            "provider": row["name"],
                            "detail": f"Route {public_host} still served while the service is disabled",
                            "detail_key": "route_still_served",
                            "detail_params": {"host": public_host},
                        }
                    )
                continue

            if not hit:
                issues.append(
                    {
                        "severity": "error",
                        "type": "missing_proxy_route",
                        "provider": row["name"],
                        "detail": f"Route {public_host} missing on provider",
                        "detail_key": "route_missing",
                        "detail_params": {"host": public_host},
                    }
                )
                continue

            # Held and not served, which is how Vauxtra itself switches a service off. Only
            # the hostname and the origin were ever compared, so the flag Vauxtra had written
            # read back as perfectly in sync while the hostname answered nothing -- and
            # `update_host` is a PUT that does not carry the flag, so the Reconcile button
            # beside this line walked over the host without lifting anything. Both halves are
            # fixed together: the push resumes, and this is what asks it to.
            if not _host_is_served(hit):
                issues.append(
                    {
                        "severity": "error",
                        "type": "proxy_route_suspended",
                        "provider": row["name"],
                        "detail": f"Route {public_host} is suspended on the provider",
                        "detail_key": "route_suspended",
                        "detail_params": {"host": public_host},
                    }
                )

            current_origin = f"{hit.get('scheme', 'http')}://{hit.get('host', '')}:{hit.get('port', '')}"
            if current_origin != expected_origin:
                issues.append(
                    {
                        "severity": "warn",
                        "type": "proxy_origin_mismatch",
                        "provider": row["name"],
                        "detail": f"Expected {expected_origin}, found {current_origin}",
                        "detail_key": "origin_mismatch",
                        "detail_params": {"expected": expected_origin, "found": current_origin},
                    }
                )
        except Exception as e:
            issues.append(
                {
                    "severity": "error",
                    "type": "proxy_check_failed",
                    "provider": row["name"],
                    # Technitium and Pi-hole v5 carry their credential in the query string,
                    # and a `requests` error quotes the URL it failed on.
                    "detail": redact_query_secrets(str(e)),
                }
            )

    # `dns_ip` is the address the records are compared against, so an empty one used to mean
    # there was nothing to compare. A disabled service is not comparing anything: it is asking
    # whether a record is still there, which is a question worth answering even once Vauxtra
    # has forgotten which address it pointed at.
    if expose_mode != "tunnel" and (svc["dns_ip"] or not service_enabled):
        for row in dns_targets:
            try:
                provider = create_provider(row)
                rewrites = provider.list_rewrites() or []
                match = _rewrite_for(rewrites, public_host)
                if not service_enabled:
                    # No suspension exists in DNS: a rewrite that is there is a name resolving.
                    if match:
                        issues.append(
                            {
                                "severity": "error",
                                "type": "dns_rewrite_still_served",
                                "provider": row["name"],
                                "detail": f"Rewrite {public_host} still resolving while the service is disabled",
                                "detail_key": "rewrite_still_served",
                                "detail_params": {"host": public_host},
                            }
                        )
                    continue
                if not match:
                    issues.append(
                        {
                            "severity": "error",
                            "type": "missing_dns_rewrite",
                            "provider": row["name"],
                            "detail": f"Rewrite {public_host} missing on provider",
                            "detail_key": "rewrite_missing",
                            "detail_params": {"host": public_host},
                        }
                    )
                else:
                    answer = str(match.get("answer") or match.get("ip") or "").strip().lower()
                    expected = str(svc["dns_ip"] or "").strip().lower()
                    if expected and answer != expected:
                        issues.append(
                            {
                                "severity": "warn",
                                "type": "dns_target_mismatch",
                                "provider": row["name"],
                                "detail": f"Expected {expected}, found {answer}",
                                "detail_key": "answer_mismatch",
                                "detail_params": {"expected": expected, "found": answer},
                            }
                        )
            except Exception as e:
                issues.append(
                    {
                        "severity": "error",
                        "type": "dns_check_failed",
                        "provider": row["name"],
                        "detail": redact_query_secrets(str(e)),
                    }
                )

    # A record on an integration the service is not pushed to is invisible to the loop
    # above, and it answers the same name all the same. Only an enabled service that
    # publishes an address somewhere has an answer to hold it against.
    published = str(svc["dns_ip"] or "").strip().lower()
    if look_elsewhere and expose_mode != "tunnel" and service_enabled and dns_targets and published:
        attached = {row["id"] for row in dns_targets}
        issues.extend(_answers_elsewhere(conn, public_host, published, attached))

    return {
        "service_id": sid,
        "public_host": public_host,
        "mode": expose_mode,
        "ok": len([i for i in issues if i.get("severity") == "error"]) == 0,
        "issues": issues,
    }


@router.post("/api/services/{sid}/push")
def push_service(sid: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    svc  = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(404, "Service not found")
    try:
        return _push_service_row(conn, svc, sid)
    finally:
        conn.close()


def _execute_push(svc, sid: int) -> dict:
    """Push a service from outside the HTTP layer -- the scheduler's auto-reconcile job.

    `app/scheduler.py` has imported this name since auto-reconcile was written, and it was
    never defined: the job raised `ImportError` on its first tick. Nobody noticed because
    `auto_reconcile_enabled` was not writable either, so the job could never be scheduled.
    Both halves are fixed together; a feature that cannot be switched on is not a feature.
    """
    conn = get_db()
    try:
        return _push_service_row(conn, svc, sid)
    finally:
        conn.close()


def _push_service_row(conn, svc, sid: int, *, only_provider_ids: set[int] | None = None) -> dict:
    """The push itself. Commits; the caller owns the connection and closes it.

    `only_provider_ids` narrows the push to part of the targets. `push_extra_targets` uses
    it to reach the multi-sync providers on their own, once the write routes have dealt
    with the primary proxy and the primary DNS server themselves.

    A disabled service is withheld instead of published. The record is what a push converges
    the providers on, and the record says this one is off -- publishing it here is how a
    service the table showed as disabled came back. Two one-click paths reach this: the drift
    drawer offers Reconcile beside the errors it just reported, and `ExposeModal` fires a push
    of its own right after saving any service that has a multi-sync target, so pressing Save
    on a disabled service republished it on every provider at once.
    """
    if not svc["enabled"]:
        errors = withhold_service_routes(conn, svc, sid, only_provider_ids=only_provider_ids)
        conn.commit()
        return {"ok": not errors, "errors": errors}

    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
    if only_provider_ids is not None:
        proxy_targets = [r for r in proxy_targets if r["id"] in only_provider_ids]
        dns_targets = [r for r in dns_targets if r["id"] in only_provider_ids]
    errors = []

    for row in proxy_targets:
        if PROVIDER_TYPES.get(row["type"], {}).get("read_only"):
            add_log("info", f"[Push] Skipped read-only proxy provider ({row['type']}) for {public_host}", conn)
            continue

        try:
            proxy   = create_provider(row)
            cert_id = None if expose_mode == "tunnel" else proxy.find_best_certificate(svc["domain"])

            host_id = None
            if expose_mode != "tunnel" and row["id"] == svc["proxy_provider_id"]:
                host_id = _stored_host_id(host_id_is_hostname(proxy, row["type"]), svc, public_host)
            if not host_id:
                host_id = _find_host_id(proxy, public_host)

            if host_id:
                # Providers report a refusal by returning False, not by raising: NPM answers
                # False on an expired token, Cloudflare Tunnel on an ingress rule it could not
                # write. Only the `except` below used to fill `errors`, so those cases came
                # back as `ok: true` with a "Proxy synced" line in the journal.
                pushed = proxy.update_host(
                    host_id,
                    public_host,
                    svc["target_ip"],
                    svc["target_port"],
                    svc["forward_scheme"],
                    bool(svc["websocket"]),
                    cert_id,
                )
                attempted = f"update of host {host_id}"
                if pushed and expose_mode != "tunnel" and row["id"] == svc["proxy_provider_id"] \
                        and host_id != svc["npm_host_id"]:
                    # The live lookup found the rule under a name the row did not hold:
                    # write it back so the next cycle does not have to look again.
                    conn.execute("UPDATE services SET npm_host_id=? WHERE id=?", (host_id, sid))
                # A host can be held without serving -- that is how Vauxtra switches a service
                # off -- and `update_host` is a PUT that does not carry the flag. So the push
                # wrote the right origin onto a suspended rule and left it suspended, and
                # Reconcile, which is this push, could not converge the one state Vauxtra
                # itself produces. A provider with no suspension is never asked, and the two
                # that have one answer on the state the host ends up in rather than on the
                # code of the call, so resuming a host already running is not a refusal.
                if pushed and supports_suspension(proxy) and not proxy.toggle_host(host_id, True):
                    pushed = False
                    attempted = f"resume of host {host_id}"
            else:
                result = proxy.create_host(
                    public_host,
                    svc["target_ip"],
                    svc["target_port"],
                    svc["forward_scheme"],
                    bool(svc["websocket"]),
                    cert_id,
                )
                pushed = bool(result)
                attempted = f"creation of a proxy host for {public_host}"
                if result and expose_mode != "tunnel" and row["id"] == svc["proxy_provider_id"]:
                    conn.execute("UPDATE services SET npm_host_id=? WHERE id=?", (result.get("id"), sid))

            if not pushed:
                errors.append(_refused("Proxy", row["name"], attempted))
                add_log("error", f"[Push] Proxy refused the {attempted} on {row['name']}", conn)
                continue

            add_log("info", f"[Push] Proxy synced on {row['name']}: {public_host}", conn)
        except Exception as e:
            errors.append(f"Proxy ({row['name']}): {redact_query_secrets(str(e))}")

    if expose_mode != "tunnel":
        dns_target_mode = (svc["public_target_mode"] or "manual") if _has_column(svc, "public_target_mode") else "manual"
        manual_value = svc["dns_ip"] if dns_target_mode != "auto" else ""
        dns_target, dns_target_source = resolve_public_target(
            conn,
            mode=dns_target_mode,
            manual_value=manual_value,
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=svc["dns_ip"] or "",
        )

        if not dns_target and dns_targets:
            # The dry-run has always refused this. The push skipped the loop below without a
            # word and returned `{"ok": true, "errors": []}`, so the preview of the action and
            # the action itself contradicted each other about the same service, and the one
            # that reported success is the one that writes records.
            sentence = describe_public_target_failure(dns_target_source)[1]
            errors.append(sentence)
            add_log("error", f"[Push] Nothing pushed to DNS for {public_host}: {sentence}", conn)

        if dns_target and dns_target != (svc["dns_ip"] or ""):
            conn.execute("UPDATE services SET dns_ip=? WHERE id=?", (dns_target, sid))
            add_log("info", f"[Push] DNS target refreshed for {public_host}: {svc['dns_ip']} → {dns_target} ({dns_target_source})", conn)

        if dns_target:
            for row in dns_targets:
                try:
                    dns = create_provider(row)
                    # Read the actual current value from the provider (not just DB) to fix drift
                    actual_rewrites = dns.list_rewrites()
                    actual_entry = _rewrite_for(actual_rewrites, public_host)
                    actual_ip = (actual_entry or {}).get("ip") or (actual_entry or {}).get("answer", "")
                    failure = ""
                    if actual_ip and actual_ip != dns_target:
                        # The stale record has to go first, and the `elif` matters: AdGuard and
                        # Pi-hole happily hold two rewrites for one name, so adding after a
                        # failed removal leaves the host resolving to whichever the resolver
                        # picks. Not pushing at all is the lesser evil, and it is reported.
                        if not dns.delete_rewrite(public_host, actual_ip):
                            failure = f"removal of the stale {public_host} → {actual_ip} record"
                        elif not dns.add_rewrite(public_host, dns_target):
                            failure = f"creation of {public_host} → {dns_target}"
                    elif not actual_ip and not dns.add_rewrite(public_host, dns_target):
                        failure = f"creation of {public_host} → {dns_target}"

                    if failure:
                        errors.append(_refused("DNS", row["name"], failure))
                        add_log("error", f"[Push] DNS refused the {failure} on {row['name']}", conn)
                        continue

                    add_log("info", f"[Push] DNS synced on {row['name']}: {public_host} → {dns_target}", conn)
                except Exception as e:
                    errors.append(f"DNS ({row['name']}): {redact_query_secrets(str(e))}")

    conn.commit()
    return {"ok": not errors, "errors": errors}


def push_extra_targets(conn, sid: int) -> list[str]:
    """Publish a service on the multi-sync targets its write route never touches.

    `add_service` and `update_service` address one proxy and one DNS server -- the two
    columns on the service row -- and record everything else in `service_push_targets`
    afterwards. Nothing then pushed to those rows. A service created with a second DNS
    server answered `201 {"errors": []}` while that server received nothing, and the very
    next call to `/drift` reported `missing_dns_rewrite` on a service the operator had just
    been told was published. The second target only ever filled in on the next scheduler
    cycle, or on an explicit push nobody knew to make.

    Returns one message per failure, in the shape both routes already put in `errors`.
    """
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc or not svc["enabled"]:
        # A disabled service withholds its primary push too: the extras follow the same rule
        # rather than publishing a hostname the interface shows as off.
        return []

    extra_ids = {
        r["provider_id"]
        for r in conn.execute(
            "SELECT provider_id FROM service_push_targets WHERE service_id=?", (sid,)
        )
    }
    if not extra_ids:
        return []

    return _push_service_row(conn, svc, sid, only_provider_ids=extra_ids)["errors"]


def withdraw_extra_targets(conn, sid: int) -> list[str]:
    """Take a disabled service off the multi-sync targets its write route never touches.

    The mirror of `push_extra_targets`, and it was missing. `update_service` and the bulk
    enable/disable walked `proxy_provider_id`, `dns_provider_id` and `tunnel_provider_id`
    when the flag changed, so disabling a service suspended the primary proxy and deleted
    the primary DNS record while the second proxy went on forwarding the same hostname and
    the second DNS server went on resolving it. The interface showed the service as off,
    which is the one state an operator reads as "this is not reachable any more", and the
    only thing that had changed was who Vauxtra was still talking to.

    The extras are deleted where the primary proxy is merely suspended, and that asymmetry
    is load-bearing rather than an oversight. NPM's enable and disable live on their own
    endpoints: `update_host` is a PUT that never touches the flag, so a suspended host stays
    suspended through any number of pushes. The re-enable path here is `push_extra_targets`,
    which finds the existing host by hostname and calls `update_host` -- it has no stored id
    to toggle and no toggle to make. Suspending an extra would therefore leave it dark
    forever after a re-enable, and silently: the PUT answers 200, so `errors` stays empty
    and the journal reads "Proxy synced". Deleting it means the next push finds nothing and
    calls `create_host`, which is what actually brings the route back.

    Returns one message per failure, in the shape both routes already put in `errors`.
    """
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc or svc["enabled"]:
        # An enabled service publishes on its extras; `push_extra_targets` is that half.
        return []

    extra_ids = {
        r["provider_id"]
        for r in conn.execute(
            "SELECT provider_id FROM service_push_targets WHERE service_id=?", (sid,)
        )
    }
    if not extra_ids:
        return []

    # `[Disable]`, not `[Delete]`: the service still exists, and the journal is where an
    # operator reconstructs which of the two happened.
    return withdraw_service_routes(
        conn, svc, sid, only_provider_ids=extra_ids, log_prefix="[Disable]"
    )


@router.post("/api/services/{sid}/push/dry-run")
def dry_run_push_service(sid: int, request: Request):
    # Explicitly `read`: no scope at all also lets through a key granted nothing.
    require_auth(request, scope="read")
    conn = get_db()
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(404, "Service not found")

    try:
        plan = _build_push_plan(conn, svc, sid)
        return plan
    finally:
        conn.close()


@router.get("/api/services/{sid}/drift")
def service_drift(sid: int, request: Request):
    require_auth(request)
    conn = get_db()
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(404, "Service not found")

    try:
        return _compute_service_drift(conn, svc, sid)
    finally:
        conn.close()


@router.post("/api/services/{sid}/reconcile")
def reconcile_service(sid: int, request: Request):
    require_auth(request, scope="write")

    conn = get_db()
    svc_before = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc_before:
        conn.close()
        raise HTTPException(404, "Service not found")
    before = _compute_service_drift(conn, svc_before, sid)
    conn.close()

    push_result = push_service(sid, request)

    conn = get_db()
    svc_after = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc_after:
        conn.close()
        raise HTTPException(404, "Service not found")
    after = _compute_service_drift(conn, svc_after, sid)
    conn.close()

    return {
        "ok": bool(after.get("ok")) and not bool(push_result.get("errors")),
        "before": before,
        "push": push_result,
        "after": after,
    }


@router.post("/api/services/sync")
def sync_services(request: Request):
    """Every route and record the enabled integrations hold, each marked with its zone.

    Measured in production on 2026-09-22, two scans minutes apart answered 91 routes and then
    32, with no setting changed in between according to the report. The 59 others came from
    twelve zones nobody had declared, listed through one Cloudflare token by the first scan
    and not by the second; and nothing in the answer said which integrations had been asked,
    or that one had failed. So every row now carries `_zone` and `_declared` (see
    `_zone_of`), and the answer says what was asked of whom: `providers` holds one line per
    integration scanned, with its count and, when it failed, why; `declared_domains` the
    domains the panel filters on.

    Integrations are asked in the order they were added, so two scans that get the same rows
    back list them in the same order, and the import keeps the same record of two.
    """
    # Explicitly `read`: this only lists what the providers already hold.
    require_auth_or_setup(request, scope="read")
    conn = get_db()
    providers = conn.execute("SELECT * FROM providers WHERE enabled=1 ORDER BY id").fetchall()
    declared = _declared_domains(conn)
    existing_fqdns = {
        _imported_name(f"{r['subdomain']}.{r['domain']}")
        for r in conn.execute("SELECT subdomain, domain FROM services").fetchall()
    }
    existing_tunnel_hosts = {
        (r["tunnel_hostname"] or "").strip().lower()
        for r in conn.execute("SELECT tunnel_hostname FROM services WHERE tunnel_hostname IS NOT NULL AND tunnel_hostname <> ''").fetchall()
    }
    existing_npm_ids = {
        r["npm_host_id"]
        for r in conn.execute("SELECT npm_host_id FROM services WHERE npm_host_id IS NOT NULL").fetchall()
    }
    conn.close()

    result = {"proxy_hosts": [], "dns_rewrites": [], "providers": [], "declared_domains": declared}
    for p in providers:
        meta = PROVIDER_TYPES.get(p["type"], {})
        caps = meta.get("capabilities", {})
        is_proxy = bool(caps.get("proxy")) or meta.get("category") == "proxy"
        is_dns = bool(caps.get("dns")) or meta.get("category") == "dns"
        if not (is_proxy or is_dns):
            continue
        report = {"id": p["id"], "name": p["name"], "type": p["type"], "ok": True, "count": 0, "error": ""}
        result["providers"].append(report)
        try:
            provider = create_provider(p)

            if is_proxy:
                hosts = provider.list_hosts()
                for h in hosts:
                    # Normalize field names so the frontend always finds them
                    if "domains" in h and "domain_names" not in h:
                        h["domain_names"] = h["domains"]
                    if "domain_names" in h and "domains" not in h:
                        h["domains"] = h["domain_names"]
                    if "host" in h and "forward_host" not in h:
                        h["forward_host"] = h["host"]
                    if "port" in h and "forward_port" not in h:
                        h["forward_port"] = h["port"]
                    if "scheme" in h and "forward_scheme" not in h:
                        h["forward_scheme"] = h["scheme"]

                    domains = [d.strip().lower() for d in (h.get("domains") or h.get("domain_names") or []) if d]
                    already_by_domain = any(d in existing_fqdns or d in existing_tunnel_hosts for d in domains)
                    already_by_id = h.get("id") in existing_npm_ids
                    h["_provider_id"]      = p["id"]
                    h["_provider_name"]    = p["name"]
                    h["_provider_type"]    = p["type"]
                    h["_provider_readonly"] = PROVIDER_TYPES.get(p["type"], {}).get("read_only", False)
                    h["_already_imported"] = already_by_id or already_by_domain
                result["proxy_hosts"].extend(hosts)
                report["count"] = len(hosts)
            elif is_dns:
                rewrites = provider.list_rewrites()
                for r in rewrites:
                    r["_provider_id"]      = p["id"]
                    r["_provider_name"]    = p["name"]
                    r["_already_imported"] = _imported_name(r.get("domain")) in existing_fqdns
                result["dns_rewrites"].extend(rewrites)
                report["count"] = len(rewrites)
        except Exception as e:
            # Masked before it is shown or written: see `redact_query_secrets`.
            error = redact_query_secrets(str(e)) or type(e).__name__
            report["ok"] = False
            report["error"] = error
            add_log("error", f"Sync {p['name']}: {error}")

    # Zones last, once every integration has answered: a proxy knows no zone, and the zone a
    # DNS integration reported is the best guess for any name under it, a route included.
    provider_zones = {
        zone for zone in (_imported_name(r.get("zone")).strip(".") for r in result["dns_rewrites"]) if zone
    }
    for r in result["dns_rewrites"]:
        name = _imported_name(r.get("domain"))
        hint = r.get("zone") or _longest_zone(name, provider_zones)
        r["_zone"], r["_declared"] = _zone_of(name, declared, hint)
    for h in result["proxy_hosts"]:
        # The name the import keeps is the first one (see `import_services`).
        names = [_imported_name(d) for d in (h.get("domains") or []) if str(d).strip()]
        name = names[0] if names else ""
        h["_zone"], h["_declared"] = _zone_of(name, declared, _longest_zone(name, provider_zones))

    return result


@router.post("/api/services/import")
def import_services(request: Request, data: dict = Body(...)):
    """Turn scanned provider rows into services, and say what happened to every one of them.

    Four outcomes, because two were not enough to tell the operator anything. A row is
    `imported` (a new service), `linked` (an existing service gained the DNS half it was
    missing -- a real write that used to be reported as nothing at all), set aside on purpose
    (`skipped`), or refused because something is wrong with it (`errors`).

    Two rows can make one service, and that is the point of the pairing: a DNS record whose
    name matches a proxy host in the same payload is folded into that host, and the pair is
    counted once under `imported`. Apart from that fold every submitted row lands in exactly
    one of the four -- and `skipped` may carry extra lines for names *inside* a row that could
    not become a service of their own, so it is the one bucket that can outrun the row count.

    `imported` and `errors` keep the meaning and the shape they always had, so every existing
    caller keeps working; `linked` and `skipped` are additions.
    """
    require_auth_or_setup(request, scope="write")
    imported = 0
    linked   = 0
    skipped  = []
    errors   = []
    conn     = get_db()
    declared = _declared_domains(conn)

    # Built one row at a time rather than by a comprehension: the comprehension dropped a
    # nameless record, and a second record for a name already in the map, without either the
    # loop below or the caller ever hearing of them. Both shapes are real -- AdGuard and
    # Pi-hole hold two rewrites for one name without complaining.
    #
    # Two deliberate changes live here, neither of them a side effect of rewriting the loop.
    #
    # Names are compared through `_imported_name`, stripped and lowercased. Unstripped,
    # " nas.maison.lan " was a different key from "nas.maison.lan" and imported a second
    # service whose subdomain was " nas" and whose domain was "maison.lan " -- a row that
    # matches no scan and pushes nowhere. Uncased, "NAS.maison.lan" did the same thing, and
    # kept doing it: the spelling a provider happens to use is not the spelling a service is
    # stored under. The proxy loop normalizes the same way, so a padded or capitalized name
    # on one side still pairs with a clean one on the other.
    #
    # And on a name two providers answer for, the comprehension kept the LAST record; this
    # keeps the first. Neither is a better guess, only a steadier one: `sync_services` asks
    # the integrations in the order they were added (`ORDER BY id`), so of two answers the
    # one kept is the older integration's, scan after scan. What changed is that the choice
    # is no longer silent -- the message below names both providers, so the operator settles
    # it at the provider instead of discovering months later which address `push_service`
    # has been writing.
    dns_by_fqdn: dict[str, dict] = {}
    for r in data.get("dns_rewrites", []):
        fqdn = _imported_name(r.get("domain"))
        if not fqdn:
            refuse_import(
                errors, conn,
                f"a DNS record from {r.get('_provider_name') or 'the provider'}",
                "it carries no name to import under",
            )
            continue
        if fqdn in dns_by_fqdn:
            kept = dns_by_fqdn[fqdn].get("_provider_name") or "the first provider"
            other = r.get("_provider_name") or "another provider"
            if kept == other:
                # One provider answering twice for one name is not two integrations
                # disagreeing, it is a duplicate row inside one of them, and naming the
                # provider twice in the same sentence reads like a bug in the message.
                reason = (
                    f"{kept} holds two records for this name. The first is the one imported; "
                    f"remove the duplicate at the provider"
                )
            else:
                # Two integrations answering one name is also what a split-horizon looks
                # like: the LAN resolver hands out the local address, the public zone the
                # tunnel. The old wording ("remove the other") told the operator to delete
                # the half that makes it work, so the sentence now says what the import did
                # and leaves the verdict to them. It stays in `errors` all the same: the
                # record from {other} is not tracked, and that is still worth reading.
                reason = (
                    f"{kept} and {other} both answer for this name. The answer from {kept} is "
                    f"the one imported; the record at {other} is left as it is and not tracked. "
                    f"If the two are meant to differ (split-horizon), nothing needs doing; "
                    f"if not, one of them is stale"
                )
            refuse_import(errors, conn, fqdn, reason)
            continue
        dns_by_fqdn[fqdn] = r

    for h in data.get("proxy_hosts", []):
        where = f"proxy host {h.get('id', '?')} on {h.get('_provider_name') or 'the provider'}"
        try:
            raw = h.get("domains") or h.get("domain_names") or []
            domains = [_imported_name(d) for d in raw if str(d).strip()]
            if not domains:
                refuse_import(errors, conn, where, "the provider listed no domain name for it")
                continue
            fqdn  = domains[0]
            # A proxy host may serve several names; a service carries one. The rest are named
            # rather than dropped, because the operator ticked one row and gets one service.
            for spare in domains[1:]:
                set_aside(
                    skipped, spare,
                    f"{where} also answers for {fqdn}, and a service carries a single name",
                )
            dns_match = dns_by_fqdn.get(fqdn)
            # The zone a DNS integration reported for this very name knows better than a
            # proxy, which knows none.
            hint = (dns_match or {}).get("_zone") or (dns_match or {}).get("zone") or h.get("_zone")
            subdomain, domain, why = _split_for_import(fqdn, declared, hint)
            if why:
                refuse_import(errors, conn, fqdn, why)
                continue
            if _tracked_id(conn, fqdn, tunnels=True) is not None:
                set_aside(skipped, fqdn, "Vauxtra already tracks this name")
                continue

            provider_type = (h.get("_provider_type") or "").strip().lower()

            dns_provider_id = dns_match.get("_provider_id") if dns_match else None
            dns_ip          = dns_match.get("answer", "") if dns_match else ""

            if provider_type == "cloudflare_tunnel":
                conn.execute(
                    """INSERT INTO services
                       (subdomain, domain, target_ip, target_port, forward_scheme,
                        websocket, expose_mode, tunnel_provider_id, tunnel_hostname,
                        dns_provider_id, dns_ip)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        subdomain,
                        domain,
                        h.get("host", h.get("forward_host", "")),
                        int(h.get("port", h.get("forward_port", 80))),
                        h.get("scheme", h.get("forward_scheme", "http")),
                        int(bool(h.get("websocket", h.get("allow_websocket_upgrade", False)))),
                        "tunnel",
                        h.get("_provider_id"),
                        fqdn,
                        None,
                        "",
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO services
                       (subdomain, domain, target_ip, target_port, forward_scheme,
                        websocket, proxy_provider_id, npm_host_id, dns_provider_id, dns_ip)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (subdomain, domain,
                     h.get("host", h.get("forward_host", "")),
                     int(h.get("port", h.get("forward_port", 80))),
                     h.get("scheme", h.get("forward_scheme", "http")),
                     int(bool(h.get("websocket", h.get("allow_websocket_upgrade", False)))),
                     h.get("_provider_id"), h.get("id"), dns_provider_id, dns_ip),
                )
            conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
            imported += 1
            dns_by_fqdn.pop(fqdn, None)
            add_log("info", f"Imported: {fqdn}" + (" (proxy + DNS)" if dns_match else ""), conn)
        except Exception as e:
            # Through the helper like every other refusal: an exception here is still a row
            # the operator ticked, and `str(e)` on its own named neither the row nor a place
            # to look, and wrote nothing to the journal.
            refuse_import(errors, conn, where, f"importing it raised {type(e).__name__}: {e}")

    for fqdn, r in dns_by_fqdn.items():
        try:
            ip = r.get("answer", "")
            # Split in two, because the two causes are not cured the same way: one is a
            # record to fix at the provider, the other a name that can never be a service.
            if not ip:
                refuse_import(errors, conn, fqdn, "its record answers with no address")
                continue
            subdomain, domain, why = _split_for_import(fqdn, declared, r.get("_zone") or r.get("zone"))
            if why:
                refuse_import(errors, conn, fqdn, why)
                continue
            existing = _tracked_row(conn, fqdn)
            if existing is not None:
                # A link fills the DNS half a service is missing, and only that. It used to
                # overwrite whatever half was there: "Quick import" sent the whole scan back,
                # tracked names included, so a name two integrations answer for re-pointed a
                # tracked service at whichever had been read first, and a tunnel service -- which
                # stores no DNS provider, its tunnel writes the record -- gained one.
                if existing["expose_mode"] == "tunnel":
                    set_aside(
                        skipped, fqdn,
                        "Vauxtra already publishes this name through a tunnel, which writes its own DNS record",
                    )
                    continue
                if existing["dns_provider_id"] is not None:
                    set_aside(skipped, fqdn, "Vauxtra already tracks this name and its DNS record")
                    continue
                conn.execute(
                    "UPDATE services SET dns_provider_id=?, dns_ip=? WHERE id=?",
                    (r.get("_provider_id"), ip, existing["id"]),
                )
                # Counted, because this writes to the row `push_service` reads next. It used
                # to update the service, log it, and still answer `{"imported": 0,
                # "errors": []}` -- the answer for "there was nothing to do".
                linked += 1
                add_log("info", f"DNS linked: {fqdn} -> {ip}", conn)
            else:
                # No port: a DNS record names an address, not a service listening on it. The
                # 80 written here before was a guess the monitor then took at its word, and
                # a VPN endpoint or a hypervisor came in reported down on port 80 forever.
                conn.execute(
                    """INSERT INTO services
                       (subdomain, domain, target_ip, target_port, dns_provider_id, dns_ip)
                       VALUES (?,?,?,?,?,?)""",
                    (subdomain, domain, ip, NO_PORT, r.get("_provider_id"), ip),
                )
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
                imported += 1
                add_log("info", f"Imported from DNS: {fqdn} -> {ip}", conn)
        except Exception as e:
            refuse_import(errors, conn, fqdn, f"importing it raised {type(e).__name__}: {e}")

    if skipped:
        # One line for the run. See `set_aside`.
        add_log("info", f"Import passed over {plural(len(skipped), 'row')} already accounted for", conn)

    conn.commit()
    conn.close()
    return {"imported": imported, "linked": linked, "skipped": skipped, "errors": errors}
