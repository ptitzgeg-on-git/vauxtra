import socket
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.api.sync import push_extra_targets, withdraw_service_routes
from app.auth import require_auth
from app.models import (
    add_log,
    get_db,
    row_to_service,
    set_environments,
    set_push_targets,
    set_tags,
)
from app.providers.factory import create_provider, host_id_is_hostname
from app.public_target import (
    describe_public_target_failure,
    resolve_public_target,
    suggest_public_targets,
)
from app.text import plural
from app.validators import (
    DOMAIN_REASONS,
    FQDN_REASONS,
    SUBDOMAIN_REASONS,
    domain_problem,
    fqdn_problem,
    is_valid_hostname,
    is_valid_port,
    normalize_domain,
    subdomain_problem,
)

router = APIRouter()


def _sync_npm_statuses(conn) -> None:
    """Sync enabled/disabled status from NPM for all services using NPM proxy.

    Owns its transaction: it commits each service's write before reaching for the next
    one, because between two services it talks to NPM over HTTP and SQLite admits one
    writer at a time. Held open across those calls, this loop was itself producing the
    "database is locked" that its own error branch below learned to ignore.
    """
    try:
        # Find all services with NPM proxy host
        services = conn.execute("""
            SELECT s.id, s.npm_host_id, s.proxy_provider_id, s.enabled
            FROM services s
            WHERE s.npm_host_id IS NOT NULL
              AND s.proxy_provider_id IS NOT NULL
              AND s.expose_mode = 'proxy_dns'
        """).fetchall()

        # One listing per proxy, not one per service. Ten services behind the same NPM
        # asked it for the same list ten times a cycle, and each of those round trips was
        # time the cycle spent holding a connection open instead of finishing.
        hosts_by_provider: dict[int, list] = {}

        for svc in services:
            try:
                provider_id = int(svc["proxy_provider_id"])
                if provider_id not in hosts_by_provider:
                    proxy_row = conn.execute(
                        "SELECT * FROM providers WHERE id=?",
                        (provider_id,)
                    ).fetchone()
                    hosts_by_provider[provider_id] = (
                        create_provider(proxy_row).list_hosts() or [] if proxy_row else []
                    )
                hosts = hosts_by_provider[provider_id]
                if not hosts:
                    continue

                # Find matching host by ID
                npm_host = next(
                    (h for h in hosts if h.get("id") == svc["npm_host_id"]),
                    None
                )
                if npm_host and "enabled" in npm_host:
                    npm_enabled = bool(npm_host["enabled"])
                    vauxtra_enabled = bool(svc["enabled"])
                    
                    # If status mismatch, sync from NPM
                    if npm_enabled != vauxtra_enabled:
                        conn.execute(
                            "UPDATE services SET enabled=? WHERE id=?",
                            (int(npm_enabled), svc["id"])
                        )
                        add_log(
                            "info",
                            f"Synced service {svc['id']} status from NPM: {'enabled' if npm_enabled else 'disabled'}",
                            conn
                        )
                        conn.commit()
            except Exception as e:
                # Log but don't block: continue syncing other services.
                # SQLite lock contention can happen under concurrent writes; avoid warning spam.
                msg = str(e).lower()
                if "database is locked" not in msg:
                    add_log("warn", f"Failed to sync NPM status for service {svc['id']}: {e}", conn)
    except Exception:
        # Non-blocking: sync failures shouldn't break service list
        pass


def _service_fqdn(subdomain: str, domain: str) -> str:
    return f"{subdomain}.{domain}".strip(".").lower()


def _service_public_hostname(expose_mode: str, tunnel_hostname: str, subdomain: str, domain: str) -> str:
    if expose_mode == "tunnel":
        host = (tunnel_hostname or "").strip().lower()
        if host:
            return host
    return _service_fqdn(subdomain, domain)


