import re

from fastapi import APIRouter, Body, HTTPException, Request

from app.auth import require_auth, require_auth_or_setup
from app.importing import refuse_import, set_aside
from app.models import add_log, get_db
from app.providers.base import supports_suspension
from app.providers.factory import PROVIDER_TYPES, create_provider, host_id_is_hostname
from app.public_target import describe_public_target_failure, resolve_public_target
from app.security import redact_query_secrets
from app.text import plural
from app.validators import NO_PORT, is_valid_hostname, is_valid_port

router = APIRouter()


def _has_column(row, name: str) -> bool:
    """Whether `row` has a column `name` (works for sqlite3.Row and dict).

    `name in row` searches a Row's values, not its keys.
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
    """Message for a provider call that returned False without saying why."""
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
    """Whether a provider host record serves `wanted` (already lowercased), ignoring case."""
    domains = host.get("domains") or host.get("domain_names") or []
    return any(str(d).strip().lower() == wanted for d in domains)


def _rewrite_for(rewrites, public_host: str) -> dict | None:
    """Return the record a DNS provider holds for `public_host`, compared case-insensitively."""
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
    """Records `provider` holds for `name`, via `records_for` or, for older plugins, the full listing."""
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

    Some providers refuse a deletion that matched nothing, so the listing decides. A
    listing that fails counts as kept.
    """
    wanted = (answer or "").strip().rstrip(".").lower()
    try:
        held = _records_named(dns, host)
    except Exception:
        return True
    return any(str(r.get("answer") or "").strip().rstrip(".").lower() == wanted for r in held)


def _answers_elsewhere(conn, public_host: str, published: str, attached: set) -> list[dict]:
    """Warn about DNS integrations that resolve `public_host` without being attached.

    Warnings only: could be split-horizon or a leftover. Never sets ok=false, since
    auto-reconcile cannot fix it. An unreadable integration is its own warning.
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
    """Normalize a hostname read from a provider the way services are stored (stripped, lowercase)."""
    return str(value or "").strip().lower()


def _longest_zone(name: str, zones) -> str:
    """The longest of *zones* that *name* is, or sits under, at a label boundary; "" if none."""
    best = ""
    for zone in zones:
        if zone and (name == zone or name.endswith("." + zone)) and len(zone) > len(best):
            best = zone
    return best


def _zone_of(name: str, declared, hint="") -> tuple[str, bool]:
    """Return (zone, declared) for a scanned name.

    Prefers the longest declared domain, then the zone the provider reported, then
    everything after the first dot. A single-label name has zone "".
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
    """Id of the service published under `fqdn` (whole-name match), or None.

    `tunnels` also matches a tunnel service's own hostname.
    """
    query = "SELECT id FROM services WHERE lower(subdomain || '.' || domain) = ?"
    params: tuple = (fqdn,)
    if tunnels:
        query += " OR lower(COALESCE(tunnel_hostname, '')) = ?"
        params = (fqdn, fqdn)
    row = conn.execute(query, params).fetchone()
    return row["id"] if row else None


def _tracked_row(conn, fqdn: str):
    """The service published under `fqdn`, tunnel hostnames included, with the columns a link reads."""
    return conn.execute(
        "SELECT id, expose_mode, dns_provider_id FROM services "
        "WHERE lower(subdomain || '.' || domain) = ? OR lower(COALESCE(tunnel_hostname, '')) = ?",
        (fqdn, fqdn),
    ).fetchone()


def _split_for_import(fqdn: str, declared, hint="") -> tuple[str, str, str]:
    """Return (subdomain, domain, "") for an importable name, or ("", "", reason).

    Splits at the zone `_zone_of` finds, not at the first dot.
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
    """Return the provider's host record for `public_host` (case-insensitive), or None.

    The whole record, because `enabled` tells a suspended host from a serving one.
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
    """Whether a host is serving. Missing `enabled` (no suspension support) means True."""
    return bool(host is None or host.get("enabled", True))


def _find_host_id(proxy, public_host: str):
    """The provider's id for the host serving `public_host`, or None."""
    host = _find_host(proxy, public_host)
    return host.get("id") if host else None


def _stored_host_id(hostname_keyed: bool, svc, public_host: str):
    """The stored primary-proxy id, or None when a hostname-keyed id no longer matches `public_host`."""
    host_id = svc["npm_host_id"]
    if not host_id:
        return None
    if hostname_keyed and str(host_id).strip().lower() != public_host:
        return None
    return host_id


