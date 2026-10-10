from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import require_auth, require_auth_or_setup
from app.models import PUBLIC_HOSTNAME_SQL, add_log, get_db
from app.providers.base import DNSProvider, record_value_key
from app.providers.factory import PROVIDER_TYPES, create_provider, host_id_is_hostname
from app.public_target import describe_public_target_failure, resolve_dns_target

router = APIRouter()

# B9: how long a refused removal of a former proxy route is retried before Vauxtra gives up
# and asks the operator to remove it by hand.
PENDING_REMOVAL_TTL_DAYS = 7


def _col(row, name: str, default=None):
    """`row[name]` when the row has that column, else `default`.

    `"name" in row` is not that test for a `sqlite3.Row`: a Row is a sequence, so `in`
    compares against its *values*. Every `"expose_mode" in svc` read False on a database
    row, and the push treated each tunnel service as `proxy_dns` and each `auto` target as
    `manual` (found with H2).
    """
    keys = row.keys() if hasattr(row, "keys") else ()
    return row[name] if name in keys else default


def _service_public_host(service_row) -> str:
    mode = (_col(service_row, "expose_mode") or "proxy_dns").strip().lower()
    if mode == "tunnel":
        tunnel_hostname = str(_col(service_row, "tunnel_hostname") or "").strip().lower()
        if tunnel_hostname:
            return tunnel_hostname
    return f"{service_row['subdomain']}.{service_row['domain']}".strip(".").lower()


def _dns_name(value) -> str:
    """A DNS name or answer as compared here: case and the root dot do not count.

    Providers hand names back as they were typed ("App.example.com", "target.example.com."),
    and an exact comparison read the record as missing or different, so the push removed a
    record that was right and added it again.
    """
    return str(value or "").strip().lower().rstrip(".")


def _records_of_name(dns, name: str) -> list[dict] | None:
    """The records a DNS provider holds for *name*, or None when they could not be read.

    Real providers answer from the zone of the name (`DNSProvider.lookup_rewrites`). Any
    other object (a test double, a plug-in written before the method existed) is read
    through its full list, filtered here.
    """
    if isinstance(dns, DNSProvider):
        return dns.lookup_rewrites(name)
    rewrites = dns.list_rewrites()
    if rewrites is None:
        return None
    return [e for e in rewrites if _dns_name(e.get("domain")) == _dns_name(name)]


def _record_values(entries) -> set[str]:
    """The normalized values among *entries* (as read by `_records_of_name`)."""
    return {
        record_value_key(e.get("ip") or e.get("answer") or "")
        for e in entries or []
        if e.get("ip") or e.get("answer")
    }


def _move_dns_record(dns, name: str, before: list[dict], old_value: str, new_value: str) -> str:
    """Move *name* from *old_value* to *new_value* on one DNS server; "" on success.

    M7: the new record is added first, then the old one removed, never the reverse --
    deleting first left the name with no record whenever the add then failed. A provider's
    own `update_rewrite` is not trusted for this: Pi-hole's is add-then-delete too, and it
    answered True with both records live.

    A refused delete is checked against the server before anything is undone. Cloudflare
    *edits* its single record in place on `add_rewrite`, so the old value is already gone
    and the delete has nothing to find: undoing the "new" record there removed the only
    one. The new record is withdrawn only when the old one is really still served and the
    new one did not exist before this push (it is not ours to delete otherwise).
    """
    if not dns.add_rewrite(name, new_value):
        return f"creation of {name} → {new_value}"
    if dns.delete_rewrite(name, old_value):
        return ""
    after = _records_of_name(dns, name)
    if after is not None and record_value_key(old_value) not in _record_values(after):
        return ""  # the old value is gone after all (edited in place, or removed meanwhile)
    failure = f"removal of the stale {name} → {old_value} record"
    if record_value_key(new_value) in _record_values(before):
        return failure  # the new value was already published: left as it was
    # AdGuard and Pi-hole answer both records of a name in turn: the new one is withdrawn
    # rather than left beside the stale one, so the name keeps exactly the record it had.
    if not dns.delete_rewrite(name, new_value):
        failure += (f"; the new {new_value} record could not be withdrawn either, so both "
                    "are live -- remove the stale one by hand")
    return failure


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
    expose_mode = (_col(svc, "expose_mode") or "proxy_dns").strip().lower()

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


