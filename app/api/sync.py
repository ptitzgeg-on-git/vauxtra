from fastapi import APIRouter, Body, Request, HTTPException
from app.models import get_db, get_db_ctx, add_log, set_tags, set_environments
from app.providers.factory import create_provider, PROVIDER_TYPES
from app.auth import require_auth
from app.public_target import resolve_public_target

router = APIRouter()


def _service_public_host(service_row) -> str:
    mode = (service_row["expose_mode"] or "proxy_dns").strip().lower() if "expose_mode" in service_row.keys() else "proxy_dns"
    if mode == "tunnel":
        tunnel_hostname = str(service_row["tunnel_hostname"] or "").strip().lower() if "tunnel_hostname" in service_row.keys() else ""
        if tunnel_hostname:
            return tunnel_hostname
    return f"{service_row['subdomain']}.{service_row['domain']}".strip(".").lower()


def _collect_push_targets(conn, svc, sid: int) -> tuple[str, str, list, list]:
    public_host = _service_public_host(svc)
    expose_mode = (svc["expose_mode"] or "proxy_dns").strip().lower() if "expose_mode" in svc.keys() else "proxy_dns"

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
    try:
        for h in proxy.list_hosts() or []:
            domains = h.get("domains") or h.get("domain_names") or []
            if public_host in domains:
                return h.get("id")
    except Exception:
        return None
    return None


def _build_push_plan(conn, svc, sid: int) -> dict:
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)

    proxy_actions: list[dict] = []
    dns_actions: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    dns_target = ""
    dns_target_source = ""
    if expose_mode != "tunnel":
        dns_target_mode = (svc["public_target_mode"] or "manual") if "public_target_mode" in svc.keys() else "manual"
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
                host_id = svc["npm_host_id"]
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
            errors.append("Unable to resolve DNS target for dry-run")

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