def _all_route_holders(conn, svc, sid: int) -> tuple[str, str, list, list]:
    """Every provider that may still hold a route for this service, disabled ones included.

    Unlike a push, a withdrawal must reach disabled providers too.
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
    """Remove a service's public route from every provider that may serve it.

    only_provider_ids: restrict to these providers (edit, provider deletion, disable).
    log_prefix: journal prefix, so a disable is not logged as a deletion.
    Returns one message per failure; empty means fully withdrawn.
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
                # Hostname-keyed providers: withdraw by name, whatever npm_host_id holds.
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

            # A refusal is checked against the listing: some providers refuse a deletion
            # that matched nothing.
            if dns.delete_rewrite(public_host, ip):
                add_log("info", f"{log_prefix} DNS record removed on {row['name']}: {public_host}", conn)
            elif _dns_record_kept(dns, public_host, ip):
                errors.append(f"Failed to delete DNS rewrite on {row['name']}")
        except Exception as e:
            errors.append(f"DNS ({row['name']}): {redact_query_secrets(str(e))}")

    if expose_mode == "tunnel" and not proxy_rows and only_provider_ids is None:
        # Only for a full withdrawal; a narrowed one may leave the tunnel out on purpose.
        errors.append("No tunnel provider left to remove the route from")

    return errors


