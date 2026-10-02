import socket
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.sync import (
    _dns_record_kept,
    _host_serves,
    push_extra_targets,
    withdraw_extra_targets,
    withdraw_service_routes,
)
from app.auth import require_auth
from app.models import (
    add_log,
    get_db,
    labels_by_service,
    row_to_service,
    set_environments,
    set_push_targets,
    set_tags,
)
from app.providers.base import supports_suspension
from app.providers.factory import PROVIDER_TYPES, create_provider, host_id_is_hostname
from app.public_target import (
    describe_public_target_failure,
    resolve_public_target,
    suggest_public_targets,
)
from app.security import redact_query_secrets
from app.text import plural, verb
from app.validators import (
    DOMAIN_REASONS,
    FQDN_REASONS,
    NO_PORT,
    SUBDOMAIN_REASONS,
    domain_problem,
    fqdn_problem,
    is_valid_hostname,
    is_valid_service_port,
    normalize_domain,
    subdomain_problem,
)

router = APIRouter()


def _sync_npm_statuses(conn) -> None:
    """Copy each NPM proxy host's enabled flag onto its service.

    Commits after each service: the loop makes HTTP calls between writes, and holding the
    SQLite write lock across them causes "database is locked" elsewhere.
    """
    try:
        services = conn.execute("""
            SELECT s.id, s.npm_host_id, s.proxy_provider_id, s.enabled
            FROM services s
            WHERE s.npm_host_id IS NOT NULL
              AND s.proxy_provider_id IS NOT NULL
              AND s.expose_mode = 'proxy_dns'
        """).fetchall()

        # One listing per proxy, not one per service.
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

                npm_host = next(
                    (h for h in hosts if h.get("id") == svc["npm_host_id"]),
                    None
                )
                if npm_host and "enabled" in npm_host:
                    npm_enabled = bool(npm_host["enabled"])
                    vauxtra_enabled = bool(svc["enabled"])

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
                # Keep going with the other services. Lock contention is expected under
                # concurrent writes and is not worth a warning.
                msg = str(e).lower()
                if "database is locked" not in msg:
                    add_log("warn", f"Failed to sync NPM status for service {svc['id']}: {e}", conn)
    except Exception:
        # A sync failure must not break the service list.
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
    """Return (proxy_id, dns_id) as the row will store them for the body's expose_mode.

    An extra target equal to a primary is dropped. Shared by preflight and the write
    routes so both de-duplicate against the same ids.
    """
    if body.expose_mode == "tunnel":
        return body.tunnel_provider_id, None
    return body.proxy_provider_id, body.dns_provider_id


# Preflight checks return `detail` (English, for logs) and `detail_key` (i18n code).
# `blocking: True` must mean the save routes would refuse the same body; anything they
# accept is a warning. See tests/test_preflight_symmetry.py for the per-branch list.
def _public_target_refusal(conn, source: str, dns_provider_id: int | None) -> dict:
    """Body of the 400 raised when a DNS provider has no target to write, naming that provider."""
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


def _dns_resolved_detail(dns_provider_row, target, source) -> dict:
    """Describe the resolved DNS target as public or LAN, like the form labels it.

    Uses the provider's `public_dns` capability, as the form does. A missing provider row
    gets a neutral sentence.
    """
    if dns_provider_row is None:
        return _detail(
            "dns_resolved",
            f"Resolved DNS target: {target} ({source})",
            target=str(target),
            source=str(source),
        )
    meta = PROVIDER_TYPES.get(dns_provider_row["type"], {})
    public = bool(meta.get("capabilities", {}).get("public_dns"))
    if public:
        return _detail(
            "dns_resolved_public",
            f"Resolved public DNS target: {target} ({source})",
            target=str(target),
            source=str(source),
        )
    return _detail(
        "dns_resolved_local",
        f"Resolved LAN DNS target: {target} ({source})",
        target=str(target),
        source=str(source),
    )


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
        # A warning: add_service pushes to a disabled primary without reading `enabled`.
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

    # Resolve from the same starting point as the save route: nothing for a create, the
    # stored dns_ip for an edit (service_id set).
    current_dns_ip = ""
    if service_id:
        current_row = conn.execute("SELECT dns_ip FROM services WHERE id=?", (int(service_id),)).fetchone()
        current_dns_ip = (current_row["dns_ip"] or "") if current_row else ""

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
                _detail("host_free", "No other service uses this address")
                if conflicting_id is None
                else _detail(
                    "host_taken",
                    f"This address is already used by service #{conflicting_id}",
                    id=conflicting_id,
                )
            ),
        }
    )

    # A warning only: the save routes never probe the target, and the proxy may reach a
    # network Vauxtra cannot. Timed here so the number survives translation.
    # A DNS-only service has no port, so there is nothing to probe.
    if body.target_port == NO_PORT:
        checks.append(
            {
                "name": "target_reachable",
                "ok": True,
                "blocking": False,
                **_detail(
                    "target_no_port",
                    "No port: the service is only published in DNS, so there is nothing to reach",
                ),
            }
        )
    else:
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
            # Warnings only: add_service publishes the ingress rule without checking tunnel health.
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
                # Some integrations authenticate in the query string, and errors quote the URL.
                error = redact_query_secrets(str(e))
                checks.append(
                    {
                        "name": "tunnel_health",
                        "ok": False,
                        "blocking": False,
                        **_detail(
                            "tunnel_check_failed",
                            f"Tunnel health check failed: {error}",
                            error=error,
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
            # `dns_provider` above only reads the row; this actually contacts the server.
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
                        _dns_resolved_detail(dns_provider_row, resolved_target, target_source)
                        if resolved_target
                        else _detail(*describe_public_target_failure(target_source))
                    ),
                    "data": {"resolved_target": resolved_target, "source": target_source},
                }
            )

    # Extra (multi-sync) targets are pushed too, so they are checked too. Outside the mode
    # branch because a tunnel service can carry extra proxies.
    primary_proxy_id, primary_dns_id = _primary_push_targets(body)
    for role, extra_ids, primary_id in (
        ("proxy", body.extra_proxy_provider_ids, primary_proxy_id),
        ("dns", body.extra_dns_provider_ids, primary_dns_id),
    ):
        for pid in dict.fromkeys(int(p) for p in (extra_ids or []) if p):
            if pid == primary_id:
                continue
            name = f"extra_{role}_provider"
            row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
            if not row:
                # Blocking: `_unknown_references` makes the save route answer 400.
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
                # The push skips a disabled extra, so this is a warning.
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
    # Unknown keys are a 422: a client PUTting back a GET body sends `tags`, not `tag_ids`,
    # and silently applying the empty default would wipe the service's labels.
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
        if not is_valid_service_port(v):
            raise ValueError("Invalid port (1–65535, or 0 for a service published in DNS only)")
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
    def validate_port_when_forwarded(self):
        # NO_PORT is only valid for a DNS-only service; a proxy or tunnel needs a real port.
        forwarded = (
            self.expose_mode == "tunnel"
            or bool(self.proxy_provider_id)
            or bool(self.extra_proxy_provider_ids)
        )
        if self.target_port == NO_PORT and forwarded:
            raise ValueError(
                "A port is required when a proxy or a tunnel forwards to the service "
                "(0 is only for a service published in DNS alone)"
            )
        return self

    @model_validator(mode="after")
    def validate_hostname_length(self):
        # The length limit applies to subdomain + domain together, so it cannot be a field validator.
        problem = fqdn_problem(self.subdomain, self.domain)
        if problem:
            raise ValueError(f"Invalid hostname: {FQDN_REASONS[problem]}")
        return self


