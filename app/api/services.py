import socket
import time
from fastapi import APIRouter, Query, Request, HTTPException
from pydantic import BaseModel, model_validator, field_validator
from app.models import (
    get_db,
    add_log,
    row_to_service,
    set_tags,
    set_environments,
    set_push_targets,
)
from app.providers.factory import create_provider
from app.auth import require_auth
from app.public_target import resolve_public_target, suggest_public_targets
from app.validators import is_valid_subdomain, is_valid_hostname, is_valid_port

router = APIRouter()


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


def _check_provider(conn, provider_id: int | None, *, role: str, required: bool) -> tuple[dict | None, dict]:
    if not provider_id:
        if required:
            return None, {
                "name": f"{role}_provider",
                "ok": False,
                "blocking": True,
                "detail": f"{role.capitalize()} provider is required",
            }
        return None, {
            "name": f"{role}_provider",
            "ok": True,
            "blocking": False,
            "detail": f"{role.capitalize()} provider not set (optional)",
        }

    row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return None, {
            "name": f"{role}_provider",
            "ok": False,
            "blocking": True,
            "detail": f"{role.capitalize()} provider not found",
        }
    if not row["enabled"]:
        return dict(row), {
            "name": f"{role}_provider",
            "ok": False,
            "blocking": True,
            "detail": f"{role.capitalize()} provider is disabled",
        }

    return dict(row), {
        "name": f"{role}_provider",
        "ok": True,
        "blocking": False,
        "detail": f"{role.capitalize()} provider ready: {row['name']}",
    }