def withhold_service_routes(
    conn, svc, sid: int, *, only_provider_ids: set[int] | None = None
) -> list[str]:
    """Take a disabled service offline the way the disable path does.

    The primary proxy is suspended when the provider supports it (keeps NPM config and
    npm_host_id valid for re-enable); DNS and extra proxies are removed.
    Returns one message per failure.
    """
    expose_mode, public_host, proxy_rows, dns_rows = _all_route_holders(conn, svc, sid)
    if only_provider_ids is not None:
        proxy_rows = [r for r in proxy_rows if r["id"] in only_provider_ids]
        dns_rows = [r for r in dns_rows if r["id"] in only_provider_ids]
    errors: list[str] = []

    # Tunnel mode has no primary proxy to suspend.
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
                # Deletion is only for providers without suspension, never after a failed suspend.
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
    """Dry-run plan for a disabled service: what the push would withdraw or suspend.

    Contacts no provider beyond building its client. Actions are what the push will
    attempt, not a diff, and `would_change` follows them.
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
                # Without a toggle_host override the suspension can only fail, so delete.
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
            # Always read the listing: `enabled` lives nowhere else.
            live = _find_host(proxy, public_host)
            if not host_id:
                host_id = live.get("id") if live else None

            if not host_id:
                action = "create"
            elif not _host_is_served(live) and supports_suspension(proxy):
                # Reported separately from "update": the push also lifts the suspension.
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


# Drift details carry `detail` (English fallback) plus `detail_key` and params for i18n.
def _compute_service_drift(conn, svc, sid: int, *, look_elsewhere: bool = True) -> dict:
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
    issues: list[dict] = []

    expected_origin = f"{svc['forward_scheme']}://{svc['target_ip']}:{svc['target_port']}"
    # A disabled service is expected to be absent from its providers.
    service_enabled = bool(svc["enabled"])

    for row in proxy_targets:
        try:
            provider = create_provider(row)
            hosts = provider.list_hosts() or []
            hit = next((h for h in hosts if _host_serves(h, public_host)), None)

            if not service_enabled:
                # A suspended host stays listed; `enabled` says whether it still serves.
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

            # Held but suspended: report it so the push resumes it.
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

    # A disabled service is checked for leftover records even without a stored dns_ip.
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

    # Records on integrations the service is not pushed to; only relevant for an
    # enabled service with an address.
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
    """Push a service outside the HTTP layer (scheduler auto-reconcile)."""
    conn = get_db()
    try:
        return _push_service_row(conn, svc, sid)
    finally:
        conn.close()


def _push_service_row(conn, svc, sid: int, *, only_provider_ids: set[int] | None = None) -> dict:
    """Push one service to its providers. Commits; the caller closes the connection.

    `only_provider_ids` restricts the push (used by push_extra_targets). A disabled service
    is withheld instead of published.
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
            cert_id = None if expose_mode == "tunnel" else proxy.find_best_certificate(public_host)

            host_id = None
            if expose_mode != "tunnel" and row["id"] == svc["proxy_provider_id"]:
                host_id = _stored_host_id(host_id_is_hostname(proxy, row["type"]), svc, public_host)
            if not host_id:
                host_id = _find_host_id(proxy, public_host)

            if host_id:
                # Providers signal some refusals with False rather than an exception.
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
                # update_host does not carry the enabled flag, so resume a suspended host
                # explicitly. Judged on the resulting state, so an already running host is fine.
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
            # Refused, as the dry-run does.
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
                        # Remove the stale record first; if that fails do not add, since
                        # some resolvers hold two rewrites for one name.
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
    """Push a service to its extra (multi-sync) targets after the write route handled the primaries.

    Returns one message per failure.
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
    """Remove a disabled service from its extra (multi-sync) targets.

    Extras are deleted, not suspended: Vauxtra stores no host id for them, so a suspended
    extra could never be re-enabled. The next push recreates them.
    Returns one message per failure.
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
    """List every route and record the enabled integrations hold, each tagged with its zone.

    Rows carry `_zone` and `_declared`; `providers` reports each integration scanned with its
    count or error; `declared_domains` lists the domains the panel filters on. Integrations
    are scanned in creation order so results are stable.
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


# What turns a target into something else once it is written into `scheme://target:port`:
# a path, credentials, a query, a fragment or a second word.
_ORIGIN_BREAKING = re.compile(r"[\s/@?#\\]")


@router.post("/api/services/import")
def import_services(request: Request, data: dict = Body(...)):
    """Import scanned rows as services.

    Returns imported, linked (existing service gained a DNS record), skipped, errors.
    A DNS row matching a proxy host in the same payload is merged and counted once.
    """
    require_auth_or_setup(request, scope="write")
    imported = 0
    linked   = 0
    skipped  = []
    errors   = []
    conn     = get_db()
    declared = _declared_domains(conn)
    # The rows come back from the panel, so nothing in them is trusted: the mode follows the
    # stored integration, and a route is held to the rules of one created by hand.
    provider_types = {
        row["id"]: row["type"] for row in conn.execute("SELECT id, type FROM providers").fetchall()
    }

    # Explicit loop so duplicate and nameless records are reported, not dropped.
    # Names are compared stripped and lowercased. When two providers answer for one name,
    # the first (oldest provider, ORDER BY id) wins and both are named in the message.
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
        try:
            dns_provider = int(r.get("_provider_id"))
        except (TypeError, ValueError):
            dns_provider = None
        dns_type = provider_types.get(dns_provider)
        if dns_type is None or not (PROVIDER_TYPES.get(dns_type, {}).get("capabilities") or {}).get("dns"):
            refuse_import(errors, conn, fqdn, "the integration it was read from is not a configured DNS integration")
            continue
        answer = str(r.get("answer") or "").strip()
        # A CNAME answer may end with the root dot; the rest is held to a service target's rule.
        if answer and not is_valid_hostname(answer.rstrip(".")):
            refuse_import(errors, conn, fqdn, f"its record answers {answer!r}, which is not an address or a host name")
            continue
        r = {**r, "_provider_id": dns_provider, "answer": answer}
        if fqdn in dns_by_fqdn:
            kept = dns_by_fqdn[fqdn].get("_provider_name") or "the first provider"
            other = r.get("_provider_name") or "another provider"
            if kept == other:
                # Duplicate within one provider: name it once.
                reason = (
                    f"{kept} holds two records for this name. The first is the one imported; "
                    f"remove the duplicate at the provider"
                )
            else:
                # May be split-horizon, so the message reports and does not tell the operator
                # to remove anything.
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

            row = f"{fqdn} ({where})"
            try:
                provider_id = int(h.get("_provider_id"))
            except (TypeError, ValueError):
                provider_id = None
            provider_type = provider_types.get(provider_id)
            if provider_type is None:
                refuse_import(errors, conn, row, "the integration it was read from is not configured here")
                continue
            if not (PROVIDER_TYPES.get(provider_type, {}).get("capabilities") or {}).get("proxy"):
                refuse_import(errors, conn, row, "the integration it was read from does not publish routes")
                continue
            scheme = str(h.get("scheme", h.get("forward_scheme", "http")) or "").strip().lower()
            if scheme not in ("http", "https"):
                refuse_import(
                    errors, conn, row,
                    f"it forwards over {scheme or 'no scheme'}, and Vauxtra publishes http and https services only",
                )
                continue
            target = str(h.get("host", h.get("forward_host", "")) or "").strip()
            if not target or _ORIGIN_BREAKING.search(target):
                refuse_import(errors, conn, row, f"its target {target!r} is not a host name or an address")
                continue
            port = h.get("port", h.get("forward_port", 80))
            if not is_valid_port(port):
                refuse_import(errors, conn, row, f"its port {port} is not between 1 and 65535")
                continue
            port = int(port)

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
                        target,
                        port,
                        scheme,
                        int(bool(h.get("websocket", h.get("allow_websocket_upgrade", False)))),
                        "tunnel",
                        provider_id,
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
                    (subdomain, domain, target, port, scheme,
                     int(bool(h.get("websocket", h.get("allow_websocket_upgrade", False)))),
                     provider_id, h.get("id"), dns_provider_id, dns_ip),
                )
            conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
            imported += 1
            dns_by_fqdn.pop(fqdn, None)
            add_log("info", f"Imported: {fqdn}" + (" (proxy + DNS)" if dns_match else ""), conn)
        except Exception as e:
            # Through the helper so the row is named and the refusal is logged.
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
                # A link only fills a missing DNS half; tunnel services store no DNS provider.
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
                # Counted: this writes to the row push_service reads.
                linked += 1
                add_log("info", f"DNS linked: {fqdn} -> {ip}", conn)
            else:
                # No port: a DNS record names an address, not a listening service.
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