def _find_host_id(proxy, public_host: str):
    """The provider's id for the host serving `public_host`, or None.

    Hostnames compare case-insensitively: Zoraxy keeps a rule under the spelling it was
    typed with, and a rule created as "App.Example.com" is the same host as the service
    Vauxtra spells "app.example.com".
    """
    wanted = (public_host or "").strip().lower()
    try:
        hosts = proxy.list_hosts()
    except AttributeError:
        return None
    if hosts is None:
        # Unreadable is not empty: answering None here made the push create a second host
        # for a name the provider already served.
        raise RuntimeError("could not read the provider's host list")
    try:
        for h in hosts:
            domains = h.get("domains") or h.get("domain_names") or []
            if any(str(d).strip().lower() == wanted for d in domains):
                return h.get("id")
    except (AttributeError, TypeError, ValueError):
        return None
    return None


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
    expose_mode = (_col(svc, "expose_mode") or "proxy_dns").strip().lower()

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


def withdraw_former_proxy_host(conn, provider_id, host_id, public_host: str) -> str:
    """Remove the route a service leaves behind on a proxy it no longer uses.

    Answers "" when the route is gone (or there was none to remove), else the sentence to
    report. A refused removal is queued in `pending_proxy_removals`, which the scheduler
    retries: keeping the old proxy as the service's own made the drift check report the
    route missing, and the auto-reconcile recreated it on the proxy being left (C3, N1).
    """
    if not provider_id or host_id in (None, ""):
        return ""
    row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return ""
    if PROVIDER_TYPES.get(row["type"], {}).get("read_only"):
        # B9: Vauxtra cannot remove anything from a read-only proxy (Traefik reads its
        # routes from its own configuration). Queued, the removal was retried forever.
        add_log(
            "info",
            f"{public_host} left {row['name']}, which is read-only: remove its route there by hand",
            conn,
        )
        conn.commit()
        return ""
    conn.commit()  # M1: no transaction held across the HTTP call below
    try:
        removed = bool(create_provider(row).delete_host(host_id))
        failure = "" if removed else "the provider refused the removal"
    except Exception as e:
        removed, failure = False, str(e)
    if removed:
        return ""
    conn.execute(
        "INSERT OR IGNORE INTO pending_proxy_removals (provider_id, host_id, public_host) "
        "VALUES (?,?,?)",
        (provider_id, str(host_id), public_host),
    )
    message = (
        f"Could not remove {public_host} from its former proxy ({row['name']}): {failure}. "
        "The removal will be retried."
    )
    add_log("warn", message, conn)
    # M1: the caller goes on to other providers' HTTP calls; the queued entry and its log
    # line must not hold SQLite's writer lock across them.
    conn.commit()
    return message