def _run_preflight(conn, body, service_id: int | None = None) -> dict:
    checks: list[dict] = []

    public_host = _service_public_hostname(body.expose_mode, body.tunnel_hostname, body.subdomain, body.domain)

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
            "detail": "No existing route conflict" if conflicting_id is None else f"Route already exists on service #{conflicting_id}",
        }
    )

    # Target reachability
    reachable, detail = _service_target_reachable(body.target_ip, body.target_port)
    checks.append(
        {
            "name": "target_reachable",
            "ok": reachable,
            "blocking": True,
            "detail": detail if reachable else f"Target not reachable: {detail}",
        }
    )

    if body.forward_scheme == "https" and int(body.target_port) == 80:
        checks.append(
            {
                "name": "https_port_hint",
                "ok": False,
                "blocking": False,
                "detail": "Forward scheme is HTTPS but port is 80. Verify backend TLS termination.",
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
            try:
                provider = create_provider(tunnel_provider_row)
                if hasattr(provider, "health_status"):
                    health = provider.health_status()
                    checks.append(
                        {
                            "name": "tunnel_health",
                            "ok": bool(health.get("ok")),
                            "blocking": True,
                            "detail": f"Tunnel status: {health.get('status', 'unknown')}",
                            "data": health,
                        }
                    )
                else:
                    ok = bool(provider.test_connection())
                    checks.append(
                        {
                            "name": "tunnel_health",
                            "ok": ok,
                            "blocking": True,
                            "detail": "Tunnel provider reachable" if ok else "Tunnel provider not reachable",
                        }
                    )
            except Exception as e:
                checks.append(
                    {
                        "name": "tunnel_health",
                        "ok": False,
                        "blocking": True,
                        "detail": f"Tunnel health check failed: {e}",
                    }
                )

    else:
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
                    "detail": "Proxy provider connection is healthy" if ok else "Proxy provider connection test failed",
                }
            )

        if body.dns_provider_id:
            resolved_target, target_source = resolve_public_target(
                conn,
                mode=body.public_target_mode,
                manual_value=body.dns_ip,
                proxy_provider_id=body.proxy_provider_id,
                current_value="",
            )
            checks.append(
                {
                    "name": "dns_target_resolution",
                    "ok": bool(resolved_target),
                    "blocking": True,
                    "detail": (
                        f"Resolved public DNS target: {resolved_target} ({target_source})"
                        if resolved_target
                        else "Unable to resolve DNS public target"
                    ),
                    "data": {"resolved_target": resolved_target, "source": target_source},
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
        if not is_valid_subdomain(v):
            raise ValueError("Invalid subdomain")
        return v

    @field_validator("domain")
    @classmethod
    def val_domain(cls, v):
        return v.strip().lower()

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
        if self.expose_mode == "tunnel":
            if not self.tunnel_provider_id:
                raise ValueError("Tunnel provider is required in tunnel mode")
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
    require_auth(request)
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


@router.post("/api/services", status_code=201)
def add_service(request: Request, body: ServiceIn):
    require_auth(request, scope="write")
    public_host = _service_public_hostname(body.expose_mode, body.tunnel_hostname, body.subdomain, body.domain)
    conn   = get_db()
    errors = []

    npm_host_id = None
    dns_target = ""

    if body.expose_mode == "tunnel":
        row = conn.execute("SELECT * FROM providers WHERE id=?", (body.tunnel_provider_id,)).fetchone()
        if not row:
            errors.append("Tunnel provider not found")
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
                    add_log("ok", f"Tunnel route created: {public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                else:
                    errors.append("Failed to create tunnel route")
                    add_log("error", f"Tunnel route failed: {public_host}")
            except Exception as e:
                errors.append(str(e))
    else:
        if body.proxy_provider_id:
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
                        add_log("ok", f"Proxy created: {public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                    else:
                        errors.append("Failed to create proxy host")
                        add_log("error", f"Proxy failed: {public_host}")
                except Exception as e:
                    errors.append(str(e))

        if body.dns_provider_id:
            dns_target, dns_target_source = resolve_public_target(
                conn,
                mode=body.public_target_mode,
                manual_value=body.dns_ip,
                proxy_provider_id=body.proxy_provider_id,
                current_value="",
            )
            if not dns_target:
                conn.close()
                raise HTTPException(400, "Unable to resolve DNS public target")

            row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if row:
                try:
                    dns = create_provider(row)
                    if dns.add_rewrite(public_host, dns_target):
                        add_log("ok", f"DNS added: {public_host} → {dns_target} ({dns_target_source})")
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

    primary_proxy_provider_id = stored_proxy_provider_id or stored_tunnel_provider_id
    extra_proxy_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_proxy_provider_ids)
        if pid and pid != primary_proxy_provider_id
    ]
    extra_dns_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_dns_provider_ids)
        if pid and pid != stored_dns_provider_id
    ]

    set_push_targets(conn, sid, extra_proxy_ids, extra_dns_ids)

    if body.tag_ids:
        set_tags(conn, sid, body.tag_ids)
    if body.environment_ids:
        set_environments(conn, sid, body.environment_ids)
    conn.commit()
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

    old_mode = (old["expose_mode"] or "proxy_dns").strip().lower()
    new_mode = body.expose_mode
    new_public_host = _service_public_hostname(new_mode, body.tunnel_hostname, body.subdomain, body.domain)
    old_public_host = _service_public_hostname(old_mode, old["tunnel_hostname"] or "", old["subdomain"], old["domain"])
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
                    add_log("ok", f"Tunnel updated: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                else:
                    errors.append("Failed to update tunnel route")
                    add_log("error", f"Tunnel update failed: {new_public_host}")
            except Exception as e:
                errors.append(str(e))

        dns_ip = ""
        dns_target_source = "n/a"
    else:
        if old_mode == "tunnel" and old["tunnel_provider_id"]:
            old_tunnel_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["tunnel_provider_id"],)).fetchone()
            if old_tunnel_row:
                try:
                    create_provider(old_tunnel_row).delete_host(old_public_host)
                except Exception as e:
                    add_log("warn", f"Could not clean up old tunnel during mode switch: {e}")

        if body.proxy_provider_id:
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
                        add_log("ok", f"Proxy updated: {new_public_host} → {body.forward_scheme}://{body.target_ip}:{body.target_port}")
                except Exception as e:
                    errors.append(str(e))

        dns_ip, dns_target_source = resolve_public_target(
            conn,
            mode=body.public_target_mode,
            manual_value=body.dns_ip,
            proxy_provider_id=body.proxy_provider_id,
            current_value=old["dns_ip"] or "",
        )
        if not dns_ip:
            dns_ip = old["dns_ip"] or ""

        if body.dns_provider_id and dns_ip:
            dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (body.dns_provider_id,)).fetchone()
            if dns_row:
                try:
                    dns = create_provider(dns_row)
                    old_dns_ip = old["dns_ip"] or ""
                    if old_mode == "proxy_dns" and old["dns_provider_id"] == body.dns_provider_id and old_dns_ip:
                        if old_public_host != new_public_host or old_dns_ip != dns_ip:
                            if dns.update_rewrite(old_public_host, old_dns_ip, new_public_host, dns_ip):
                                add_log("ok", f"DNS updated: {new_public_host} → {dns_ip} ({dns_target_source})")
                            else:
                                errors.append("Failed to update DNS rewrite")
                    else:
                        if dns.add_rewrite(new_public_host, dns_ip):
                            if old_mode == "proxy_dns" and old["dns_provider_id"] and old_dns_ip:
                                old_dns_row = conn.execute("SELECT * FROM providers WHERE id=?", (old["dns_provider_id"],)).fetchone()
                                if old_dns_row:
                                    try:
                                        create_provider(old_dns_row).delete_rewrite(old_public_host, old_dns_ip)
                                    except Exception as e:
                                        add_log("warn", f"Could not clean up old DNS rewrite: {e}")
                            add_log("ok", f"DNS updated: {new_public_host} → {dns_ip} ({dns_target_source})")
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

    primary_proxy_provider_id = stored_proxy_provider_id or stored_tunnel_provider_id
    extra_proxy_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_proxy_provider_ids)
        if pid and pid != primary_proxy_provider_id
    ]
    extra_dns_ids = [
        int(pid)
        for pid in dict.fromkeys(body.extra_dns_provider_ids)
        if pid and pid != stored_dns_provider_id
    ]

    set_push_targets(conn, sid, extra_proxy_ids, extra_dns_ids)

    set_tags(conn, sid, body.tag_ids)
    set_environments(conn, sid, body.environment_ids)
    conn.commit()
    add_log("ok", f"Service updated: {new_public_host}")

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
    errors = []

    if mode == "tunnel":
        if svc["tunnel_provider_id"]:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["tunnel_provider_id"],)).fetchone()
            if row:
                try:
                    if create_provider(row).delete_host(public_host):
                        add_log("ok", f"Tunnel route deleted: {public_host}")
                    else:
                        errors.append("Failed to delete tunnel route")
                except Exception as e:
                    errors.append(str(e))
    else:
        if svc["proxy_provider_id"] and svc["npm_host_id"]:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["proxy_provider_id"],)).fetchone()
            if row:
                try:
                    if create_provider(row).delete_host(svc["npm_host_id"]):
                        add_log("ok", f"Proxy deleted: {public_host}")
                    else:
                        errors.append("Failed to delete proxy host")
                except Exception as e:
                    errors.append(str(e))

        if svc["dns_provider_id"] and svc["dns_ip"]:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (svc["dns_provider_id"],)).fetchone()
            if row:
                try:
                    if create_provider(row).delete_rewrite(public_host, svc["dns_ip"]):
                        add_log("ok", f"DNS deleted: {public_host}")
                    else:
                        errors.append("Failed to delete DNS rewrite")
                except Exception as e:
                    errors.append(str(e))

    conn.execute("DELETE FROM services WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    add_log("info", f"Service deleted: {public_host}")
    return {"ok": True, "errors": errors}


@router.get("/api/services/{sid}/check")
def check_service(sid: int, request: Request):
    require_auth(request)
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
    conn.commit()
    conn.close()
    add_log("ok" if status == "ok" else "error", f"Check {public_host}: {status}")
    return {"id": sid, "status": status, "latency_ms": latency_ms, "dns_resolved": dns_resolved}


@router.post("/api/services/check-all")
def check_all(request: Request):
    require_auth(request)
    conn     = get_db()
    services = conn.execute(
        "SELECT id, target_ip, target_port, subdomain, domain FROM services WHERE enabled=1"
    ).fetchall()
    ok_count = error_count = 0

    for svc in services:
        status = "unknown"
        try:
            with socket.create_connection((svc["target_ip"], svc["target_port"]), timeout=3):
                status = "ok"
                ok_count += 1
        except OSError:
            status = "error"
            error_count += 1
        conn.execute(
            "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
            (status, svc["id"]),
        )

    conn.commit()
    conn.close()
    return {"checked": len(services), "ok": ok_count, "error": error_count}