class ServicePreflightIn(ServiceIn):
    service_id: int | None = None


class ServiceLabelsIn(BaseModel):
    """Body of PATCH /api/services/{sid}: labels only, no provider is called.

    A field left out or null is unchanged; a list replaces the stored one. Any other key
    is a 422, so a routing field sent here is refused instead of looking saved.
    """

    model_config = ConfigDict(extra="forbid")

    tag_ids:         list[int] | None = None
    environment_ids: list[int] | None = None
    icon_url:        str | None       = None


@router.get("/api/services/public-target/suggest")
def suggest_public_target(
    request: Request,
    proxy_provider_id: int | None = Query(default=None),
):
    require_auth(request)
    conn = get_db()
    try:
        # Reachable with a `read` key; the cache bounds the outbound lookups.
        return suggest_public_targets(conn, proxy_provider_id=proxy_provider_id, wan_ip_max_age=60.0)
    finally:
        conn.close()


@router.post("/api/services/preflight")
def preflight_service(request: Request, body: ServicePreflightIn):
    # Write scope: the server connects to the host and port in the body, so a read key
    # would otherwise be a port scanner.
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
             tp.name AS tunnel_provider_name, tp.type AS tunnel_type
        FROM services s
        LEFT JOIN providers dp ON s.dns_provider_id  = dp.id
        LEFT JOIN providers pp ON s.proxy_provider_id = pp.id
         LEFT JOIN providers tp ON s.tunnel_provider_id = tp.id
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

    tags_by_service, envs_by_service = labels_by_service(conn, service_ids)
    conn.close()

    out = []
    for r in rows:
        service = row_to_service(
            r, tags_by_service.get(r["id"], []), envs_by_service.get(r["id"], [])
        )
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


def _service_detail(conn, sid: int) -> dict | None:
    """One service as GET /api/services/{sid} returns it, or None. Leaves `conn` open."""
    row = conn.execute(
        """
        SELECT s.*,
               dp.name AS dns_provider_name, dp.type AS dns_type,
               pp.name AS proxy_provider_name, pp.type AS proxy_type,
               tp.name AS tunnel_provider_name, tp.type AS tunnel_type
        FROM services s
        LEFT JOIN providers dp ON s.dns_provider_id = dp.id
        LEFT JOIN providers pp ON s.proxy_provider_id = pp.id
        LEFT JOIN providers tp ON s.tunnel_provider_id = tp.id
        WHERE s.id = ?
        """,
        (sid,),
    ).fetchone()
    if not row:
        return None

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
    tags_for_service, envs_for_service = labels_by_service(conn, [sid])

    service = row_to_service(
        row, tags_for_service.get(sid, []), envs_for_service.get(sid, [])
    )
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


@router.get("/api/services/{sid}")
def get_service(sid: int, request: Request):
    """Return one service with provider metadata, tags, environments and push targets."""
    require_auth(request)
    conn = get_db()
    service = _service_detail(conn, sid)
    conn.close()
    if service is None:
        raise HTTPException(404, "Service not found")
    return service