def retry_pending_proxy_removals(conn) -> int:
    """Retry the removals `withdraw_former_proxy_host` queued. Returns how many succeeded.

    A route a service has taken back since (same proxy, same id) is no longer stale: its
    entry is dropped without touching the provider.
    """
    done = 0
    for pending in conn.execute("SELECT * FROM pending_proxy_removals ORDER BY id").fetchall():
        # M8: a service that publishes this hostname on this proxy again -- as its own proxy
        # or as an extra target in `service_push_targets` -- owns the route now; hostnames
        # compare case-insensitively, as the unique index does.
        owner = conn.execute(
            f"""SELECT 1 FROM services s
               WHERE (s.proxy_provider_id=? AND CAST(s.npm_host_id AS TEXT)=?)
                  OR (s.tunnel_provider_id=? AND lower(trim(s.tunnel_hostname))=lower(?))
                  OR (s.id IN (SELECT id FROM services WHERE {PUBLIC_HOSTNAME_SQL}=lower(trim(?)))
                      AND (s.proxy_provider_id=? OR s.tunnel_provider_id=?
                           OR EXISTS (SELECT 1 FROM service_push_targets t
                                      WHERE t.service_id=s.id AND t.provider_id=?
                                        AND t.role='proxy')))
               LIMIT 1""",  # noqa: S608 - constant SQL fragment
            (pending["provider_id"], pending["host_id"], pending["provider_id"], pending["host_id"],
             pending["public_host"] or "", pending["provider_id"], pending["provider_id"],
             pending["provider_id"]),
        ).fetchone()
        if owner:
            conn.execute("DELETE FROM pending_proxy_removals WHERE id=?", (pending["id"],))
            conn.commit()
            continue
        row = conn.execute(
            "SELECT * FROM providers WHERE id=?", (pending["provider_id"],)
        ).fetchone()
        if not row:
            continue  # the cascade removes the entry with its provider
        expired = conn.execute(
            "SELECT julianday('now') - julianday(?) > ?",
            (pending["created_at"], PENDING_REMOVAL_TTL_DAYS),
        ).fetchone()[0]
        if PROVIDER_TYPES.get(row["type"], {}).get("read_only") or expired:
            # B9: a read-only provider never accepts the removal, and a provider that has
            # refused it for weeks will not change its mind on the next cycle: the entry is
            # dropped with a line saying what is left to do, rather than retried forever.
            conn.execute("DELETE FROM pending_proxy_removals WHERE id=?", (pending["id"],))
            add_log(
                "warning",
                f"Gave up removing {pending['public_host']} from {row['name']}: remove its route there by hand",
                conn,
            )
            conn.commit()
            continue
        conn.commit()  # no transaction held across the HTTP call below
        host_id = pending["host_id"]
        try:
            removed = bool(create_provider(row).delete_host(int(host_id) if host_id.isdigit() else host_id))
        except Exception:
            removed = False
        if removed:
            conn.execute("DELETE FROM pending_proxy_removals WHERE id=?", (pending["id"],))
            add_log("info", f"Former proxy route removed on {row['name']}: {pending['public_host']}", conn)
            conn.commit()
            done += 1
    return done