def _compute_service_drift(conn, svc, sid: int) -> dict:
    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
    issues: list[dict] = []

    expected_origin = f"{svc['forward_scheme']}://{svc['target_ip']}:{svc['target_port']}"

    for row in proxy_targets:
        try:
            provider = create_provider(row)
            hosts = provider.list_hosts() or []
            hit = None
            for host in hosts:
                domains = host.get("domains") or host.get("domain_names") or []
                if public_host in domains:
                    hit = host
                    break

            if not hit:
                issues.append(
                    {
                        "severity": "error",
                        "type": "missing_proxy_route",
                        "provider": row["name"],
                        "detail": f"Route {public_host} missing on provider",
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
                rewrites = provider.list_rewrites() or []
                match = next((r for r in rewrites if str(r.get("domain", "")).strip().lower() == public_host), None)
                if not match:
                    issues.append(
                        {
                            "severity": "error",
                            "type": "missing_dns_rewrite",
                            "provider": row["name"],
                            "detail": f"Rewrite {public_host} missing on provider",
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

    expose_mode, public_host, proxy_targets, dns_targets = _collect_push_targets(conn, svc, sid)
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
                host_id = svc["npm_host_id"]
            if not host_id:
                host_id = _find_host_id(proxy, public_host)

            if host_id:
                proxy.update_host(
                    host_id,
                    public_host,
                    svc["target_ip"],
                    svc["target_port"],
                    svc["forward_scheme"],
                    bool(svc["websocket"]),
                    cert_id,
                )
            else:
                result = proxy.create_host(
                    public_host,
                    svc["target_ip"],
                    svc["target_port"],
                    svc["forward_scheme"],
                    bool(svc["websocket"]),
                    cert_id,
                )
                if result and expose_mode != "tunnel" and row["id"] == svc["proxy_provider_id"]:
                    conn.execute("UPDATE services SET npm_host_id=? WHERE id=?", (result.get("id"), sid))

            add_log("ok", f"[Push] Proxy synced on {row['name']}: {public_host}", conn)
        except Exception as e:
            errors.append(f"Proxy ({row['name']}): {e}")

    if expose_mode != "tunnel":
        dns_target_mode = (svc["public_target_mode"] or "manual") if "public_target_mode" in svc.keys() else "manual"
        manual_value = svc["dns_ip"] if dns_target_mode != "auto" else ""
        dns_target, dns_target_source = resolve_public_target(
            conn,
            mode=dns_target_mode,
            manual_value=manual_value,
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=svc["dns_ip"] or "",
        )

        if dns_target and dns_target != (svc["dns_ip"] or ""):
            conn.execute("UPDATE services SET dns_ip=? WHERE id=?", (dns_target, sid))
            add_log("info", f"[Push] DNS target refreshed for {public_host}: {svc['dns_ip']} → {dns_target} ({dns_target_source})", conn)

        if dns_target:
            for row in dns_targets:
                try:
                    dns = create_provider(row)
                    old_value = svc["dns_ip"] or dns_target
                    if not dns.update_rewrite(public_host, old_value, public_host, dns_target):
                        dns.add_rewrite(public_host, dns_target)
                    add_log("ok", f"[Push] DNS synced on {row['name']}: {public_host} → {dns_target}", conn)
                except Exception as e:
                    errors.append(f"DNS ({row['name']}): {e}")

    conn.commit()
    conn.close()
    return {"ok": not errors, "errors": errors}


@router.post("/api/services/{sid}/push/dry-run")
def dry_run_push_service(sid: int, request: Request):
    require_auth(request)
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
    require_auth(request)
    conn = get_db()
    providers = conn.execute("SELECT * FROM providers WHERE enabled=1").fetchall()
    existing_fqdns = {
        f"{r['subdomain']}.{r['domain']}"
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
            elif is_dns:
                rewrites = provider.list_rewrites()
                for r in rewrites:
                    r["_provider_id"]      = p["id"]
                    r["_provider_name"]    = p["name"]
                    r["_already_imported"] = r.get("domain", "") in existing_fqdns
                result["dns_rewrites"].extend(rewrites)
        except Exception as e:
            add_log("error", f"Sync {p['name']}: {e}")

    return result


@router.post("/api/services/import")
def import_services(request: Request, data: dict = Body(...)):
    require_auth(request, scope="write")
    imported = 0
    errors   = []
    conn     = get_db()

    dns_by_fqdn = {r["domain"]: r for r in data.get("dns_rewrites", []) if r.get("domain")}

    for h in data.get("proxy_hosts", []):
        try:
            domains = h.get("domains") or h.get("domain_names", [])
            if not domains:
                continue
            fqdn  = domains[0]
            parts = fqdn.split(".", 1)
            if len(parts) < 2:
                continue
            subdomain, domain = parts[0], parts[1]
            if conn.execute("SELECT id FROM services WHERE subdomain=? AND domain=?", (subdomain, domain)).fetchone():
                continue

            provider_type = (h.get("_provider_type") or "").strip().lower()

            dns_match       = dns_by_fqdn.get(fqdn)
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
            add_log("ok", f"Imported: {fqdn}" + (" (proxy + DNS)" if dns_match else ""), conn)
        except Exception as e:
            errors.append(str(e))

    for fqdn, r in dns_by_fqdn.items():
        try:
            ip    = r.get("answer", "")
            parts = fqdn.split(".", 1)
            if not ip or len(parts) < 2:
                continue
            subdomain, domain = parts[0], parts[1]
            existing = conn.execute(
                "SELECT id FROM services WHERE subdomain=? AND domain=?", (subdomain, domain)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE services SET dns_provider_id=?, dns_ip=? WHERE id=?",
                    (r.get("_provider_id"), ip, existing["id"]),
                )
                add_log("ok", f"DNS linked: {fqdn} → {ip}", conn)
            else:
                conn.execute(
                    """INSERT INTO services
                       (subdomain, domain, target_ip, target_port, dns_provider_id, dns_ip)
                       VALUES (?,?,?,?,?,?)""",
                    (subdomain, domain, ip, 80, r.get("_provider_id"), ip),
                )
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
                imported += 1
                add_log("ok", f"Imported from DNS: {fqdn} → {ip}", conn)
        except Exception as e:
            errors.append(str(e))

    conn.commit()
    conn.close()
    return {"imported": imported, "errors": errors}