def _service_target_reachable(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return True, f"Reachable in {elapsed} ms"
    except Exception as e:
        return False, str(e)


def _primary_push_targets(body) -> tuple[int | None, int | None]:
    """The proxy and DNS ids the service row will really hold, given the mode.

    The de-duplication of the multi-sync targets is written against these: an extra equal to
    a primary is not an extra, because the primary already receives the route. But the write
    routes blank the columns the mode does not use *before* comparing -- `add_service` stores
    `tunnel_provider_id` only in tunnel mode, and `dns_provider_id` only in proxy_dns mode --
    so the comparison is not the one a reading of the payload alone suggests.

    The preflight compared against `proxy_provider_id or tunnel_provider_id` regardless of
    mode, and so answered a different question from the route it is a preflight for. Measured
    on `{expose_mode: proxy_dns, proxy_provider_id: null, tunnel_provider_id: 5,
    extra_proxy_provider_ids: [5]}`: the preflight emitted no `extra_proxy_provider` line at
    all, and POST /api/services answered 201 with provider 5 holding the new host and a
    `service_push_targets` row for it. The mirror case is a tunnel-mode body whose
    `dns_provider_id` repeats an id in `extra_dns_provider_ids`. Shared, so that the two
    cannot drift apart again.
    """
    if body.expose_mode == "tunnel":
        return body.tunnel_provider_id, None
    return body.proxy_provider_id, body.dns_provider_id


#: Every preflight check carries `detail` (the English sentence, kept for logs and older
#: clients) plus `detail_key` -- the short code the sentence was written from -- and the
#: values it was built out of. The UI looks up `expose.preflight.detail.<detail_key>` so the
#: line is read in the reader's language, and falls back to `detail` when the code is new.
#:
#: `blocking` is not a severity, it is a claim about the save routes. The Expose panel greys
#: out "Create route" while `summary.blocking_failures` is above zero and offers nothing to
#: press instead, so `blocking: True` promises that POST /api/services -- and, for a body
#: carrying a `service_id`, PUT /api/services/{sid} -- would refuse the same body. A check
#: that promises that and is wrong is a dead end: a red badge, a "Re-run checks" button that
#: will fail forever, and an API that accepts the body anyway.
#:
#: So the rule, for every check in `_run_preflight` and `_check_provider`: block only what
#: the save routes really refuse, warn about everything else. The unit of the rule is the
#: branch and not the check name: `proxy_provider` and `dns_provider` each carry four of
#: them, and two of the four land on opposite verdicts. Measured through all three routes on
#: the same body, these refuse and keep their badge -- `host_taken` (409), `target_none`
#: (400), `dns_target_required` and `dns_target_detection_failed` (400), `provider_missing`
#: on a primary provider and on an extra one alike (400, from `_unknown_references`), and
#: `provider_required`, which never reaches this function at all because
#: `validate_mode_dependencies` answers 422 first.
#: These do not, and are warnings: `target_reachable`, `proxy_connection`, `dns_connection`,
#: `extra_provider_disabled`, `provider_disabled` on the primary proxy or DNS server, and
#: `tunnel_health` in all three of its shapes. Each of the last four was measured answering
#: `201 {"errors": []}` with the proxy host created, the DNS rewrite written, or the tunnel
#: ingress rule published -- `add_service` reads no `enabled` column and asks no tunnel how
#: it feels; it fetches the provider row and pushes.
def _public_target_refusal(conn, source: str, dns_provider_id: int | None) -> dict:
    """The body of the 400 raised when a DNS provider has no target to write.

    Named after the provider the operator chose: an instance holding several DNS providers
    would otherwise give the same anonymous sentence whichever one is at fault.
    """
    name = ""
    if dns_provider_id:
        row = conn.execute("SELECT name FROM providers WHERE id=?", (int(dns_provider_id),)).fetchone()
        name = row["name"] if row else ""
    detail_key, sentence = describe_public_target_failure(source, name)
    return {"message": sentence, "detail_key": detail_key}


def _detail(key: str, text: str, **params) -> dict:
    out = {"detail": text, "detail_key": key}
    if params:
        out["detail_params"] = params
    return out


def _check_provider(conn, provider_id: int | None, *, role: str, required: bool) -> tuple[dict | None, dict]:
    if not provider_id:
        if required:
            return None, {
                "name": f"{role}_provider",
                "ok": False,
                "blocking": True,
                **_detail("provider_required", f"{role.capitalize()} provider is required"),
            }
        return None, {
            "name": f"{role}_provider",
            "ok": True,
            "blocking": False,
            **_detail("provider_optional", f"{role.capitalize()} provider not set (optional)"),
        }

    row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return None, {
            "name": f"{role}_provider",
            "ok": False,
            "blocking": True,
            **_detail("provider_missing", f"{role.capitalize()} provider not found"),
        }
    if not row["enabled"]:
        # A warning under the blocking rule above: `add_service` selects the provider row and
        # pushes to it without ever reading `enabled`, so a disabled primary answers 201 with
        # the proxy host created and the DNS rewrite written. `extra_provider_disabled` below
        # is already a warning for the weaker case -- the extras really are skipped -- and it
        # would have been absurd for the target that does receive the route to be the gate.
        return dict(row), {
            "name": f"{role}_provider",
            "ok": False,
            "blocking": False,
            **_detail("provider_disabled", f"{role.capitalize()} provider is disabled"),
        }

    return dict(row), {
        "name": f"{role}_provider",
        "ok": True,
        "blocking": False,
        **_detail(
            "provider_ready",
            f"{role.capitalize()} provider ready: {row['name']}",
            name=row["name"],
        ),
    }


def _run_preflight(conn, body, service_id: int | None = None) -> dict:
    checks: list[dict] = []

    public_host = _service_public_hostname(body.expose_mode, body.tunnel_hostname, body.subdomain, body.domain)

    # The address the save route resolves from. `add_service` starts from nothing and
    # `update_service` starts from the row it is about to overwrite, so a preflight that
    # always started from nothing answered for one route and guessed for the other: in auto
    # mode a service already holding a target read `dns_target_resolution` red while the PUT
    # kept that very target and answered 200. `service_id` is what the panel sends when it
    # is editing; the two branches below are the two save routes, spelled the same way.
    current_dns_ip = ""
    if service_id:
        current_row = conn.execute("SELECT dns_ip FROM services WHERE id=?", (int(service_id),)).fetchone()
        current_dns_ip = (current_row["dns_ip"] or "") if current_row else ""

    # Route uniqueness check
    rows = conn.execute(
        "SELECT id, subdomain, domain, expose_mode, tunnel_hostname FROM services"
    ).fetchall()
    conflicting_id = None
    for row in rows:
        rid = int(row["id"])
        if service_id and rid == service_id:
            continue
        row_host = _service_public_hostname(
            (row["expose_mode"] or "proxy_dns").strip().lower(),
            row["tunnel_hostname"] or "",
            row["subdomain"],
            row["domain"],
        )
        if row_host == public_host:
            conflicting_id = rid
            break

    checks.append(
        {
            "name": "public_host_conflict",
            "ok": conflicting_id is None,
            "blocking": True,
            **(
                _detail("host_free", "No existing route conflict")
                if conflicting_id is None
                else _detail(
                    "host_taken",
                    f"Route already exists on service #{conflicting_id}",
                    id=conflicting_id,
                )
            ),
        }
    )

    # Target reachability. The round trip is timed here rather than read back out of the
    # helper's sentence, so the number survives translation.
    #
    # A warning under the blocking rule above: `add_service` never probes the target -- this
    # function is the only caller of `_service_target_reachable`, and it is not on the save
    # path. Blocking here forbade a configuration the product publishes without a murmur: a
    # Vauxtra that cannot see the target's VLAN while the reverse proxy can, a firewall that
    # only opens for the proxy, a backend switched off while its route is prepared, a name
    # only the proxy's Docker network resolves.
    started = time.monotonic()
    reachable, detail = _service_target_reachable(body.target_ip, body.target_port)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    checks.append(
        {
            "name": "target_reachable",
            "ok": reachable,
            "blocking": False,
            **(
                _detail("target_reachable", detail, ms=elapsed_ms)
                if reachable
                else _detail("target_unreachable", f"Target not reachable: {detail}", reason=detail)
            ),
        }
    )

    if body.forward_scheme == "https" and int(body.target_port) == 80:
        checks.append(
            {
                "name": "https_port_hint",
                "ok": False,
                "blocking": False,
                **_detail(
                    "https_port_hint",
                    "Forward scheme is HTTPS but port is 80. Verify backend TLS termination.",
                ),
            }
        )

    if body.expose_mode == "tunnel":
        tunnel_provider_row, tunnel_check = _check_provider(
            conn,
            body.tunnel_provider_id,
            role="tunnel",
            required=True,
        )
        checks.append(tunnel_check)

        if tunnel_provider_row:
            # Warnings under the blocking rule above, all three shapes of it. `add_service`
            # asks the tunnel nothing: it fetches the row and calls `create_host`. Measured on
            # the same body through both routes, a provider answering `health_status()` with
            # `{"ok": False}`, one whose `test_connection()` returns False, and one whose
            # health endpoint raises each came back `201 {"errors": []}` with the ingress rule
            # published. A tunnel that is down at preflight time is also the case most likely
            # to be up a minute later, which is exactly what the greyed-out button forbade.
            try:
                provider = create_provider(tunnel_provider_row)
                if hasattr(provider, "health_status"):
                    health = provider.health_status()
                    checks.append(
                        {
                            "name": "tunnel_health",
                            "ok": bool(health.get("ok")),
                            "blocking": False,
                            **_detail(
                                "tunnel_status",
                                f"Tunnel status: {health.get('status', 'unknown')}",
                                status=str(health.get("status", "unknown")),
                            ),
                            "data": health,
                        }
                    )
                else:
                    ok = bool(provider.test_connection())
                    checks.append(
                        {
                            "name": "tunnel_health",
                            "ok": ok,
                            "blocking": False,
                            **(
                                _detail("tunnel_reachable", "Tunnel provider reachable")
                                if ok
                                else _detail("tunnel_unreachable", "Tunnel provider not reachable")
                            ),
                        }
                    )
            except Exception as e:
                checks.append(
                    {
                        "name": "tunnel_health",
                        "ok": False,
                        "blocking": False,
                        **_detail(
                            "tunnel_check_failed",
                            f"Tunnel health check failed: {e}",
                            error=str(e),
                        ),
                    }
                )

    else:
        has_any_proxy_target = bool(body.proxy_provider_id) or bool(body.extra_proxy_provider_ids)
        has_any_dns_target = bool(body.dns_provider_id) or bool(body.extra_dns_provider_ids)
        checks.append(
            {
                "name": "provider_target_required",
                "ok": has_any_proxy_target or has_any_dns_target,
                "blocking": True,
                **(
                    _detail("target_set", "At least one provider target is configured")
                    if (has_any_proxy_target or has_any_dns_target)
                    else _detail(
                        "target_none",
                        "Select at least one proxy, DNS, or tunnel provider target",
                    )
                ),
            }
        )

        proxy_provider_row, proxy_check = _check_provider(
            conn,
            body.proxy_provider_id,
            role="proxy",
            required=False,
        )
        dns_provider_row, dns_check = _check_provider(
            conn,
            body.dns_provider_id,
            role="dns",
            required=False,
        )
        checks.append(proxy_check)
        checks.append(dns_check)

        if proxy_provider_row:
            try:
                ok = bool(create_provider(proxy_provider_row).test_connection())
            except Exception:
                ok = False
            checks.append(
                {
                    "name": "proxy_connection",
                    "ok": ok,
                    "blocking": False,
                    **(
                        _detail("proxy_ok", "Proxy provider connection is healthy")
                        if ok
                        else _detail("proxy_failed", "Proxy provider connection test failed")
                    ),
                }
            )

        if dns_provider_row:
            # The DNS server receives the record exactly as the proxy receives the host, and
            # only the proxy was ever dialled: `dns_provider` above reads a row, it does not
            # knock. A server that had moved, lost its token or closed its port read "Ready"
            # in green and refused the rewrite one click later. Named, because an instance
            # holding several DNS providers would otherwise not say which one went quiet.
            try:
                ok = bool(create_provider(dns_provider_row).test_connection())
            except Exception:
                ok = False
            checks.append(
                {
                    "name": "dns_connection",
                    "ok": ok,
                    "blocking": False,
                    **(
                        _detail(
                            "dns_ok",
                            f"DNS provider connection is healthy: {dns_provider_row['name']}",
                            name=dns_provider_row["name"],
                        )
                        if ok
                        else _detail(
                            "dns_failed",
                            f"DNS provider connection test failed: {dns_provider_row['name']}",
                            name=dns_provider_row["name"],
                        )
                    ),
                }
            )

        if body.dns_provider_id:
            resolved_target, target_source = resolve_public_target(
                conn,
                mode=body.public_target_mode,
                manual_value=body.dns_ip,
                proxy_provider_id=body.proxy_provider_id,
                current_value=current_dns_ip,
            )
            checks.append(
                {
                    "name": "dns_target_resolution",
                    "ok": bool(resolved_target),
                    "blocking": True,
                    **(
                        _detail(
                            "dns_resolved",
                            f"Resolved public DNS target: {resolved_target} ({target_source})",
                            target=str(resolved_target),
                            source=str(target_source),
                        )
                        if resolved_target
                        else _detail(*describe_public_target_failure(target_source))
                    ),
                    "data": {"resolved_target": resolved_target, "source": target_source},
                }
            )

    # A route published on several DNS servers, or several proxies, had exactly one of them
    # looked at: the extra ids only ever fed the "at least one target is set" boolean above.
    # Since the save route learned to push to them, the silence became a false green -- a
    # second server that was down answered "all checks passed" and then refused the record,
    # in a 207 the panel had had every chance to foresee. Outside the mode branch on purpose:
    # a tunnel service carries extra proxies too, and `_collect_push_targets` reads the same
    # table for it.
    primary_proxy_id, primary_dns_id = _primary_push_targets(body)
    for role, extra_ids, primary_id in (
        ("proxy", body.extra_proxy_provider_ids, primary_proxy_id),
        ("dns", body.extra_dns_provider_ids, primary_dns_id),
    ):
        for pid in dict.fromkeys(int(p) for p in (extra_ids or []) if p):
            if pid == primary_id:
                # A primary is not an extra. `_primary_push_targets` is what makes that the
                # same sentence here and in `add_service`, mode included.
                continue
            name = f"extra_{role}_provider"
            row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
            if not row:
                # The one gate of the three, and it is earned: `_unknown_references` turns an
                # id pointing at nothing into a 400, so this body really is refused.
                checks.append(
                    {
                        "name": name,
                        "ok": False,
                        "blocking": True,
                        **_detail(
                            "provider_missing",
                            f"Extra {role} provider #{pid} no longer exists",
                            id=pid,
                        ),
                    }
                )
                continue
            if not row["enabled"]:
                # The push skips a disabled target instead of failing on it, so this one costs
                # the operator nothing but a target that will receive nothing.
                checks.append(
                    {
                        "name": name,
                        "ok": False,
                        "blocking": False,
                        **_detail(
                            "extra_provider_disabled",
                            f"Extra {role} provider is disabled: {row['name']}",
                            name=row["name"],
                        ),
                    }
                )
                continue
            try:
                ok = bool(create_provider(dict(row)).test_connection())
            except Exception:
                ok = False
            checks.append(
                {
                    "name": name,
                    "ok": ok,
                    "blocking": False,
                    **(
                        _detail(
                            "extra_provider_ok",
                            f"Extra {role} provider answers: {row['name']}",
                            name=row["name"],
                        )
                        if ok
                        else _detail(
                            "extra_provider_failed",
                            f"Extra {role} provider does not answer: {row['name']}",
                            name=row["name"],
                        )
                    ),
                }
            )

    blocking_failures = [c for c in checks if c.get("blocking") and not c.get("ok")]
    warnings = [c for c in checks if (not c.get("blocking")) and (not c.get("ok"))]

    return {
        "ok": len(blocking_failures) == 0,
        "public_host": public_host,
        "checks": checks,
        "summary": {
            "blocking_failures": len(blocking_failures),
            "warnings": len(warnings),
            "total": len(checks),
        },
    }


class ServiceIn(BaseModel):
    # Unknown keys are rejected rather than ignored. A client that reads a service back and
    # PUTs it again sends `tags`/`environments` (the serialized relations), not the
    # `tag_ids`/`environment_ids` this model expects: pydantic's default would drop them
    # silently and apply the empty defaults, and `set_tags` starts with a DELETE. A 422 is
    # the only outcome that does not quietly wipe a service's tags.
    model_config = ConfigDict(extra="forbid")

    subdomain:         str
    domain:            str
    target_ip:         str
    target_port:       int
    forward_scheme:    str  = "http"
    websocket:         bool = False
    enabled:           bool = True
    dns_provider_id:   int | None = None
    proxy_provider_id: int | None = None
    tunnel_provider_id: int | None = None
    expose_mode:       str = "proxy_dns"
    public_target_mode: str = "manual"
    auto_update_dns:   bool = False
    tunnel_hostname:   str = ""
    dns_ip:            str  = ""
    tag_ids:           list[int] = []
    environment_ids:   list[int] = []
    icon_url:          str       = ""
    extra_proxy_provider_ids: list[int] = []
    extra_dns_provider_ids: list[int] = []

    @field_validator("subdomain")
    @classmethod
    def val_subdomain(cls, v):
        # The message names the rule that was broken, not just the field: eight different
        # mistakes used to answer "Invalid subdomain", which tells an operator nothing about
        # the one character to change.
        v = v.strip().lower()
        problem = subdomain_problem(v, allow_wildcard=True)
        if problem:
            raise ValueError(f"Invalid subdomain: {SUBDOMAIN_REASONS[problem]}")
        return v

    @field_validator("domain")
    @classmethod
    def val_domain(cls, v):
        val = normalize_domain(v)
        problem = domain_problem(val)
        if problem:
            raise ValueError(f"Invalid domain: {DOMAIN_REASONS[problem]}")
        return val

    @field_validator("target_ip")
    @classmethod
    def val_ip(cls, v):
        v = v.strip()
        if not is_valid_hostname(v):
            raise ValueError("Invalid target IP or hostname")
        return v

    @field_validator("target_port")
    @classmethod
    def val_port(cls, v):
        if not is_valid_port(v):
            raise ValueError("Invalid port (1–65535)")
        return int(v)

    @field_validator("forward_scheme")
    @classmethod
    def val_scheme(cls, v):
        if v not in ("http", "https"):
            raise ValueError("Invalid scheme")
        return v

    @field_validator("dns_ip")
    @classmethod
    def val_dns_ip(cls, v):
        v = (v or "").strip().lower()
        if v and not is_valid_hostname(v):
            raise ValueError("Invalid DNS public target")
        return v

    @field_validator("expose_mode")
    @classmethod
    def val_expose_mode(cls, v):
        v = (v or "proxy_dns").strip().lower()
        if v not in ("proxy_dns", "tunnel"):
            raise ValueError("Unsupported expose mode")
        return v

    @field_validator("public_target_mode")
    @classmethod
    def val_public_target_mode(cls, v):
        v = (v or "manual").strip().lower()
        if v not in ("manual", "auto"):
            raise ValueError("Invalid public target mode")
        return v

    @field_validator("tunnel_hostname")
    @classmethod
    def val_tunnel_hostname(cls, v):
        val = (v or "").strip().lower()
        if val and not is_valid_hostname(val):
            raise ValueError("Invalid tunnel hostname")
        return val

    @model_validator(mode="after")
    def validate_mode_dependencies(self):
        if self.expose_mode == "tunnel" and not self.tunnel_provider_id:
            raise ValueError("Tunnel provider is required in tunnel mode")
        return self

    @model_validator(mode="after")
    def validate_hostname_length(self):
        # A rule about the pair, so it cannot live on either field: a 250-character
        # subdomain and a 10-character domain are each acceptable on their own and the name
        # they make is not. Raised here it is a 422 with a sentence; left out it was a saved
        # route every provider refused separately, later, each in its own words.
        problem = fqdn_problem(self.subdomain, self.domain)
        if problem:
            raise ValueError(f"Invalid hostname: {FQDN_REASONS[problem]}")
        return self


class ServicePreflightIn(ServiceIn):
    service_id: int | None = None


@router.get("/api/services/public-target/suggest")
def suggest_public_target(
    request: Request,
    proxy_provider_id: int | None = Query(default=None),
):
    require_auth(request)
    conn = get_db()
    try:
        return suggest_public_targets(conn, proxy_provider_id=proxy_provider_id)
    finally:
        conn.close()


@router.post("/api/services/preflight")
def preflight_service(request: Request, body: ServicePreflightIn):
    # `write`: the body carries an arbitrary target host and port that the server then
    # connects to. Left at "any authenticated caller", a read-only key was a port scanner
    # pointed at whatever the Vauxtra container can reach.
    require_auth(request, scope="write")
    conn = get_db()
    try:
        return _run_preflight(conn, body, service_id=body.service_id)
    finally:
        conn.close()



@router.get("/api/services")
def list_services(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute("""
        SELECT s.*,
               dp.name AS dns_provider_name, dp.type AS dns_type,
               pp.name AS proxy_provider_name, pp.type AS proxy_type,
             tp.name AS tunnel_provider_name, tp.type AS tunnel_type,
               GROUP_CONCAT(DISTINCT t.name || ':' || t.color || ':' || t.id) AS tags_raw,
               GROUP_CONCAT(DISTINCT e.name || ':' || e.color || ':' || e.id) AS envs_raw
        FROM services s
        LEFT JOIN providers dp ON s.dns_provider_id  = dp.id
        LEFT JOIN providers pp ON s.proxy_provider_id = pp.id
         LEFT JOIN providers tp ON s.tunnel_provider_id = tp.id
        LEFT JOIN service_tags st ON st.service_id = s.id
        LEFT JOIN tags t ON t.id = st.tag_id
        LEFT JOIN service_environments se ON se.service_id = s.id
        LEFT JOIN environments e ON e.id = se.environment_id
        GROUP BY s.id
        ORDER BY s.domain, s.subdomain
    """).fetchall()

    targets_by_service: dict[int, list[dict]] = {}
    service_ids = [r["id"] for r in rows]
    if service_ids:
        placeholders = ",".join(["?"] * len(service_ids))
        targets = conn.execute(
            f"""
            SELECT spt.service_id,
                   spt.role,
                   p.id AS provider_id,
                   p.name AS provider_name,
                   p.type AS provider_type,
                   p.enabled AS provider_enabled
            FROM service_push_targets spt
            JOIN providers p ON p.id = spt.provider_id
            WHERE spt.service_id IN ({placeholders})
            ORDER BY p.name
            """,
            service_ids,
        ).fetchall()

        for t in targets:
            sid = t["service_id"]
            if sid not in targets_by_service:
                targets_by_service[sid] = []
            targets_by_service[sid].append({
                "role": t["role"],
                "provider_id": t["provider_id"],
                "provider_name": t["provider_name"],
                "provider_type": t["provider_type"],
                "provider_enabled": bool(t["provider_enabled"]),
            })

    conn.close()

    out = []
    for r in rows:
        service = row_to_service(r)
        push_targets = targets_by_service.get(r["id"], [])
        service["push_targets"] = push_targets
        service["extra_proxy_provider_ids"] = [
            t["provider_id"] for t in push_targets if t["role"] == "proxy"
        ]
        service["extra_dns_provider_ids"] = [
            t["provider_id"] for t in push_targets if t["role"] == "dns"
        ]
        out.append(service)

    return out


@router.get("/api/services/history")
def services_history(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute("""
        SELECT service_id, status, created_at
        FROM uptime_events
        WHERE created_at >= datetime('now', '-24 hours')
        ORDER BY service_id, created_at
    """).fetchall()
    conn.close()
    result: dict = {}
    for row in rows:
        sid = row["service_id"]
        if sid not in result:
            result[sid] = []
        result[sid].append({"status": row["status"], "created_at": row["created_at"]})
    return result


@router.get("/api/services/{sid}")
def get_service(sid: int, request: Request):
    """Return one service with provider metadata, tags, environments and push targets."""
    require_auth(request)
    conn = get_db()
    row = conn.execute(
        """
        SELECT s.*,
               dp.name AS dns_provider_name, dp.type AS dns_type,
               pp.name AS proxy_provider_name, pp.type AS proxy_type,
               tp.name AS tunnel_provider_name, tp.type AS tunnel_type,
               GROUP_CONCAT(DISTINCT t.name || ':' || t.color || ':' || t.id) AS tags_raw,
               GROUP_CONCAT(DISTINCT e.name || ':' || e.color || ':' || e.id) AS envs_raw
        FROM services s
        LEFT JOIN providers dp ON s.dns_provider_id = dp.id
        LEFT JOIN providers pp ON s.proxy_provider_id = pp.id
        LEFT JOIN providers tp ON s.tunnel_provider_id = tp.id
        LEFT JOIN service_tags st ON st.service_id = s.id
        LEFT JOIN tags t ON t.id = st.tag_id
        LEFT JOIN service_environments se ON se.service_id = s.id
        LEFT JOIN environments e ON e.id = se.environment_id
        WHERE s.id = ?
        GROUP BY s.id
        """,
        (sid,),
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(404, "Service not found")

    push_targets_rows = conn.execute(
        """
        SELECT spt.role,
               p.id AS provider_id,
               p.name AS provider_name,
               p.type AS provider_type,
               p.enabled AS provider_enabled
        FROM service_push_targets spt
        JOIN providers p ON p.id = spt.provider_id
        WHERE spt.service_id=?
        ORDER BY p.name
        """,
        (sid,),
    ).fetchall()
    conn.close()

    service = row_to_service(row)
    push_targets = [
        {
            "role": t["role"],
            "provider_id": t["provider_id"],
            "provider_name": t["provider_name"],
            "provider_type": t["provider_type"],
            "provider_enabled": bool(t["provider_enabled"]),
        }
        for t in push_targets_rows
    ]
    service["push_targets"] = push_targets
    service["extra_proxy_provider_ids"] = [
        t["provider_id"] for t in push_targets if t["role"] == "proxy"
    ]
    service["extra_dns_provider_ids"] = [
        t["provider_id"] for t in push_targets if t["role"] == "dns"
    ]
    return service


def _conflicting_service(conn, public_host: str, exclude_id: int | None = None):
    """The service already answering for `public_host`, if there is one.

    Comparison goes through `_service_public_hostname` rather than the raw columns, because
    a tunnel service publishes `tunnel_hostname` -- so a tunnel and a proxy service can
    collide while their `(subdomain, domain)` pairs differ, which the unique index in
    `app/models.py` cannot see.
    """
    for row in conn.execute(
        "SELECT id, subdomain, domain, expose_mode, tunnel_hostname FROM services"
    ).fetchall():
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        existing = _service_public_hostname(
            row["expose_mode"] or "proxy_dns",
            row["tunnel_hostname"] or "",
            row["subdomain"],
            row["domain"],
        )
        if existing == public_host:
            return row
    return None


def _unknown_references(conn, body: ServiceIn) -> list[str]:
    """Name every id in the payload that points at nothing.

    Both write endpoints call the proxy and the DNS provider *first*, and only then write the
    rows -- `INSERT INTO services`, `set_push_targets`, `set_tags`, `set_environments`.
    Foreign keys are enforced (`app/db.py`), so a single unknown id raised `IntegrityError`
    well after the public hostname had been published: the transaction rolled back, the route
    stayed up, and nothing in the database described it any more. It could not be listed, and
    therefore not deleted, from Vauxtra.

    Checking here costs one query per kind and turns that 500-and-an-orphan into a 400 that
    names the offending id, before anything reaches a provider.
    """
    unknown: list[str] = []

    def _check(table: str, ids, label: str) -> None:
        wanted = sorted({int(i) for i in ids if i})
        if not wanted:
            return
        marks = ",".join("?" * len(wanted))
        found = {
            r["id"]
            for r in conn.execute(f"SELECT id FROM {table} WHERE id IN ({marks})", tuple(wanted))
        }
        unknown.extend(f"{label} {i}" for i in wanted if i not in found)

    _check("tags", body.tag_ids or [], "tag")
    _check("environments", body.environment_ids or [], "environment")
    _check(
        "providers",
        [body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id]
        + list(body.extra_proxy_provider_ids or [])
        + list(body.extra_dns_provider_ids or []),
        "provider",
    )
    return unknown


@router.post("/api/services", status_code=201)
def add_service(request: Request, body: ServiceIn):
    require_auth(request, scope="write")
    if body.expose_mode == "proxy_dns":
        has_any_proxy_target = bool(body.proxy_provider_id) or bool(body.extra_proxy_provider_ids)
        has_any_dns_target = bool(body.dns_provider_id) or bool(body.extra_dns_provider_ids)
        if not (has_any_proxy_target or has_any_dns_target):
            raise HTTPException(400, "At least one proxy or DNS provider target is required")

    public_host = _service_public_hostname(body.expose_mode, body.tunnel_hostname, body.subdomain, body.domain)
    conn   = get_db()

    unknown = _unknown_references(conn, body)
    if unknown:
        conn.close()
        raise HTTPException(400, f"Nothing was created -- unknown {', '.join(unknown)}")

    clash = _conflicting_service(conn, public_host)
    if clash:
        conn.close()
        raise HTTPException(
            409,
            f"{public_host} is already served by service #{clash['id']}. Two services on one "
            "hostname push over each other -- edit that one, or choose another hostname.",
        )

    # Resolved before a single provider is touched. This refusal used to live past the
    # proxy creation, so a target nobody could resolve first created a host on the remote
    # proxy and then deleted it again -- through a compensating delete whose own failure was
    # swallowed by a bare `except: pass`. Nothing to compensate if nothing was done yet.
    dns_target = ""
    dns_target_source = ""
    if body.expose_mode == "proxy_dns" and body.dns_provider_id:
        dns_target, dns_target_source = resolve_public_target(
            conn,
            mode=body.public_target_mode,
            manual_value=body.dns_ip,
            proxy_provider_id=body.proxy_provider_id,
            current_value="",
        )
        if not dns_target:
            refusal = _public_target_refusal(conn, dns_target_source, body.dns_provider_id)
            conn.close()
            raise HTTPException(400, refusal)

    errors = []

    npm_host_id = None

    if body.expose_mode == "tunnel":
        row = conn.execute("SELECT * FROM providers WHERE id=?", (body.tunnel_provider_id,)).fetchone()
        if not row:
            errors.append("Tunnel provider not found")
        elif not body.enabled:
            # Created disabled: publishing the ingress rule now would make the hostname
            # publicly reachable while the UI shows the service as off. It is published
            # when the service is enabled.
            add_log("info", f"Tunnel route not published (service created disabled): {public_host}")
        else:
            try:
                proxy = create_provider(row)
                result = proxy.create_host(
                    public_host,
                    body.target_ip,
                    body.target_port,
                    body.forward_scheme,
                    body.websocket,
                    None,
                )
                if result:
                    add_log("info", f"Tunnel route created: {public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                else:
                    errors.append("Failed to create tunnel route")
                    add_log("error", f"Tunnel route failed: {public_host}")
            except Exception as e:
                errors.append(str(e))
    else:
        if body.proxy_provider_id and not body.enabled:
            # Same reasoning as the tunnel branch. `npm_host_id` stays NULL, which is exactly
            # the state the enable path already knows how to re-deploy from.
            add_log("info", f"Proxy host not created (service created disabled): {public_host}")
        elif body.proxy_provider_id:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (body.proxy_provider_id,)).fetchone()
            if row:
                try:
                    proxy = create_provider(row)
                    cert_id = proxy.find_best_certificate(body.domain)
                    result = proxy.create_host(
                        public_host,
                        body.target_ip,
                        body.target_port,
                        body.forward_scheme,
                        body.websocket,
                        cert_id,
                    )
                    if result:
                        npm_host_id = result.get("id")
                        add_log("info", f"Proxy created: {public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                    else:
                        errors.append("Failed to create proxy host")
                        add_log("error", f"Proxy failed: {public_host}")
                except Exception as e:
                    errors.append(str(e))

        if body.dns_provider_id:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if row and not body.enabled:
                # The resolved target is still stored, so the enable path can re-add the
                # record; only the push is withheld.
                add_log("info", f"DNS record not added (service created disabled): {public_host}")
            elif row:
                try:
                    dns = create_provider(row)
                    if dns.add_rewrite(public_host, dns_target):
                        add_log("info", f"DNS added: {public_host} → {dns_target} ({dns_target_source})")
                    else:
                        errors.append("Failed to create DNS rewrite")
                        add_log("error", f"DNS failed: {public_host}")
                except Exception as e:
                    errors.append(str(e))
        else:
            dns_target = body.dns_ip

    stored_proxy_provider_id = body.proxy_provider_id if body.expose_mode == "proxy_dns" else None
    stored_dns_provider_id = body.dns_provider_id if body.expose_mode == "proxy_dns" else None
    stored_tunnel_provider_id = body.tunnel_provider_id if body.expose_mode == "tunnel" else None
    stored_public_target_mode = body.public_target_mode if body.expose_mode == "proxy_dns" else "manual"
    stored_auto_update_dns = body.auto_update_dns if body.expose_mode == "proxy_dns" else False
    stored_tunnel_hostname = public_host if body.expose_mode == "tunnel" else ""
    stored_dns_target = dns_target if body.expose_mode == "proxy_dns" else ""

    cur = conn.execute(
        """INSERT INTO services
           (subdomain, domain, target_ip, target_port, forward_scheme,
                websocket, enabled, dns_provider_id, proxy_provider_id, tunnel_provider_id,
                expose_mode, public_target_mode, auto_update_dns, tunnel_hostname,
                dns_ip, npm_host_id, icon_url)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (body.subdomain, body.domain, body.target_ip, body.target_port,
            body.forward_scheme, int(body.websocket), int(body.enabled),
         stored_dns_provider_id, stored_proxy_provider_id, stored_tunnel_provider_id,
         body.expose_mode, stored_public_target_mode, int(stored_auto_update_dns), stored_tunnel_hostname,
         stored_dns_target, npm_host_id,
         body.icon_url),
    )
    sid = cur.lastrowid

    # Same call the preflight makes, so the two agree on which ids are extras.
    primary_proxy_provider_id, primary_dns_provider_id = _primary_push_targets(body)
    extra_proxy_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_proxy_provider_ids)
        if pid and pid != primary_proxy_provider_id
    ]
    extra_dns_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_dns_provider_ids)
        if pid and pid != primary_dns_provider_id
    ]

    set_push_targets(conn, sid, extra_proxy_ids, extra_dns_ids)

    if body.tag_ids:
        set_tags(conn, sid, body.tag_ids)
    if body.environment_ids:
        set_environments(conn, sid, body.environment_ids)
    conn.commit()

    # The block above published on one proxy and one DNS server. The multi-sync targets were
    # recorded a few lines up and nothing pushed to them, so a service created with a second
    # DNS server answered 201 with no errors while that server stayed empty. Committed first:
    # these are provider HTTP calls, and holding SQLite's single writer across them is what
    # `update_service` already documents as the cause of `database is locked`.
    errors.extend(push_extra_targets(conn, sid))

    conn.close()

    from fastapi.responses import JSONResponse
    return JSONResponse({"id": sid, "fqdn": public_host, "errors": errors}, status_code=201 if not errors else 207)


@router.put("/api/services/{sid}")
def update_service(sid: int, request: Request, body: ServiceIn):
    require_auth(request, scope="write")
    conn = get_db()
    old  = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not old:
        conn.close()
        raise HTTPException(404, "Service not found")

    # The refusal `add_service` opens with, missing here. A service could be edited down to
    # no provider target at all: measured on `{proxy_provider_id: null, dns_provider_id:
    # null}`, POST answered 400 and PUT answered 200 with the hostname moved and nothing
    # left anywhere to serve it -- on a body whose preflight had already marked `target_none`
    # blocking and greyed the button out. Raised after the 404 so a PUT on a service that is
    # not there still says so first.
    if body.expose_mode == "proxy_dns":
        has_any_proxy_target = bool(body.proxy_provider_id) or bool(body.extra_proxy_provider_ids)
        has_any_dns_target = bool(body.dns_provider_id) or bool(body.extra_dns_provider_ids)
        if not (has_any_proxy_target or has_any_dns_target):
            conn.close()
            raise HTTPException(400, "At least one proxy or DNS provider target is required")

    unknown = _unknown_references(conn, body)
    if unknown:
        # Same reasoning as `add_service`: the providers are reconfigured before the row is
        # rewritten, so an id that does not exist used to be discovered only once the public
        # hostname had already moved.
        conn.close()
        raise HTTPException(400, f"Nothing was changed -- unknown {', '.join(unknown)}")

    clash = _conflicting_service(
        conn,
        _service_public_hostname(body.expose_mode, body.tunnel_hostname, body.subdomain, body.domain),
        exclude_id=sid,
    )
    if clash:
        conn.close()
        raise HTTPException(
            409,
            f"That hostname is already served by service #{clash['id']}. Two services on one "
            "hostname push over each other -- edit that one, or choose another hostname.",
        )

    old_mode = (old["expose_mode"] or "proxy_dns").strip().lower()
    new_mode = body.expose_mode
    new_public_host = _service_public_hostname(new_mode, body.tunnel_hostname, body.subdomain, body.domain)
    old_public_host = _service_public_hostname(old_mode, old["tunnel_hostname"] or "", old["subdomain"], old["domain"])

    # `add_service` refuses a DNS provider it has no target for. Editing one in accepted the
    # same state and answered 200: the service listed its DNS provider in the interface, no
    # record was ever written, and `push_service` then reported `{"ok": true, "errors": []}`
    # for it while `push/dry-run` reported the opposite about the very same service.
    #
    # That refusal was reachable only in theory. A blanket `dns_ip = old["dns_ip"]` sat in
    # front of it to absorb a detection blip, and it fired in manual mode too -- where
    # nothing is detected and so nothing can blip. An operator who cleared the address field
    # got 200 and the stale address written back, on a body `POST /api/services` refuses
    # with 400 and the preflight had just marked `blocking_failures: 1, ok: false`. A blip is
    # already absorbed one layer down: `resolve_public_target` receives the stored value as
    # `current_value` and `suggest_public_targets` offers it as the `current` candidate,
    # ranked by the operator's own priority policy. Doing it a second time here only
    # overrode a policy that had dropped `current` on purpose.
    dns_ip = ""
    dns_target_source = "n/a"
    if new_mode == "proxy_dns":
        dns_ip, dns_target_source = resolve_public_target(
            conn,
            mode=body.public_target_mode,
            manual_value=body.dns_ip,
            proxy_provider_id=body.proxy_provider_id,
            current_value=old["dns_ip"] or "",
        )
        if body.dns_provider_id and not dns_ip:
            refusal = _public_target_refusal(conn, dns_target_source, body.dns_provider_id)
            conn.close()
            raise HTTPException(400, refusal)

    errors = []

    next_npm_host_id = None
    if new_mode == "proxy_dns" and old_mode == "proxy_dns" and old["proxy_provider_id"] == body.proxy_provider_id:
        next_npm_host_id = old["npm_host_id"]

    if new_mode == "tunnel":
        if old_mode == "proxy_dns":
            if old["proxy_provider_id"] and old["npm_host_id"]:
                old_proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["proxy_provider_id"],)).fetchone()
                if old_proxy_row:
                    try:
                        create_provider(old_proxy_row).delete_host(old["npm_host_id"])
                    except Exception as e:
                        add_log("warn", f"Could not clean up old proxy host during mode switch: {e}")
            if old["dns_provider_id"] and old["dns_ip"]:
                old_dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["dns_provider_id"],)).fetchone()
                if old_dns_row:
                    try:
                        create_provider(old_dns_row).delete_rewrite(old_public_host, old["dns_ip"])
                    except Exception as e:
                        add_log("warn", f"Could not clean up old DNS rewrite during mode switch: {e}")

        tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.tunnel_provider_id,)).fetchone()
        if not tunnel_row:
            errors.append("Tunnel provider not found")
        elif not body.enabled:
            # A disabled service must stop being reachable. In tunnel mode the ingress rule
            # is the only thing that exposes it, and the publish path below runs on every
            # PUT without ever reading `enabled`: a request that disabled a service used to
            # re-publish the very route it was meant to cut. The configuration stays in
            # Vauxtra and is published again on re-enable.
            try:
                create_provider(tunnel_row).delete_host(new_public_host)
                add_log("info", f"Tunnel route withdrawn (service disabled): {new_public_host}")
            except Exception as e:
                errors.append(str(e))
            if old_mode == "tunnel" and old["tunnel_provider_id"] and (
                old["tunnel_provider_id"] != body.tunnel_provider_id
                or old_public_host != new_public_host
            ):
                # The hostname or the tunnel changed in the same request: the previous route
                # lives elsewhere and would survive the withdrawal above.
                old_tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["tunnel_provider_id"],)).fetchone()
                if old_tunnel_row:
                    try:
                        create_provider(old_tunnel_row).delete_host(old_public_host)
                    except Exception as e:
                        add_log("warn", f"Could not withdraw the previous tunnel route: {e}")
        else:
            try:
                tunnel = create_provider(tunnel_row)
                if old_mode == "tunnel" and old["tunnel_provider_id"] == body.tunnel_provider_id:
                    ok = tunnel.update_host(
                        old_public_host,
                        new_public_host,
                        body.target_ip,
                        body.target_port,
                        body.forward_scheme,
                        body.websocket,
                        None,
                    )
                else:
                    created = tunnel.create_host(
                        new_public_host,
                        body.target_ip,
                        body.target_port,
                        body.forward_scheme,
                        body.websocket,
                        None,
                    )
                    ok = bool(created)

                    if ok and old_mode == "tunnel" and old["tunnel_provider_id"]:
                        old_tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["tunnel_provider_id"],)).fetchone()
                        if old_tunnel_row:
                            try:
                                create_provider(old_tunnel_row).delete_host(old_public_host)
                            except Exception as e:
                                add_log("warn", f"Could not clean up old tunnel during mode switch: {e}")

                if ok:
                    add_log("info", f"Tunnel updated: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                else:
                    errors.append("Failed to update tunnel route")
                    add_log("error", f"Tunnel update failed: {new_public_host}")
            except Exception as e:
                errors.append(str(e))

    else:
        if old_mode == "tunnel" and old["tunnel_provider_id"]:
            old_tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["tunnel_provider_id"],)).fetchone()
            if old_tunnel_row:
                try:
                    create_provider(old_tunnel_row).delete_host(old_public_host)
                except Exception as e:
                    add_log("warn", f"Could not clean up old tunnel during mode switch: {e}")

        # Skip proxy ops if service stays disabled and host was already removed from provider
        # (host will be re-deployed when the service is enabled again)
        _staying_disabled = not bool(body.enabled) and not bool(old["enabled"])
        _proxy_already_removed = old["npm_host_id"] is None

        if body.proxy_provider_id and not (_staying_disabled and _proxy_already_removed):
            proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.proxy_provider_id,)).fetchone()
            if not proxy_row:
                errors.append("Proxy provider not found")
            else:
                try:
                    proxy = create_provider(proxy_row)
                    cert_id = proxy.find_best_certificate(body.domain)

                    if old_mode == "proxy_dns" and old["proxy_provider_id"] == body.proxy_provider_id and old["npm_host_id"]:
                        ok = proxy.update_host(
                            old["npm_host_id"],
                            new_public_host,
                            body.target_ip,
                            body.target_port,
                            body.forward_scheme,
                            body.websocket,
                            cert_id,
                        )
                        if ok:
                            # A provider that keys its rules on the hostname has just
                            # renamed the rule along with the service: the stored id would
                            # name a rule that no longer exists, and every later toggle,
                            # push and delete would address it.
                            if host_id_is_hostname(proxy, proxy_row["type"]):
                                next_npm_host_id = new_public_host
                            else:
                                next_npm_host_id = old["npm_host_id"]
                        else:
                            errors.append("Failed to update proxy host")
                    else:
                        created = proxy.create_host(
                            new_public_host,
                            body.target_ip,
                            body.target_port,
                            body.forward_scheme,
                            body.websocket,
                            cert_id,
                        )
                        if created:
                            next_npm_host_id = created.get("id")
                            if old_mode == "proxy_dns" and old["proxy_provider_id"] and old["npm_host_id"]:
                                old_proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["proxy_provider_id"],)).fetchone()
                                if old_proxy_row:
                                    try:
                                        create_provider(old_proxy_row).delete_host(old["npm_host_id"])
                                    except Exception as e:
                                        add_log("warn", f"Could not clean up old proxy host: {e}")
                        else:
                            errors.append("Failed to create proxy host")

                    if not errors:
                        add_log("info", f"Proxy updated: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                except Exception as e:
                    errors.append(str(e))

        if body.dns_provider_id and dns_ip and not body.enabled:
            # Publishing the record here and letting the enable/disable block below undo it
            # only worked on a transition. Editing a service that was *already* disabled ran
            # this branch with nothing to undo it, so the record came back and stayed:
            # the host resolved publicly while the UI showed the service as off.
            dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if dns_row:
                try:
                    dns = create_provider(dns_row)
                    old_dns_ip = old["dns_ip"] or ""
                    # The hostname or the address may have changed in the same request; both
                    # the previous record and the new one have to go.
                    targets = {(new_public_host, dns_ip)}
                    if old_mode == "proxy_dns" and old["dns_provider_id"] == body.dns_provider_id and old_dns_ip:
                        targets.add((old_public_host, old_dns_ip))
                    for record_host, record_ip in sorted(targets):
                        dns.delete_rewrite(record_host, record_ip)
                    add_log("info", f"DNS record withheld (service disabled): {new_public_host}")
                except Exception as e:
                    add_log("warn", f"Could not withdraw the DNS record of a disabled service: {e}")
        elif body.dns_provider_id and dns_ip:
            dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if dns_row:
                try:
                    dns = create_provider(dns_row)
                    old_dns_ip = old["dns_ip"] or ""
                    same_provider = old_mode == "proxy_dns" and old["dns_provider_id"] == body.dns_provider_id
                    # A disabled service has no record left to move: it was withdrawn when it
                    # was disabled. Treating "nothing changed" as "nothing to do" would leave
                    # the host unresolvable while the UI reported it back on.
                    published = same_provider and bool(old_dns_ip) and bool(old["enabled"])
                    moved = (old_public_host, old_dns_ip) != (new_public_host, dns_ip)

                    if published:
                        if moved:
                            if dns.update_rewrite(old_public_host, old_dns_ip, new_public_host, dns_ip):
                                add_log("info", f"DNS updated: {new_public_host} → {dns_ip} ({dns_target_source})")
                            else:
                                errors.append("Failed to update DNS rewrite")
                    else:
                        if dns.add_rewrite(new_public_host, dns_ip):
                            # Only clean up a previous record that is genuinely a different
                            # one -- otherwise this deletes what was just added.
                            if old_mode == "proxy_dns" and old["dns_provider_id"] and old_dns_ip and moved:
                                old_dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["dns_provider_id"],)).fetchone()
                                if old_dns_row:
                                    try:
                                        create_provider(old_dns_row).delete_rewrite(old_public_host, old_dns_ip)
                                    except Exception as e:
                                        add_log("warn", f"Could not clean up old DNS rewrite: {e}")
                            add_log("info", f"DNS updated: {new_public_host} → {dns_ip} ({dns_target_source})")
                        else:
                            errors.append("Failed to update DNS rewrite")
                except Exception as e:
                    errors.append(str(e))

    stored_proxy_provider_id = body.proxy_provider_id if new_mode == "proxy_dns" else None
    stored_dns_provider_id = body.dns_provider_id if new_mode == "proxy_dns" else None
    stored_tunnel_provider_id = body.tunnel_provider_id if new_mode == "tunnel" else None
    stored_public_target_mode = body.public_target_mode if new_mode == "proxy_dns" else "manual"
    stored_auto_update_dns = body.auto_update_dns if new_mode == "proxy_dns" else False
    stored_tunnel_hostname = new_public_host if new_mode == "tunnel" else ""
    stored_dns_ip = dns_ip if new_mode == "proxy_dns" else ""

    conn.execute(
        """UPDATE services SET
               subdomain=?, domain=?, target_ip=?, target_port=?,
               forward_scheme=?, websocket=?, enabled=?,
               dns_provider_id=?, proxy_provider_id=?, tunnel_provider_id=?,
               expose_mode=?, public_target_mode=?, auto_update_dns=?, tunnel_hostname=?,
               dns_ip=?, npm_host_id=?, icon_url=?
           WHERE id=?""",
        (body.subdomain, body.domain, body.target_ip, body.target_port,
         body.forward_scheme, int(body.websocket), int(body.enabled),
         stored_dns_provider_id, stored_proxy_provider_id, stored_tunnel_provider_id,
         new_mode, stored_public_target_mode, int(stored_auto_update_dns), stored_tunnel_hostname,
         stored_dns_ip, next_npm_host_id, body.icon_url, sid),
    )

    # Same call the preflight makes, so the two agree on which ids are extras.
    primary_proxy_provider_id, primary_dns_provider_id = _primary_push_targets(body)
    extra_proxy_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_proxy_provider_ids)
        if pid and pid != primary_proxy_provider_id
    ]
    extra_dns_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_dns_provider_ids)
        if pid and pid != primary_dns_provider_id
    ]

    # `set_push_targets` replaces the multi-sync list wholesale, and the UPDATE above may
    # have moved the hostname. Either change leaves a record on a provider Vauxtra is about
    # to stop addressing under that name: a target dropped from the list went on serving the
    # hostname for good, and a rename left the old name published on every extra target
    # while the new one was added beside it. Both are withdrawn here, through `old` -- the
    # row that still spells the hostname and the address those records were written with.
    # The primaries are left out: the block above already moved their record itself.
    previous_extras = {
        r["provider_id"]
        for r in conn.execute(
            "SELECT provider_id FROM service_push_targets WHERE service_id=?", (sid,)
        )
    }
    still_targeted = set(extra_proxy_ids) | set(extra_dns_ids)
    primaries = {pid for pid in (primary_proxy_provider_id, stored_dns_provider_id) if pid}
    renamed = old_public_host != new_public_host
    stale_targets = (previous_extras if renamed else previous_extras - still_targeted) - primaries
    if stale_targets:
        for message in withdraw_service_routes(conn, old, sid, only_provider_ids=stale_targets):
            add_log("warn", f"Could not withdraw {old_public_host} from a former target: {message}", conn)

    set_push_targets(conn, sid, extra_proxy_ids, extra_dns_ids)

    set_tags(conn, sid, body.tag_ids)
    set_environments(conn, sid, body.environment_ids)

    # Committed here rather than at the end of the route. The `UPDATE services` above opens
    # SQLite's write transaction, and the block that follows makes up to three provider HTTP
    # calls at `PROVIDER_TIMEOUT` seconds each. A provider that hangs therefore held the
    # single writer lock for half a minute, and every other writer -- a second operator, the
    # reconcile scheduler -- waited out its 15-second `busy_timeout` and got
    # `database is locked`. WAL keeps readers unaffected; writers are the whole cost.
    #
    # Nothing is lost by splitting the transaction: the provider calls earlier in this route
    # already ran outside it, so the record and the provider were never atomic to begin
    # with, and the block below catches its own exceptions and writes through the same
    # connection, committed by the `conn.commit()` that already closes the route.
    conn.commit()

    # Generalized enable/disable: manage providers when enabled state changes.
    # Tunnel mode is deliberately absent here: it is handled in the `new_mode == "tunnel"`
    # branch above, which runs on every PUT rather than only on a transition, so an already
    # disabled tunnel service converges back to "withdrawn" instead of staying published.
    if bool(old["enabled"]) != bool(body.enabled) and new_mode == "proxy_dns":
        enable = bool(body.enabled)

        # Proxy provider
        if body.proxy_provider_id:
            try:
                proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.proxy_provider_id,)).fetchone()
                if proxy_row:
                    proxy = create_provider(proxy_row)
                    if enable:
                        if next_npm_host_id:
                            # Host exists in provider (was suspended via toggle) — un-suspend it
                            if proxy.toggle_host(next_npm_host_id, True):
                                add_log("info", f"Proxy enabled: {new_public_host}", conn)
                            else:
                                add_log("info", f"Proxy active (toggle not supported, host already present): {new_public_host}", conn)
                        else:
                            # Host was removed from provider when disabled — re-deploy it
                            cert_id = proxy.find_best_certificate(body.domain)
                            created = proxy.create_host(
                                new_public_host, body.target_ip, body.target_port,
                                body.forward_scheme, body.websocket, cert_id,
                            )
                            if created:
                                conn.execute("UPDATE services SET npm_host_id=? WHERE id=?", (created.get("id"), sid))
                                add_log("info", f"Proxy re-deployed on enable: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}", conn)
                            else:
                                errors.append("Failed to re-deploy proxy host on enable")
                    else:
                        # Disable: try to suspend; if not supported, delete from provider
                        if next_npm_host_id:
                            if proxy.toggle_host(next_npm_host_id, False):
                                add_log("info", f"Proxy suspended: {new_public_host}", conn)
                            else:
                                proxy.delete_host(next_npm_host_id)
                                conn.execute("UPDATE services SET npm_host_id=NULL WHERE id=?", (sid,))
                                add_log("info", f"Proxy removed (suspend not supported, config kept in Vauxtra): {new_public_host}", conn)
            except Exception as e:
                add_log("warn", f"Could not manage proxy enabled state: {e}", conn)

        # DNS is deliberately not handled here any more. The block above now branches on
        # `body.enabled` and runs on every PUT, not only on a transition, so it already adds
        # the record on enable and withdraws it on disable. Repeating it here would push the
        # same change twice and log it twice.
    
    conn.commit()
    add_log("info", f"Service updated: {new_public_host}")

    # Same gap as `add_service`: everything above addresses one proxy and one DNS server.
    # Adding a second DNS server through this route answered 200 while that server stayed
    # empty, and the drift check then contradicted the response about the same service.
    errors.extend(push_extra_targets(conn, sid))

    row = conn.execute("""
        SELECT s.*,
               dp.name AS dns_provider_name, dp.type AS dns_type,
               pp.name AS proxy_provider_name, pp.type AS proxy_type,
             tp.name AS tunnel_provider_name, tp.type AS tunnel_type,
               GROUP_CONCAT(DISTINCT t.name || ':' || t.color || ':' || t.id) AS tags_raw,
               GROUP_CONCAT(DISTINCT e.name || ':' || e.color || ':' || e.id) AS envs_raw
        FROM services s
        LEFT JOIN providers dp ON s.dns_provider_id  = dp.id
        LEFT JOIN providers pp ON s.proxy_provider_id = pp.id
         LEFT JOIN providers tp ON s.tunnel_provider_id = tp.id
        LEFT JOIN service_tags st ON st.service_id = s.id
        LEFT JOIN tags t ON t.id = st.tag_id
        LEFT JOIN service_environments se ON se.service_id = s.id
        LEFT JOIN environments e ON e.id = se.environment_id
        WHERE s.id=? GROUP BY s.id""", (sid,)).fetchone()
    push_targets_rows = conn.execute(
        """
        SELECT spt.role,
               p.id AS provider_id,
               p.name AS provider_name,
               p.type AS provider_type,
               p.enabled AS provider_enabled
        FROM service_push_targets spt
        JOIN providers p ON p.id = spt.provider_id
        WHERE spt.service_id=?
        ORDER BY p.name
        """,
        (sid,),
    ).fetchall()
    conn.close()

    service = row_to_service(row)
    push_targets = [
        {
            "role": t["role"],
            "provider_id": t["provider_id"],
            "provider_name": t["provider_name"],
            "provider_type": t["provider_type"],
            "provider_enabled": bool(t["provider_enabled"]),
        }
        for t in push_targets_rows
    ]
    service["push_targets"] = push_targets
    service["extra_proxy_provider_ids"] = [
        t["provider_id"] for t in push_targets if t["role"] == "proxy"
    ]
    service["extra_dns_provider_ids"] = [
        t["provider_id"] for t in push_targets if t["role"] == "dns"
    ]

    return {**service, "errors": errors}


@router.delete("/api/services/{sid}")
def delete_service(sid: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    svc  = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(404, "Service not found")

    mode = (svc["expose_mode"] or "proxy_dns").strip().lower()
    public_host = _service_public_hostname(mode, svc["tunnel_hostname"] or "", svc["subdomain"], svc["domain"])

    # Every provider that may hold a route, not just the two columns on the service row:
    # the extra proxies and DNS servers listed in `service_push_targets` went on serving a
    # deleted service, and nothing in Vauxtra was left to point at them.
    errors = withdraw_service_routes(conn, svc, sid)

    # Boundary-aware, and lowercased so it keeps matching whatever case a message used.
    # `LIKE '%service 1%'` also matched "service 12", "service 100" and every other id that
    # merely starts with this one: deleting service 1 silently purged the monitoring
    # history of nine of its neighbours. The second pattern is the id at end of message.
    conn.execute(
        "DELETE FROM logs WHERE lower(message) GLOB ? OR lower(message) GLOB ?",
        (f"*service {sid}[^0-9]*", f"*service {sid}"),
    )
    conn.execute("DELETE FROM services WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    add_log("info", f"Service deleted: {public_host}")
    # `ok` stays true even with errors: the service is gone from Vauxtra either way, and a
    # false would push a client into retrying a delete that can only answer 404 now. The
    # provider failures are in `errors`, and the caller has to show them.
    return {"ok": True, "errors": errors}


def _check_one(sid: int) -> dict:
    """Probe one service, record the result, and return what the caller measured.

    Shared by both verbs below. The `uptime_events` insert is the same line the scheduler
    writes (`scheduler.py`): without it the 24 h column and the availability tile stayed
    empty no matter how many times an operator pressed the button, and the page ended up
    contradicting itself -- "no check in the last 24 hours" printed above a row that said
    "OK, checked just now".
    """
    conn = get_db()
    svc  = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(404, "Service not found")

    status     = "unknown"
    latency_ms = None
    start      = time.monotonic()
    try:
        with socket.create_connection((svc["target_ip"], svc["target_port"]), timeout=3):
            status     = "ok"
            latency_ms = round((time.monotonic() - start) * 1000, 1)
    except OSError:
        status = "error"

    public_host = _service_public_hostname(
        (svc["expose_mode"] or "proxy_dns").strip().lower(),
        svc["tunnel_hostname"] or "",
        svc["subdomain"],
        svc["domain"],
    )

    dns_resolved: list | None = None
    try:
        dns_results  = socket.getaddrinfo(public_host, None, socket.AF_INET)
        dns_resolved = sorted({r[4][0] for r in dns_results})
    except socket.gaierror:
        dns_resolved = []

    conn.execute(
        "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
        (status, sid),
    )
    conn.execute(
        "INSERT INTO uptime_events (service_id, status) VALUES (?,?)",
        (sid, status),
    )
    conn.commit()
    conn.close()
    add_log("info" if status == "ok" else "error", f"Check {public_host}: {status}")
    return {"id": sid, "status": status, "latency_ms": latency_ms, "dns_resolved": dns_resolved}


@router.post("/api/services/{sid}/check")
def check_service(sid: int, request: Request):
    # `write`: this rewrites `status` and `last_checked`, appends to `uptime_events` and
    # writes a log line. It read as a GET until 1.5.0 and answered any authenticated key,
    # scope or not, which let a read-only key rewrite the state of any route.
    require_auth(request, scope="write")
    return _check_one(sid)


@router.get("/api/services/{sid}/check", deprecated=True)
def check_service_get(sid: int, request: Request):
    """Deprecated alias of `POST /api/services/{sid}/check`, kept one version for scripts.

    It carries the same `write` scope as the POST. A GET that writes is also a GET a
    browser prefetch, a crawler or an uptime probe can fire just by following the link,
    which is the other half of why the POST is the one to call.
    """
    require_auth(request, scope="write")
    return _check_one(sid)


@router.post("/api/services/check-all")
def check_all(request: Request):
    # `write`: this rewrites `status` and `last_checked` on every enabled service.
    require_auth(request, scope="write")
    conn     = get_db()
    services = conn.execute(
        "SELECT id, target_ip, target_port, subdomain, domain, expose_mode FROM services WHERE enabled=1"
    ).fetchall()
    ok_count = error_count = 0
    # The connection is opened either way, so the latency is already measured: throwing it
    # away is what forced the table to tell operators to check rows one at a time to fill
    # the LATENCY column.
    results: list[dict] = []

    for svc in services:
        if (svc["expose_mode"] or "").strip().lower() == "tunnel":
            continue
        status     = "unknown"
        latency_ms = None
        start      = time.monotonic()
        try:
            with socket.create_connection((svc["target_ip"], svc["target_port"]), timeout=3):
                status     = "ok"
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                ok_count  += 1
        except OSError:
            status = "error"
            error_count += 1
        conn.execute(
            "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
            (status, svc["id"]),
        )
        # Same row the scheduler writes, so a manual run feeds the 24 h history too.
        conn.execute(
            "INSERT INTO uptime_events (service_id, status) VALUES (?,?)",
            (svc["id"], status),
        )
        results.append({"id": svc["id"], "status": status, "latency_ms": latency_ms})

    conn.commit()
    conn.close()
    # One line for the run, not one per service: the per-service check already logs each
    # probe, and a fleet of fifty would otherwise bury everything else in "Recent activity".
    add_log(
        "info" if error_count == 0 else "error",
        f"Manual check of {plural(len(results), 'service')}: {ok_count} ok, {error_count} error",
    )
    return {
        "checked": len(services),
        "ok":      ok_count,
        "error":   error_count,
        "results": results,
    }


class _BulkActionBody(BaseModel):
    ids:    list[int]
    action: str  # "enable" | "disable" | "delete"


@router.post("/api/services/bulk")
def bulk_action(body: _BulkActionBody, request: Request):
    """Bulk enable, disable, or delete a list of services by ID."""
    require_auth(request, scope="write")

    if body.action not in ("enable", "disable", "delete"):
        raise HTTPException(400, f"Unknown action '{body.action}'. Allowed: enable, disable, delete")

    if not body.ids:
        return {"ok": True, "affected": 0, "errors": []}

    conn = get_db()
    errors: list[str] = []
    affected = 0

    if body.action in ("enable", "disable"):
        enable = body.action == "enable"
        enabled_val = 1 if enable else 0
        placeholders = ",".join(["?"] * len(body.ids))

        # Fetch full service rows before updating enabled state
        services = conn.execute(
            f"SELECT * FROM services WHERE id IN ({placeholders})",
            body.ids,
        ).fetchall()

        conn.execute(
            f"UPDATE services SET enabled=? WHERE id IN ({placeholders})",
            [enabled_val, *body.ids],
        )
        # Same reasoning as in `update_service`, and it costs more here: the loop below runs
        # up to three provider calls *per service*, so a hung provider held SQLite's single
        # writer lock for `len(ids) x 3 x PROVIDER_TIMEOUT` seconds. The `enabled` flag is
        # what this route promises; publishing it before the provider work starts is also
        # what the UI reads back.
        conn.commit()

        # Generalized enable/disable: manage each provider
        for svc in services:
            sid_b = svc["id"]
            mode = (svc["expose_mode"] or "proxy_dns").strip().lower()
            pub = _service_public_hostname(
                mode, svc["tunnel_hostname"] or "", svc["subdomain"], svc["domain"]
            )

            if mode == "tunnel":
                # These rows used to be skipped entirely: the `enabled` flag was written and
                # nothing else happened, so a bulk disable left every hostname publicly
                # reachable while Vauxtra merely stopped monitoring it.
                if svc["tunnel_provider_id"]:
                    try:
                        tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["tunnel_provider_id"],)).fetchone()
                        if tunnel_row:
                            tunnel = create_provider(tunnel_row)
                            if enable:
                                created = tunnel.create_host(
                                    pub, svc["target_ip"], svc["target_port"],
                                    svc["forward_scheme"], bool(svc["websocket"]), None,
                                )
                                if created:
                                    add_log("info", f"Tunnel route re-published on enable: {pub}", conn)
                                else:
                                    errors.append(f"Service {sid_b}: failed to re-publish the tunnel route")
                            else:
                                if tunnel.delete_host(pub):
                                    add_log("info", f"Tunnel route withdrawn on disable: {pub}", conn)
                                else:
                                    errors.append(f"Service {sid_b}: failed to withdraw the tunnel route")
                    except Exception as e:
                        errors.append(f"Service {sid_b}: tunnel state error — {e}")
                continue

            if mode != "proxy_dns":
                continue

            # Proxy provider
            if svc["proxy_provider_id"]:
                try:
                    proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["proxy_provider_id"],)).fetchone()
                    if proxy_row:
                        proxy = create_provider(proxy_row)
                        if enable:
                            if svc["npm_host_id"]:
                                if proxy.toggle_host(svc["npm_host_id"], True):
                                    add_log("info", f"Proxy enabled: {pub}", conn)
                                else:
                                    add_log("info", f"Proxy active (toggle not supported): {pub}", conn)
                            else:
                                # Was deleted — re-deploy
                                cert_id = proxy.find_best_certificate(svc["domain"])
                                created = proxy.create_host(
                                    pub, svc["target_ip"], svc["target_port"],
                                    svc["forward_scheme"], bool(svc["websocket"]), cert_id,
                                )
                                if created:
                                    conn.execute("UPDATE services SET npm_host_id=? WHERE id=?", (created.get("id"), sid_b))
                                    add_log("info", f"Proxy re-deployed on enable: {pub}", conn)
                                else:
                                    errors.append(f"Service {sid_b}: failed to re-deploy proxy")
                        else:
                            if svc["npm_host_id"]:
                                if proxy.toggle_host(svc["npm_host_id"], False):
                                    add_log("info", f"Proxy suspended: {pub}", conn)
                                else:
                                    proxy.delete_host(svc["npm_host_id"])
                                    conn.execute("UPDATE services SET npm_host_id=NULL WHERE id=?", (sid_b,))
                                    add_log("info", f"Proxy removed (suspend not supported): {pub}", conn)
                except Exception as e:
                    errors.append(f"Service {sid_b}: proxy state error — {e}")

            # DNS provider: remove on disable, re-add on enable
            if svc["dns_provider_id"] and svc["dns_ip"]:
                try:
                    dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["dns_provider_id"],)).fetchone()
                    if dns_row:
                        dns = create_provider(dns_row)
                        if enable:
                            dns.add_rewrite(pub, svc["dns_ip"])
                            add_log("info", f"DNS re-added on enable: {pub} → {svc['dns_ip']}", conn)
                        else:
                            dns.delete_rewrite(pub, svc["dns_ip"])
                            add_log("info", f"DNS removed on disable: {pub}", conn)
                except Exception as e:
                    errors.append(f"Service {sid_b}: DNS state error — {e}")

            # Once per service, not once for the route. The commit above released the writer
            # lock, but every `add_log(..., conn)` in this loop takes it again -- so without
            # this the second service's provider calls ran with the lock held by the first
            # service's log line. It also makes the work durable as it goes: a provider that
            # hangs on service seven no longer costs the audit trail of services one to six.
            conn.commit()

        affected = conn.execute(
            f"SELECT COUNT(*) FROM services WHERE id IN ({placeholders})",
            body.ids,
        ).fetchone()[0]
        conn.commit()
        add_log("info", f"Bulk {body.action}: {plural(affected, 'service')}")

    elif body.action == "delete":
        for sid in body.ids:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
            if not svc:
                errors.append(f"Service {sid} not found")
                continue

            mode = (svc["expose_mode"] or "proxy_dns").strip().lower()
            public_host = _service_public_hostname(
                mode, svc["tunnel_hostname"] or "", svc["subdomain"], svc["domain"]
            )

            # The same walk as the single delete, extra push targets included: selecting
            # ten services in the table has to withdraw exactly what deleting them one by
            # one would.
            errors.extend(f"{public_host}: {e}" for e in withdraw_service_routes(conn, svc, sid))

            # And the logs, which the bulk path never purged: a deleted service left its
            # monitoring history behind, attached to an id nothing could resolve any more.
            conn.execute(
                "DELETE FROM logs WHERE lower(message) GLOB ? OR lower(message) GLOB ?",
                (f"*service {sid}[^0-9]*", f"*service {sid}"),
            )
            conn.execute("DELETE FROM services WHERE id=?", (sid,))
            affected += 1

        conn.commit()
        add_log("info", f"Bulk delete: {plural(affected, 'service')}")

    conn.close()
    return {"ok": True, "affected": affected, "errors": errors}