def withdraw_service_routes(
    conn, svc, sid: int, *, only_provider_ids: set[int] | None = None
) -> list[str]:
    """Take a service's public route off every provider that may still serve it.

    Deleting a service walked `proxy_provider_id` and `dns_provider_id` and stopped there.
    The extra targets in `service_push_targets` -- the second proxy, the second DNS server,
    the ones a push writes to on every cycle -- were never told: their routes outlived the
    deletion, the hostname stayed resolvable, the proxy kept forwarding, and nothing was
    left in Vauxtra to show for it.

    `only_provider_ids` narrows the withdrawal to part of the holders, for the two cases
    where the service itself survives: a multi-sync target dropped from an edit, and a
    provider being deleted while other targets go on serving the same hostname.

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
        # Every `add_log(..., conn)` below opens SQLite's single write transaction; it must
        # not stay open across the next provider's HTTP calls.
        conn.commit()
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
                add_log("info", f"[Delete] Proxy route removed on {row['name']}: {public_host}", conn)
            else:
                errors.append(f"Failed to delete proxy host on {row['name']}")
        except Exception as e:
            errors.append(f"Proxy ({row['name']}): {e}")

    for row in dns_rows:
        conn.commit()
        try:
            dns = create_provider(row)
            actual_ip = ""
            try:
                entry = next(iter(_records_of_name(dns, public_host) or []), None)
                actual_ip = (entry or {}).get("ip") or (entry or {}).get("answer") or ""
            except Exception:
                actual_ip = ""
            # The record on the server is what has to go, and it may have drifted away from
            # the address Vauxtra last stored; `dns_ip` is the fallback, not the reference.
            ip = actual_ip or (svc["dns_ip"] or "")
            if not ip:
                continue

            if dns.delete_rewrite(public_host, ip):
                add_log("info", f"[Delete] DNS record removed on {row['name']}: {public_host}", conn)
            else:
                errors.append(f"Failed to delete DNS rewrite on {row['name']}")
        except Exception as e:
            errors.append(f"DNS ({row['name']}): {e}")

    if expose_mode == "tunnel" and not proxy_rows and only_provider_ids is None:
        # Only when the whole service is being withdrawn. A narrowed withdrawal legitimately
        # leaves the tunnel out of the selection, and reporting that as a missing provider
        # would turn "this DNS server no longer serves it" into a failure.
        errors.append("No tunnel provider left to remove the route from")

    return errors


def _build_push_plan(conn, svc, sid: int) -> dict:
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)

    proxy_actions: list[dict] = []
    dns_actions: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    dns_target = ""
    dns_target_source = ""
    if expose_mode != "tunnel":
        dns_target_mode = _col(svc, "public_target_mode") or "manual"
        manual_value = svc["dns_ip"] if dns_target_mode != "auto" else ""
        dns_target, dns_target_source = resolve_dns_target(
            conn,
            mode=dns_target_mode,
            manual_value=manual_value,
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=svc["dns_ip"] or "",
            dns_provider_ids=[r["id"] for r in dns_targets],
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
            if not host_id:
                host_id = _find_host_id(proxy, public_host)

            proxy_actions.append(
                {
                    "provider_id": row["id"],
                    "provider_name": row["name"],
                    "provider_type": row["type"],
                    "action": "update" if host_id else "create",
                    "target_host": public_host,
                    "target_origin": f"{svc['forward_scheme']}://{svc['target_ip']}:{svc['target_port']}",
                }
            )
        except Exception as e:
            errors.append(f"Proxy ({row['name']}): {e}")

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
    }


#: Drift details used to be English sentences built here, which is the one place they can
#: never be translated. Each templated shape now also carries the key its sentence was
#: written from and the values it was built out of, so the UI can say the same thing in the
#: reader's language. `detail` stays as the plain-text fallback (logs, older clients).
def _compute_service_drift(conn, svc, sid: int) -> dict:
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
    issues: list[dict] = []

    expected_origin = f"{svc['forward_scheme']}://{svc['target_ip']}:{svc['target_port']}"

    for row in proxy_targets:
        try:
            provider = create_provider(row)
            # None means the list could not be read. It used to become an empty list, so an
            # unreachable provider reported every route as missing instead of "check failed".
            hosts = provider.list_hosts()
            if hosts is None:
                raise RuntimeError("Could not read the provider's routes")
            hit = None
            for host in hosts:
                domains = host.get("domains") or host.get("domain_names") or []
                # Host names are case-insensitive; compare them the way the DNS side does.
                if _dns_name(public_host) in {_dns_name(d) for d in domains}:
                    hit = host
                    break

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
                    "detail": str(e),
                }
            )

    if expose_mode != "tunnel" and svc["dns_ip"]:
        for row in dns_targets:
            try:
                provider = create_provider(row)
                rewrites = _records_of_name(provider, public_host)
                if rewrites is None:
                    raise RuntimeError("Could not read the provider's DNS records")
                match = next(iter(rewrites), None)
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
                    answer = _dns_name(match.get("answer") or match.get("ip"))
                    expected = _dns_name(svc["dns_ip"])
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
                        "detail": str(e),
                    }
                )

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
    if not svc["enabled"]:
        conn.close()
        raise HTTPException(
            409,
            "This service is disabled: pushing it would publish a hostname the interface "
            "shows as off. Enable it first.",
        )
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
    """
    if not dict(svc).get("enabled", 1):
        # Every write route withholds a disabled service; the push re-published it.
        return {"ok": False, "errors": ["Service is disabled: nothing was pushed"]}
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
    # Every DNS target of the service, not only the ones pushed now: one address is
    # written to all of them, so a public one among them forbids a LAN address (H2).
    all_dns_ids = [r["id"] for r in dns_targets]
    if only_provider_ids is not None:
        proxy_targets = [r for r in proxy_targets if r["id"] in only_provider_ids]
        dns_targets = [r for r in dns_targets if r["id"] in only_provider_ids]
    errors = []

    for row in proxy_targets:
        if PROVIDER_TYPES.get(row["type"], {}).get("read_only"):
            add_log("info", f"[Push] Skipped read-only proxy provider ({row['type']}) for {public_host}", conn)
            continue

        # Released before each provider's HTTP calls: the previous iteration's log line and
        # `npm_host_id` write would otherwise hold SQLite's single writer lock across them.
        conn.commit()
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
            errors.append(f"Proxy ({row['name']}): {e}")

    if expose_mode != "tunnel":
        dns_target_mode = _col(svc, "public_target_mode") or "manual"
        manual_value = svc["dns_ip"] if dns_target_mode != "auto" else ""
        # The proxy loop above leaves its writes (`npm_host_id`, journal lines) open, and
        # the resolution below may detect the public IP over the network.
        conn.commit()
        dns_target, dns_target_source = resolve_dns_target(
            conn,
            mode=dns_target_mode,
            manual_value=manual_value,
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=svc["dns_ip"] or "",
            dns_provider_ids=all_dns_ids,
        )

        if not dns_target and dns_targets:
            # The dry-run has always refused this. The push skipped the loop below without a
            # word and returned `{"ok": true, "errors": []}`, so the preview of the action and
            # the action itself contradicted each other about the same service, and the one
            # that reported success is the one that writes records.
            sentence = describe_public_target_failure(dns_target_source)[1]
            errors.append(sentence)
            add_log("error", f"[Push] Nothing pushed to DNS for {public_host}: {sentence}", conn)

        dns_pushed_ids: set[int] = set()
        if dns_target:
            for row in dns_targets:
                conn.commit()
                try:
                    dns = create_provider(row)
                    # Read the actual current value from the provider (not just DB) to fix drift,
                    # from the zone of this name only (C2): one unreadable zone elsewhere on the
                    # server used to block the push of every service.
                    actual_rewrites = _records_of_name(dns, public_host)
                    if actual_rewrites is None:
                        # Unreadable is not empty: adding blind left two records for one
                        # name on AdGuard and Pi-hole.
                        errors.append(_refused("DNS", row["name"], f"read of the records for {public_host}"))
                        add_log("error", f"[Push] DNS records of {row['name']} could not be read; nothing pushed for {public_host}", conn)
                        continue
                    actual_entry = next(
                        (e for e in actual_rewrites
                         if _dns_name(e.get("domain")) == _dns_name(public_host)),
                        None,
                    )
                    actual_ip = (actual_entry or {}).get("ip") or (actual_entry or {}).get("answer", "")
                    failure = ""
                    # Compared as DNS values (M14): `2001:db8:0::1` against a stored
                    # `2001:db8::1` is no change. Compared as names, the push "moved" the record
                    # onto itself, and the delete of the old spelling removed the only record.
                    if actual_ip and record_value_key(actual_ip) != record_value_key(dns_target):
                        failure = _move_dns_record(dns, public_host, actual_rewrites, actual_ip, dns_target)
                    elif not actual_ip and not dns.add_rewrite(public_host, dns_target):
                        failure = f"creation of {public_host} → {dns_target}"

                    if failure:
                        errors.append(_refused("DNS", row["name"], failure))
                        add_log("error", f"[Push] DNS refused the {failure} on {row['name']}", conn)
                        continue

                    dns_pushed_ids.add(row["id"])
                    add_log("info", f"[Push] DNS synced on {row['name']}: {public_host} → {dns_target}", conn)
                except Exception as e:
                    errors.append(f"DNS ({row['name']}): {e}")

        # M6: the new address is stored once a DNS server holds it. Written before the
        # provider calls, a refused write left the row naming an address no server had,
        # and the auto-DNS, comparing against the row, saw nothing left to fix.
        # The row names the address of the *primary* DNS server, the one auto-DNS compares
        # against: a push narrowed to the extra targets (`push_extra_targets`), or one where
        # only a secondary accepted the write, must not move it while the primary still
        # serves the old address. With no DNS server at all there is nothing to wait for.
        primary_dns_id = svc["dns_provider_id"]
        primary_holds_it = (
            primary_dns_id in dns_pushed_ids if primary_dns_id in all_dns_ids
            else bool(dns_pushed_ids)
        )
        if dns_target and dns_target != (svc["dns_ip"] or "") and (primary_holds_it or not all_dns_ids):
            cur = conn.execute(
                "UPDATE services SET dns_ip=? WHERE id=? AND COALESCE(dns_ip, '')=?",
                (dns_target, sid, svc["dns_ip"] or ""),
            )
            if cur.rowcount:
                add_log("info", f"[Push] DNS target refreshed for {public_host}: {svc['dns_ip']} → {dns_target} ({dns_target_source})", conn)

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
    # `write`, like every route the server acts on from the outside: it lists what the
    # providers hold, but listing means one outbound call per enabled provider, and a
    # failure writes a journal line. A `read` key could fire both on demand.
    require_auth_or_setup(request, scope="write")
    conn = get_db()
    providers = conn.execute("SELECT * FROM providers WHERE enabled=1").fetchall()
    # N22: compared in the same normal form as the names the providers hand back, and route
    # ids per provider: route 12 on one NPM is not route 12 on another.
    existing_fqdns = {
        _dns_name(f"{r['subdomain']}.{r['domain']}")
        for r in conn.execute("SELECT subdomain, domain FROM services").fetchall()
    }
    existing_tunnel_hosts = {
        (r["tunnel_hostname"] or "").strip().lower()
        for r in conn.execute("SELECT tunnel_hostname FROM services WHERE tunnel_hostname IS NOT NULL AND tunnel_hostname <> ''").fetchall()
    }
    existing_npm_ids = {
        (r["proxy_provider_id"], str(r["npm_host_id"]))
        for r in conn.execute(
            "SELECT proxy_provider_id, npm_host_id FROM services WHERE npm_host_id IS NOT NULL"
        ).fetchall()
    }
    conn.close()

    result = {"proxy_hosts": [], "dns_rewrites": []}
    for p in providers:
        try:
            provider = create_provider(p)
            meta = PROVIDER_TYPES.get(p["type"], {})
            caps = meta.get("capabilities", {})
            is_proxy = bool(caps.get("proxy")) or meta.get("category") == "proxy"
            is_dns = bool(caps.get("dns")) or meta.get("category") == "dns"

            if is_proxy:
                hosts = provider.list_hosts()
                if hosts is None:
                    raise RuntimeError("could not read the provider's host list")
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

                    domains = [_dns_name(d) for d in (h.get("domains") or h.get("domain_names") or []) if d]
                    already_by_domain = any(d in existing_fqdns or d in existing_tunnel_hosts for d in domains)
                    already_by_id = h.get("id") is not None and (p["id"], str(h.get("id"))) in existing_npm_ids
                    h["_provider_id"]      = p["id"]
                    h["_provider_name"]    = p["name"]
                    h["_provider_type"]    = p["type"]
                    h["_provider_readonly"] = PROVIDER_TYPES.get(p["type"], {}).get("read_only", False)
                    h["_already_imported"] = already_by_id or already_by_domain
                result["proxy_hosts"].extend(hosts)
            elif is_dns:
                rewrites = provider.list_rewrites()
                if rewrites is None:
                    raise RuntimeError("could not read the provider's DNS records")
                for r in rewrites:
                    r["_provider_id"]      = p["id"]
                    r["_provider_name"]    = p["name"]
                    r["_already_imported"] = _dns_name(r.get("domain")) in existing_fqdns
                result["dns_rewrites"].extend(rewrites)
        except Exception as e:
            add_log("error", f"Sync {p['name']}: {e}")

    return result