@router.patch("/api/services/{sid}")
def update_service_labels(sid: int, request: Request, body: ServiceLabelsIn):
    """Change a service's tags, environments or icon without calling any provider.

    Returns the service as GET does. 404 if absent, 400 if the body sets none of the three
    fields or names an unknown id (nothing written), 422 for any other key.
    """
    require_auth(request, scope="write")
    conn = get_db()
    row = conn.execute(
        "SELECT subdomain, domain, expose_mode, tunnel_hostname FROM services WHERE id=?", (sid,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Service not found")

    changed = [
        name
        for name, value in (
            ("tags", body.tag_ids),
            ("environments", body.environment_ids),
            ("icon", body.icon_url),
        )
        if value is not None
    ]
    if not changed:
        conn.close()
        raise HTTPException(400, "Nothing to change -- send tag_ids, environment_ids or icon_url")

    unknown = _unknown_ids(conn, "tags", body.tag_ids or [], "tag") + _unknown_ids(
        conn, "environments", body.environment_ids or [], "environment"
    )
    if unknown:
        conn.close()
        raise HTTPException(400, f"Nothing was changed -- unknown {', '.join(unknown)}")

    if body.tag_ids is not None:
        set_tags(conn, sid, body.tag_ids)
    if body.environment_ids is not None:
        set_environments(conn, sid, body.environment_ids)
    if body.icon_url is not None:
        conn.execute("UPDATE services SET icon_url=? WHERE id=?", (body.icon_url, sid))
    public_host = _service_public_hostname(
        row["expose_mode"] or "proxy_dns", row["tunnel_hostname"] or "", row["subdomain"], row["domain"]
    )
    add_log("info", f"Service labels updated: {public_host} ({', '.join(changed)}), no provider called", conn)
    conn.commit()

    service = _service_detail(conn, sid)
    conn.close()
    return service


def _conflicting_service(conn, public_host: str, exclude_id: int | None = None):
    """Return the service already publishing `public_host`, or None.

    Compares published hostnames, not raw columns: a tunnel service publishes
    tunnel_hostname, a collision the (subdomain, domain) unique index cannot see.
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


def _hostname_taken(conn, public_host: str, exclude_id: int | None = None) -> str:
    """409 message for a hostname another service publishes.

    Used both before any push and when the unique index fires at INSERT. The owner may be
    unknown in the second case, since the index compares columns, not published hostnames.
    """
    clash = _conflicting_service(conn, public_host, exclude_id=exclude_id)
    owner = f"service #{clash['id']}" if clash else "another service"
    return (
        f"{public_host} is already served by {owner}. Two services on one hostname push over "
        "each other -- edit that one, or choose another hostname."
    )


def _unknown_ids(conn, table: str, ids, label: str) -> list[str]:
    """Return `"<label> <id>"` for each id in `ids` with no row in `table`, in id order.

    0 is checked like any id: in a label list it names nothing. Callers filter provider
    ids themselves, where 0 means none.
    """
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    found = {
        r["id"]
        for r in conn.execute(f"SELECT id FROM {table} WHERE id IN ({marks})", tuple(wanted))
    }
    return [f"{label} {i}" for i in wanted if i not in found]


def _unknown_references(conn, body: ServiceIn) -> list[str]:
    """Name every id in the payload that points at nothing.

    Must run before any provider is called: the write routes push first and insert after,
    so a foreign-key failure at INSERT would leave a published route with no row.
    """
    providers = [
        body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id,
        *(body.extra_proxy_provider_ids or []), *(body.extra_dns_provider_ids or []),
    ]
    return (
        _unknown_ids(conn, "tags", body.tag_ids or [], "tag")
        + _unknown_ids(conn, "environments", body.environment_ids or [], "environment")
        # A provider id of 0 or None is the absence of one, not an id.
        + _unknown_ids(conn, "providers", [p for p in providers if p], "provider")
    )


@router.post("/api/services", status_code=201)
def add_service(request: Request, body: ServiceIn):
    """Create a service and publish its proxy host, DNS record or tunnel route.

    201 with an empty `errors` list when everything was published, 207 when the row was
    saved but some pushes failed (`errors` says which). 400 for unknown ids or an
    unresolvable DNS target, 409 when another service already publishes the hostname.
    """
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

    if _conflicting_service(conn, public_host):
        refusal = _hostname_taken(conn, public_host)
        conn.close()
        raise HTTPException(409, refusal)

    # Resolved before any provider is touched, so a refusal needs no compensating delete.
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

    # Read only if the INSERT is refused: then no row exists, and the journal is the only
    # record of what was published.
    published_on: list[str] = []

    npm_host_id = None

    if body.expose_mode == "tunnel":
        row = conn.execute("SELECT * FROM providers WHERE id=?", (body.tunnel_provider_id,)).fetchone()
        if not row:
            errors.append("Tunnel provider not found")
        elif not body.enabled:
            # Created disabled: publish on enable, not now.
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
                    published_on.append(row["name"])
                    add_log("info", f"Tunnel route created: {public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                else:
                    errors.append("Failed to create tunnel route")
                    add_log("error", f"Tunnel route failed: {public_host}")
            except Exception as e:
                errors.append(redact_query_secrets(str(e)))
    else:
        if body.proxy_provider_id and not body.enabled:
            # npm_host_id stays NULL, which the enable path re-deploys from.
            add_log("info", f"Proxy host not created (service created disabled): {public_host}")
        elif body.proxy_provider_id:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (body.proxy_provider_id,)).fetchone()
            if row:
                try:
                    proxy = create_provider(row)
                    cert_id = proxy.find_best_certificate(public_host)
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
                        published_on.append(row["name"])
                        add_log("info", f"Proxy created: {public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                    else:
                        errors.append("Failed to create proxy host")
                        add_log("error", f"Proxy failed: {public_host}")
                except Exception as e:
                    errors.append(redact_query_secrets(str(e)))

        if body.dns_provider_id:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if row and not body.enabled:
                # The target is still stored so the enable path can add the record.
                add_log("info", f"DNS record not added (service created disabled): {public_host}")
            elif row:
                try:
                    dns = create_provider(row)
                    if dns.add_rewrite(public_host, dns_target):
                        published_on.append(row["name"])
                        add_log("info", f"DNS added: {public_host} → {dns_target} ({dns_target_source})")
                    else:
                        errors.append("Failed to create DNS rewrite")
                        add_log("error", f"DNS failed: {public_host}")
                except Exception as e:
                    errors.append(redact_query_secrets(str(e)))
        else:
            dns_target = body.dns_ip

    stored_proxy_provider_id = body.proxy_provider_id if body.expose_mode == "proxy_dns" else None
    stored_dns_provider_id = body.dns_provider_id if body.expose_mode == "proxy_dns" else None
    stored_tunnel_provider_id = body.tunnel_provider_id if body.expose_mode == "tunnel" else None
    stored_public_target_mode = body.public_target_mode if body.expose_mode == "proxy_dns" else "manual"
    stored_auto_update_dns = body.auto_update_dns if body.expose_mode == "proxy_dns" else False
    stored_tunnel_hostname = public_host if body.expose_mode == "tunnel" else ""
    stored_dns_target = dns_target if body.expose_mode == "proxy_dns" else ""

    # A concurrent save can take the hostname after the earlier check; only the unique
    # index sees it. Answer 409 and journal what was published. Not withdrawn: the
    # winning service now owns those records.
    try:
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
    except sqlite3.IntegrityError:
        refusal = _hostname_taken(conn, public_host)
        conn.close()
        # Logged on add_log's own connection: this path never commits `conn`.
        if published_on:
            add_log(
                "warn",
                f"{public_host} was published on {', '.join(published_on)} and then refused: "
                "another service claimed that hostname first. No service row describes those "
                "records, so Vauxtra cannot list or remove them -- check those providers.",
            )
        raise HTTPException(409, refusal)
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

    # Push to the extra targets after committing: these are HTTP calls and must not hold
    # the SQLite write lock.
    errors.extend(push_extra_targets(conn, sid))

    conn.close()

    from fastapi.responses import JSONResponse
    return JSONResponse({"id": sid, "fqdn": public_host, "errors": errors}, status_code=201 if not errors else 207)


def _proxy_host_kept(proxy, host: str) -> bool:
    """Whether `proxy` still holds a host for `host` after refusing to delete it.

    NPM answers False both for a refusal and for an id already gone, so the listing
    decides. A listing that fails counts as kept.
    """
    wanted = (host or "").strip().lower()
    try:
        return any(_host_serves(h, wanted) for h in proxy.list_hosts() or [])
    except Exception:
        return True


def _retire(errors: list[str], row, what: str, host: str, delete, kept=None) -> None:
    """Remove the route an edit moved away from; append to `errors` if it stays.

    `delete(provider)` removes it. `kept(provider)` re-reads the listing after a refusal,
    for providers whose False is ambiguous. Read-only providers are skipped.
    """
    if PROVIDER_TYPES.get(row["type"], {}).get("read_only"):
        return
    try:
        provider = create_provider(row)
        if delete(provider) or (kept is not None and not kept(provider)):
            return
        errors.append(f"Failed to withdraw the previous {what}: {host}")
        add_log("error", f"{what[0].upper()}{what[1:]} still published: {host}")
    except Exception as e:
        reason = redact_query_secrets(str(e))
        errors.append(f"Could not withdraw the previous {what}: {reason}")
        add_log("warn", f"Could not withdraw the previous {what} ({host}): {reason}")


@router.put("/api/services/{sid}")
def update_service(sid: int, request: Request, body: ServiceIn):
    """Replace a service's configuration and move its routes to match.

    Publishes the new routes, withdraws the ones the edit moved away from and applies the
    enabled flag. Answers the service plus `errors` (provider failures; the row is saved
    regardless). 400 for no target, unknown ids or an unresolvable DNS target, 404, 409.
    """
    require_auth(request, scope="write")
    conn = get_db()
    old  = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not old:
        conn.close()
        raise HTTPException(404, "Service not found")

    # Same refusal as add_service, after the 404 so a missing service says so first.
    if body.expose_mode == "proxy_dns":
        has_any_proxy_target = bool(body.proxy_provider_id) or bool(body.extra_proxy_provider_ids)
        has_any_dns_target = bool(body.dns_provider_id) or bool(body.extra_dns_provider_ids)
        if not (has_any_proxy_target or has_any_dns_target):
            conn.close()
            raise HTTPException(400, "At least one proxy or DNS provider target is required")

    unknown = _unknown_references(conn, body)
    if unknown:
        conn.close()
        raise HTTPException(400, f"Nothing was changed -- unknown {', '.join(unknown)}")

    wanted_host = _service_public_hostname(body.expose_mode, body.tunnel_hostname, body.subdomain, body.domain)
    if _conflicting_service(conn, wanted_host, exclude_id=sid):
        refusal = _hostname_taken(conn, wanted_host, exclude_id=sid)
        conn.close()
        raise HTTPException(409, refusal)

    old_mode = (old["expose_mode"] or "proxy_dns").strip().lower()
    new_mode = body.expose_mode
    new_public_host = _service_public_hostname(new_mode, body.tunnel_hostname, body.subdomain, body.domain)
    old_public_host = _service_public_hostname(old_mode, old["tunnel_hostname"] or "", old["subdomain"], old["domain"])

    # Same rule as add_service: a DNS provider with no target is refused. No fallback to
    # the old dns_ip here; resolve_public_target already offers it as `current`.
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
                    # Providers keyed on the hostname are asked by name, not npm_host_id.
                    _retire(
                        errors, old_proxy_row, "proxy host", old_public_host,
                        lambda p: p.delete_host(
                            old_public_host
                            if host_id_is_hostname(p, old_proxy_row["type"])
                            else old["npm_host_id"]
                        ),
                        lambda p: _proxy_host_kept(p, old_public_host),
                    )
            if old["dns_provider_id"] and old["dns_ip"]:
                old_dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["dns_provider_id"],)).fetchone()
                if old_dns_row:
                    _retire(
                        errors, old_dns_row, "DNS record", old_public_host,
                        lambda p: p.delete_rewrite(old_public_host, old["dns_ip"]),
                        lambda p: _dns_record_kept(p, old_public_host, old["dns_ip"]),
                    )

        tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.tunnel_provider_id,)).fetchone()
        if not tunnel_row:
            errors.append("Tunnel provider not found")
        elif not body.enabled:
            # Disabled: withdraw the ingress rule on every PUT, not only on the transition.
            # Cloudflare Tunnel signals a refusal with False, not an exception.
            try:
                if create_provider(tunnel_row).delete_host(new_public_host):
                    add_log(
                        "info", f"Tunnel route withdrawn (service disabled): {new_public_host}"
                    )
                else:
                    errors.append("Failed to withdraw the tunnel route on disable")
                    add_log("error", f"Tunnel route still published: {new_public_host}")
            except Exception as e:
                errors.append(redact_query_secrets(str(e)))
            if old_mode == "tunnel" and old["tunnel_provider_id"] and (
                old["tunnel_provider_id"] != body.tunnel_provider_id
                or old_public_host != new_public_host
            ):
                # The hostname or tunnel also changed, so the previous route lives elsewhere.
                old_tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["tunnel_provider_id"],)).fetchone()
                if old_tunnel_row:
                    _retire(
                        errors, old_tunnel_row, "tunnel route", old_public_host,
                        lambda p: p.delete_host(old_public_host),
                    )
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
                            _retire(
                                errors, old_tunnel_row, "tunnel route", old_public_host,
                                lambda p: p.delete_host(old_public_host),
                            )

                if ok:
                    add_log("info", f"Tunnel updated: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                else:
                    errors.append("Failed to update tunnel route")
                    add_log("error", f"Tunnel update failed: {new_public_host}")
            except Exception as e:
                errors.append(redact_query_secrets(str(e)))

    else:
        if old_mode == "tunnel" and old["tunnel_provider_id"]:
            old_tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["tunnel_provider_id"],)).fetchone()
            if old_tunnel_row:
                _retire(
                    errors, old_tunnel_row, "tunnel route", old_public_host,
                    lambda p: p.delete_host(old_public_host),
                )

        # Nothing to do on the proxy for a service that stays disabled with no host; the
        # enable path re-deploys it.
        _staying_disabled = not bool(body.enabled) and not bool(old["enabled"])
        _proxy_already_removed = old["npm_host_id"] is None

        if body.proxy_provider_id and not (_staying_disabled and _proxy_already_removed):
            proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.proxy_provider_id,)).fetchone()
            if not proxy_row:
                errors.append("Proxy provider not found")
            else:
                # Count only this block's failures for the success line.
                failed_before = len(errors)
                try:
                    proxy = create_provider(proxy_row)
                    cert_id = proxy.find_best_certificate(new_public_host)

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
                            # Providers keyed on the hostname renamed the rule, so the id changes.
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
                        # The host on the former proxy is withdrawn below with the other
                        # former targets, not here (a second delete would 404 on NPM).
                        if created:
                            next_npm_host_id = created.get("id")
                        else:
                            errors.append("Failed to create proxy host")

                    if len(errors) == failed_before:
                        add_log("info", f"Proxy updated: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                except Exception as e:
                    errors.append(redact_query_secrets(str(e)))

        if body.dns_provider_id and dns_ip and not body.enabled:
            # Disabled: withdraw on every PUT, not only on the transition, or editing an
            # already disabled service would publish its record again.
            dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if dns_row:
                try:
                    dns = create_provider(dns_row)
                    old_dns_ip = old["dns_ip"] or ""
                    # Remove both the previous and the new record, in case either changed.
                    targets = {(new_public_host, dns_ip)}
                    if old_mode == "proxy_dns" and old["dns_provider_id"] == body.dns_provider_id and old_dns_ip:
                        targets.add((old_public_host, old_dns_ip))
                    # A refusal is checked against the listing before it is reported.
                    withheld = True
                    for record_host, record_ip in sorted(targets):
                        if dns.delete_rewrite(record_host, record_ip):
                            continue
                        if _dns_record_kept(dns, record_host, record_ip):
                            withheld = False
                            errors.append(
                                f"Failed to withdraw the DNS record on disable: {record_host}"
                            )
                            add_log("error", f"DNS record still published: {record_host}")
                    if withheld:
                        add_log(
                            "info", f"DNS record withheld (service disabled): {new_public_host}"
                        )
                except Exception as e:
                    add_log("warn", f"Could not withdraw the DNS record of a disabled service: {e}")
                    reason = redact_query_secrets(str(e))
                    errors.append(f"Could not withdraw the DNS record on disable: {reason}")
        elif body.dns_provider_id and dns_ip:
            dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if dns_row:
                try:
                    dns = create_provider(dns_row)
                    old_dns_ip = old["dns_ip"] or ""
                    same_provider = old_mode == "proxy_dns" and old["dns_provider_id"] == body.dns_provider_id
                    # A previously disabled service has no record to move: add it.
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
                            # Only when it differs, or this would delete what was just added.
                            if old_mode == "proxy_dns" and old["dns_provider_id"] and old_dns_ip and moved:
                                old_dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["dns_provider_id"],)).fetchone()
                                if old_dns_row:
                                    _retire(
                                        errors, old_dns_row, "DNS record", old_public_host,
                                        lambda p: p.delete_rewrite(old_public_host, old_dns_ip),
                                        lambda p: _dns_record_kept(p, old_public_host, old_dns_ip),
                                    )
                            add_log("info", f"DNS updated: {new_public_host} → {dns_ip} ({dns_target_source})")
                        else:
                            errors.append("Failed to update DNS rewrite")
                except Exception as e:
                    errors.append(redact_query_secrets(str(e)))

    stored_proxy_provider_id = body.proxy_provider_id if new_mode == "proxy_dns" else None
    stored_dns_provider_id = body.dns_provider_id if new_mode == "proxy_dns" else None
    stored_tunnel_provider_id = body.tunnel_provider_id if new_mode == "tunnel" else None
    stored_public_target_mode = body.public_target_mode if new_mode == "proxy_dns" else "manual"
    stored_auto_update_dns = body.auto_update_dns if new_mode == "proxy_dns" else False
    stored_tunnel_hostname = new_public_host if new_mode == "tunnel" else ""
    stored_dns_ip = dns_ip if new_mode == "proxy_dns" else ""

    # A concurrent save can take the hostname after the earlier check, once the providers
    # are already reconfigured. Answer 409 and journal the divergence.
    try:
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
        # A DNS-only service is never probed, so clear any stale status.
        if body.target_port == NO_PORT:
            conn.execute("UPDATE services SET status='unknown' WHERE id=?", (sid,))
    except sqlite3.IntegrityError:
        refusal = _hostname_taken(conn, new_public_host, exclude_id=sid)
        conn.close()
        add_log(
            "warn",
            f"Service #{sid} was reconfigured for {new_public_host} on its providers and then "
            f"refused: another service claimed that hostname first. The service still records "
            f"{old_public_host}, which its providers no longer serve -- run a drift check.",
        )
        raise HTTPException(409, refusal)

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

    # Withdraw, using the old row, every extra target dropped from the list, and all of
    # them on a rename. Primaries were already moved above.
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

    # A primary cleared from its column is withdrawn too, unless it moved into the extras.
    # Tunnel mode already withdrew the old proxy host and DNS record in its own branch.
    if new_mode == "proxy_dns":
        kept = still_targeted | primaries
        stale_targets |= {
            pid
            for pid in (old["proxy_provider_id"], old["dns_provider_id"])
            if pid and pid not in kept
        }
    if stale_targets:
        for message in withdraw_service_routes(conn, old, sid, only_provider_ids=stale_targets):
            add_log("warn", f"Could not withdraw {old_public_host} from a former target: {message}", conn)
            # Reported in the answer too: set_push_targets unlinks the target below, so
            # nothing in Vauxtra can reach that provider again.
            errors.append(f"Former target: {message}")

    set_push_targets(conn, sid, extra_proxy_ids, extra_dns_ids)

    set_tags(conn, sid, body.tag_ids)
    set_environments(conn, sid, body.environment_ids)

    # Commit before the provider calls below so a hung provider does not hold SQLite's
    # write lock. Row and providers were never atomic anyway.
    conn.commit()

    # Enable/disable transition on the primary proxy. Tunnel mode and DNS are handled
    # above on every PUT, not only on a transition.
    if bool(old["enabled"]) != bool(body.enabled) and new_mode == "proxy_dns":
        enable = bool(body.enabled)

        if body.proxy_provider_id:
            try:
                proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.proxy_provider_id,)).fetchone()
                if proxy_row:
                    proxy = create_provider(proxy_row)
                    if enable:
                        if next_npm_host_id:
                            # The host was suspended on disable: resume it.
                            if proxy.toggle_host(next_npm_host_id, True):
                                add_log("info", f"Proxy enabled: {new_public_host}", conn)
                            elif not supports_suspension(proxy):
                                add_log("info", f"Proxy active (this provider has no suspension, host already present): {new_public_host}", conn)
                            else:
                                # A provider that supports the toggle returned False: it failed.
                                errors.append("Failed to resume the proxy host on enable")
                                add_log("error", f"Proxy still suspended: {new_public_host}", conn)
                        else:
                            # The host was deleted on disable: re-create it.
                            cert_id = proxy.find_best_certificate(new_public_host)
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
                        # Disable: suspend where supported, delete otherwise.
                        if next_npm_host_id:
                            if proxy.toggle_host(next_npm_host_id, False):
                                add_log("info", f"Proxy suspended: {new_public_host}", conn)
                            elif not supports_suspension(proxy):
                                # Clear npm_host_id so re-enable re-creates the host.
                                if proxy.delete_host(next_npm_host_id):
                                    conn.execute("UPDATE services SET npm_host_id=NULL WHERE id=?", (sid,))
                                    add_log("info", f"Proxy removed (suspend not supported, config kept in Vauxtra): {new_public_host}", conn)
                                else:
                                    errors.append("Failed to remove the proxy host on disable")
                            else:
                                # A failed suspend is reported, never turned into a delete:
                                # that would lose the host's custom config.
                                errors.append("Failed to suspend the proxy host on disable")
                                add_log("error", f"Proxy still serving: {new_public_host}", conn)
            except Exception as e:
                reason = redact_query_secrets(str(e))
                add_log("warn", f"Could not manage proxy enabled state: {reason}", conn)
                errors.append(f"Could not change the proxy host's state: {reason}")

    conn.commit()
    add_log("info", f"Service updated: {new_public_host}")

    # Extra targets follow the enabled flag; only one of these two calls does anything.
    errors.extend(push_extra_targets(conn, sid))
    errors.extend(withdraw_extra_targets(conn, sid))
    # Commits the journal lines the withdrawal wrote on this connection.
    conn.commit()

    service = _service_detail(conn, sid)
    conn.close()

    return {**service, "errors": errors}


def _webhooks_scoped_to(conn, sids: list[int]) -> list[dict]:
    """Webhooks scoped to one of `sids`.

    scope_ref_id has no foreign key, so deleting the service leaves these webhooks
    matching nothing; callers log them.
    """
    if not sids:
        return []
    marks = ",".join("?" for _ in sids)
    rows = conn.execute(
        f"""
        SELECT id, name, enabled
          FROM webhooks
         WHERE scope_type = 'service' AND scope_ref_id IN ({marks})
         ORDER BY name
        """,  # noqa: S608 -- `marks` is a run of literal `?`, one per id; the ids are bound
        sids,
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"])} for r in rows]


def _log_orphaned_webhooks(hooks: list[dict], what: str, conn=None) -> None:
    """Log one warning naming the webhooks a deletion left without a target."""
    if not hooks:
        return
    names = ", ".join(h["name"] for h in hooks[:5])
    if len(hooks) > 5:
        names += f", and {len(hooks) - 5} more"
    add_log(
        "warn",
        f"{what}: {plural(len(hooks), 'notification webhook')} "
        f"{verb(len(hooks), 'was', 'were')} scoped to it and now "
        f"{verb(len(hooks), 'matches', 'match')} nothing ({names})",
        conn,
    )


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

    # Every provider that may hold a route, extra push targets included.
    errors = withdraw_service_routes(conn, svc, sid)

    # Read before the row goes.
    orphaned_hooks = _webhooks_scoped_to(conn, [sid])

    # GLOB with a digit boundary so service 1 does not match service 12. The second
    # pattern is the id at the end of the message.
    conn.execute(
        "DELETE FROM logs WHERE lower(message) GLOB ? OR lower(message) GLOB ?",
        (f"*service {sid}[^0-9]*", f"*service {sid}"),
    )
    conn.execute("DELETE FROM services WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    add_log("info", f"Service deleted: {public_host}")
    _log_orphaned_webhooks(orphaned_hooks, f"Service deleted: {public_host}")
    # `ok` stays true even with errors: the row is gone, and a retry could only 404.
    return {"ok": True, "errors": errors}


def _check_one(sid: int) -> dict:
    """Probe one service, record status and an uptime event like the scheduler, return the result."""
    conn = get_db()
    svc  = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(404, "Service not found")

    status     = "unknown"
    latency_ms = None
    # A DNS-only service has no port to probe; only its name is resolved.
    tested     = int(svc["target_port"] or 0) != NO_PORT
    start      = time.monotonic()
    if tested:
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

    if tested:
        conn.execute(
            "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
            (status, sid),
        )
        conn.execute(
            "INSERT INTO uptime_events (service_id, status) VALUES (?,?)",
            (sid, status),
        )
    else:
        conn.execute("UPDATE services SET status='unknown' WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    if tested:
        add_log("info" if status == "ok" else "error", f"Check {public_host}: {status}")
    return {
        "id": sid,
        "status": status,
        "latency_ms": latency_ms,
        "dns_resolved": dns_resolved,
        "tested": tested,
    }


@router.post("/api/services/{sid}/check")
def check_service(sid: int, request: Request):
    # Write scope: this updates status, uptime history and the log.
    require_auth(request, scope="write")
    return _check_one(sid)


@router.get("/api/services/{sid}/check", deprecated=True)
def check_service_get(sid: int, request: Request):
    """Deprecated alias of POST /api/services/{sid}/check, same write scope. Use the POST."""
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
    conn.close()
    ok_count = error_count = 0
    results: list[dict] = []

    skipped_tunnel = skipped_no_port = 0
    for svc in services:
        # Tunnels and DNS-only services are not probed; counted apart so the UI can say why.
        if (svc["expose_mode"] or "").strip().lower() == "tunnel":
            skipped_tunnel += 1
            continue
        if not svc["target_port"]:
            skipped_no_port += 1
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
        results.append({"id": svc["id"], "status": status, "latency_ms": latency_ms})

    # Written after all probes so the write lock is not held across them.
    conn = get_db()
    try:
        for result in results:
            conn.execute(
                "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
                (result["status"], result["id"]),
            )
            # Skipped for a service deleted while the probes ran.
            conn.execute(
                "INSERT INTO uptime_events (service_id, status) "
                "SELECT ?, ? WHERE EXISTS (SELECT 1 FROM services WHERE id=?)",
                (result["id"], result["status"], result["id"]),
            )
        conn.commit()
    finally:
        conn.close()
    # One log line for the whole run.
    add_log(
        "info" if error_count == 0 else "error",
        f"Manual check of {plural(len(results), 'service')}: {ok_count} ok, {error_count} error",
    )
    return {
        "checked":         len(services),
        "ok":              ok_count,
        "error":           error_count,
        "skipped":         skipped_tunnel + skipped_no_port,
        "skipped_tunnel":  skipped_tunnel,
        "skipped_no_port": skipped_no_port,
        "results":         results,
    }


class _BulkActionBody(BaseModel):
    # Bounded: each id costs up to three provider calls, and SQLite caps bound parameters.
    ids:    list[int] = Field(max_length=500)
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

        services = conn.execute(
            f"SELECT * FROM services WHERE id IN ({placeholders})",
            body.ids,
        ).fetchall()

        conn.execute(
            f"UPDATE services SET enabled=? WHERE id IN ({placeholders})",
            [enabled_val, *body.ids],
        )
        # Commit before the provider calls so they never run under the write lock.
        conn.commit()

        for svc in services:
            # The `continue` branches skip the commit at the end of the loop body.
            conn.commit()
            sid_b = svc["id"]
            mode = (svc["expose_mode"] or "proxy_dns").strip().lower()
            pub = _service_public_hostname(
                mode, svc["tunnel_hostname"] or "", svc["subdomain"], svc["domain"]
            )

            # Extra targets follow the flag; before the mode branches so `continue` cannot skip it.
            for message in (push_extra_targets(conn, sid_b) if enable
                            else withdraw_extra_targets(conn, sid_b)):
                errors.append(f"{pub}: {message}")

            if mode == "tunnel":
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
                        errors.append(f"Service {sid_b}: tunnel state error — {redact_query_secrets(str(e))}")
                continue

            if mode != "proxy_dns":
                continue

            if svc["proxy_provider_id"]:
                try:
                    proxy_row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["proxy_provider_id"],)).fetchone()
                    if proxy_row:
                        proxy = create_provider(proxy_row)
                        if enable:
                            if svc["npm_host_id"]:
                                if proxy.toggle_host(svc["npm_host_id"], True):
                                    add_log("info", f"Proxy enabled: {pub}", conn)
                                elif not supports_suspension(proxy):
                                    add_log("info", f"Proxy active (this provider has no suspension): {pub}", conn)
                                else:
                                    errors.append(f"Service {sid_b}: failed to resume the proxy host")
                                    add_log("error", f"Proxy still suspended: {pub}", conn)
                            else:
                                # Deleted on disable: re-create it.
                                cert_id = proxy.find_best_certificate(pub)
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
                                elif not supports_suspension(proxy):
                                    if proxy.delete_host(svc["npm_host_id"]):
                                        conn.execute("UPDATE services SET npm_host_id=NULL WHERE id=?", (sid_b,))
                                        add_log("info", f"Proxy removed (suspend not supported): {pub}", conn)
                                    else:
                                        errors.append(f"Service {sid_b}: failed to remove the proxy host")
                                else:
                                    # A failed suspend is reported, never turned into a delete.
                                    errors.append(f"Service {sid_b}: failed to suspend the proxy host")
                                    add_log("error", f"Proxy still serving: {pub}", conn)
                except Exception as e:
                    errors.append(f"Service {sid_b}: proxy state error — {redact_query_secrets(str(e))}")

            if svc["dns_provider_id"] and svc["dns_ip"]:
                try:
                    dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["dns_provider_id"],)).fetchone()
                    if dns_row:
                        dns = create_provider(dns_row)
                        if enable:
                            if dns.add_rewrite(pub, svc["dns_ip"]):
                                add_log("info", f"DNS re-added on enable: {pub} → {svc['dns_ip']}", conn)
                            else:
                                errors.append(
                                    f"Service {sid_b}: failed to re-publish the DNS record"
                                )
                                add_log("error", f"DNS record not published: {pub}", conn)
                        else:
                            # A refusal is checked against the listing before it is reported.
                            if dns.delete_rewrite(pub, svc["dns_ip"]) or not _dns_record_kept(
                                dns, pub, svc["dns_ip"]
                            ):
                                add_log("info", f"DNS removed on disable: {pub}", conn)
                            else:
                                errors.append(
                                    f"Service {sid_b}: failed to withdraw the DNS record"
                                )
                                add_log("error", f"DNS record still published: {pub}", conn)
                except Exception as e:
                    errors.append(f"Service {sid_b}: DNS state error — {redact_query_secrets(str(e))}")

            # Per service: add_log(..., conn) re-takes the write lock, which must be released
            # before the next service's provider calls.
            conn.commit()

        affected = conn.execute(
            f"SELECT COUNT(*) FROM services WHERE id IN ({placeholders})",
            body.ids,
        ).fetchone()[0]
        conn.commit()
        add_log("info", f"Bulk {body.action}: {plural(affected, 'service')}")

    elif body.action == "delete":
        orphaned_hooks: list[dict] = []
        for sid in body.ids:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
            if not svc:
                errors.append(f"Service {sid} not found")
                continue

            mode = (svc["expose_mode"] or "proxy_dns").strip().lower()
            public_host = _service_public_hostname(
                mode, svc["tunnel_hostname"] or "", svc["subdomain"], svc["domain"]
            )

            # Same steps as the single delete.
            errors.extend(f"{public_host}: {e}" for e in withdraw_service_routes(conn, svc, sid))
            orphaned_hooks.extend(_webhooks_scoped_to(conn, [sid]))
            conn.execute(
                "DELETE FROM logs WHERE lower(message) GLOB ? OR lower(message) GLOB ?",
                (f"*service {sid}[^0-9]*", f"*service {sid}"),
            )
            conn.execute("DELETE FROM services WHERE id=?", (sid,))
            affected += 1
            # Before the next service's provider calls, which must not hold the writer lock.
            conn.commit()

        conn.commit()
        add_log("info", f"Bulk delete: {plural(affected, 'service')}")
        # One warning for the batch.
        _log_orphaned_webhooks(orphaned_hooks, f"Bulk delete: {plural(affected, 'service')}")

    conn.close()
    return {"ok": True, "affected": affected, "errors": errors}