class ServicesImportIn(BaseModel):
    """The body of `/api/services/import`: the two lists `/api/sync` returned.

    Each element is still checked one by one (through `ServiceIn`), so one bad entry is
    reported in `errors` and the rest imports. The body itself used to be any JSON value,
    and a list or a string in place of these lists ended as a 500.
    """

    proxy_hosts: list[dict] = Field(default_factory=list)
    dns_rewrites: list[dict] = Field(default_factory=list)


def _proxy_host_ids(conn, provider_ids: set) -> dict:
    """The route ids each proxy provider holds now, or None when its list cannot be read.

    Called before the import writes anything, so no transaction is open across the calls.
    """
    known: dict = {}
    for pid in provider_ids:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
        if not row:
            known[pid] = None
            continue
        try:
            hosts = create_provider(row).list_hosts()
        except Exception:
            hosts = None
        known[pid] = None if hosts is None else {str(h.get("id")) for h in hosts if h.get("id") is not None}
    return known


@router.post("/api/services/import")
def import_services(request: Request, data: ServicesImportIn):
    require_auth_or_setup(request, scope="write")
    # Late import: `services` imports this module.
    from app.api.services import ServiceIn, _conflicting_service

    imported = 0
    errors   = []
    conn     = get_db()

    def _norm(name) -> str:
        return str(name or "").strip().rstrip(".").lower()

    dns_by_fqdn = {
        _norm(r.get("domain")): r for r in data.dns_rewrites if _norm(r.get("domain"))
    }

    # The route id each imported service is bound to came from the body as is. A made-up id
    # bound the service to a route that does not exist, and an id already bound to another
    # service made the two share one route, so deleting either removed the other's.
    # N23: the provider ids come from the request body. Each one has to exist and play the
    # role it is given, or the service is bound to nothing, or to a DNS server as its proxy.
    provider_types = {r["id"]: r["type"] for r in conn.execute("SELECT id, type FROM providers")}

    known_host_ids = _proxy_host_ids(
        conn,
        {
            h.get("_provider_id")
            for h in data.proxy_hosts
            if h.get("id") is not None
            and provider_types.get(h.get("_provider_id")) not in (None, "cloudflare_tunnel")
        },
    )
    claimed_host_ids: set = set()

    def _plays(pid, role: str) -> bool:
        ptype = provider_types.get(pid)
        if ptype is None:
            return False
        if role == "tunnel":
            return ptype == "cloudflare_tunnel"
        meta = PROVIDER_TYPES.get(ptype, {})
        return bool(meta.get("capabilities", {}).get(role)) or meta.get("category") == role

    def _refuse(fqdn: str, message: str) -> None:
        # C5: a name whose proxy host is refused must not come back through the DNS-only
        # loop below as a port-80 service nobody asked for.
        errors.append(message)
        dns_by_fqdn.pop(fqdn, None)

    for h in data.proxy_hosts:
        fqdn = ""
        try:
            domains = h.get("domains") or h.get("domain_names", [])
            if not domains:
                continue
            fqdn  = _norm(domains[0])
            parts = fqdn.split(".", 1)
            if len(parts) < 2:
                continue

            # B16: the role follows the provider as stored, not the `_provider_type` the body
            # declares: a tunnel declared as "npm" (or the reverse) was bound in the wrong
            # role, its route id unchecked.
            provider_type = provider_types.get(h.get("_provider_id")) or ""

            dns_match       = dns_by_fqdn.get(fqdn)
            dns_provider_id = dns_match.get("_provider_id") if dns_match else None
            dns_ip          = dns_match.get("answer", "") if dns_match else ""

            role = "tunnel" if provider_type == "cloudflare_tunnel" else "proxy"
            if not _plays(h.get("_provider_id"), role):
                _refuse(fqdn, f"{fqdn}: provider {h.get('_provider_id')} is not a known {role} provider")
                continue
            if dns_provider_id is not None and not _plays(dns_provider_id, "dns"):
                _refuse(fqdn, f"{fqdn}: provider {dns_provider_id} is not a known DNS provider")
                continue

            # Each element goes through the same model as a service created by hand: the
            # import used to write whatever the body held (any string as a target, any port,
            # mixed-case names that then missed every case-sensitive lookup).
            tunnel = provider_type == "cloudflare_tunnel"
            checked = ServiceIn(
                subdomain=parts[0],
                domain=parts[1],
                target_ip=str(h.get("host", h.get("forward_host", "")) or ""),
                target_port=h.get("port", h.get("forward_port", 80)),
                forward_scheme=h.get("scheme", h.get("forward_scheme", "http")),
                websocket=bool(h.get("websocket", h.get("allow_websocket_upgrade", False))),
                expose_mode="tunnel" if tunnel else "proxy_dns",
                tunnel_provider_id=h.get("_provider_id") if tunnel else None,
                tunnel_hostname=fqdn if tunnel else "",
                proxy_provider_id=None if tunnel else h.get("_provider_id"),
                dns_provider_id=None if tunnel else dns_provider_id,
                dns_ip="" if tunnel else str(dns_ip or ""),
            )
            subdomain, domain = checked.subdomain, checked.domain
            # Same rule as a service created by hand: names compare without case, and a
            # tunnel hostname counts too. An exact match let "App" sit next to "app".
            public_host = fqdn if tunnel else f"{subdomain}.{domain}".lower()
            if _conflicting_service(conn, public_host, subdomain=subdomain, domain=domain):
                continue

            host_id = h.get("id")
            if not tunnel and host_id is not None:
                pid = checked.proxy_provider_id
                ids = known_host_ids.get(h.get("_provider_id"))
                if ids is None:
                    _refuse(fqdn, f"{fqdn}: could not check route {host_id} on the proxy provider")
                    continue
                if str(host_id) not in ids:
                    _refuse(fqdn, f"{fqdn}: route {host_id} does not exist on the proxy provider")
                    continue
                taken = conn.execute(
                    "SELECT 1 FROM services WHERE proxy_provider_id=? AND CAST(npm_host_id AS TEXT)=?",
                    (pid, str(host_id)),
                ).fetchone()
                if taken or (pid, str(host_id)) in claimed_host_ids:
                    _refuse(fqdn, f"{fqdn}: route {host_id} is already bound to another service")
                    continue
                claimed_host_ids.add((pid, str(host_id)))

            if tunnel:
                conn.execute(
                    """INSERT INTO services
                       (subdomain, domain, target_ip, target_port, forward_scheme,
                        websocket, expose_mode, tunnel_provider_id, tunnel_hostname,
                        dns_provider_id, dns_ip)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        subdomain,
                        domain,
                        checked.target_ip,
                        checked.target_port,
                        checked.forward_scheme,
                        int(checked.websocket),
                        "tunnel",
                        checked.tunnel_provider_id,
                        checked.tunnel_hostname,
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
                     checked.target_ip,
                     checked.target_port,
                     checked.forward_scheme,
                     int(checked.websocket),
                     checked.proxy_provider_id, h.get("id"), checked.dns_provider_id,
                     checked.dns_ip),
                )
            conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
            imported += 1
            dns_by_fqdn.pop(fqdn, None)
            add_log("info", f"Imported: {fqdn}" + (" (proxy + DNS)" if dns_match else ""), conn)
        except Exception as e:
            _refuse(fqdn, str(e))

    for fqdn, r in dns_by_fqdn.items():
        try:
            parts = fqdn.split(".", 1)
            if not r.get("answer") or len(parts) < 2:
                continue
            checked = ServiceIn(
                subdomain=parts[0], domain=parts[1],
                target_ip=str(r.get("answer", "")), target_port=80,
                dns_provider_id=r.get("_provider_id"), dns_ip=str(r.get("answer", "")),
            )
            subdomain, domain, ip = checked.subdomain, checked.domain, checked.dns_ip
            if not _plays(checked.dns_provider_id, "dns"):
                errors.append(f"{fqdn}: provider {checked.dns_provider_id} is not a known DNS provider")
                continue
            existing = conn.execute(
                "SELECT id, dns_provider_id, dns_ip FROM services WHERE lower(subdomain)=? AND lower(domain)=?",
                (subdomain.lower(), domain.lower()),
            ).fetchone()
            if existing and existing["dns_provider_id"] and (
                existing["dns_provider_id"] != checked.dns_provider_id
                or _dns_name(existing["dns_ip"]) != _dns_name(ip)
            ):
                # N23: the service already publishes this name elsewhere, or to another
                # address. Linking used to overwrite that without a word.
                errors.append(
                    f"{fqdn}: already published by service #{existing['id']} on another DNS "
                    "provider or address; left unchanged"
                )
                continue
            if existing:
                conn.execute(
                    "UPDATE services SET dns_provider_id=?, dns_ip=? WHERE id=?",
                    (checked.dns_provider_id, ip, existing["id"]),
                )
                add_log("info", f"DNS linked: {fqdn} → {ip}", conn)
            else:
                conn.execute(
                    """INSERT INTO services
                       (subdomain, domain, target_ip, target_port, dns_provider_id, dns_ip)
                       VALUES (?,?,?,?,?,?)""",
                    (subdomain, domain, checked.target_ip, 80, checked.dns_provider_id, ip),
                )
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
                imported += 1
                add_log("info", f"Imported from DNS: {fqdn} → {ip}", conn)
        except Exception as e:
            errors.append(str(e))

    conn.commit()
    conn.close()
    return {"imported": imported, "errors": errors}
