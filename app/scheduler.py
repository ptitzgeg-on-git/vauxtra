"""Automatic health check and reconcile scheduler."""
import json
import socket
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

from app.models import get_db, add_log
from app.providers.factory import create_provider
from app.public_target import detect_server_public_ip, load_public_target_policy, resolve_public_target

_scheduler = BackgroundScheduler(daemon=True)
_lock      = threading.Lock()
_alert_down_since: dict[tuple[int, int], float] = {}
_alert_down_sent: set[tuple[int, int]] = set()
_tunnel_last_status: dict[int, str] = {}
# Circuit-breaker: track consecutive failures per service for DNS auto-update
_dns_update_failures: dict[int, int] = {}
_DNS_FAILURE_THRESHOLD = 3  # Disable auto-update after this many consecutive failures


def _load_scheduler_state() -> None:
    """Load persisted alert state from database on startup."""
    global _alert_down_since, _alert_down_sent, _tunnel_last_status, _dns_update_failures
    try:
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM scheduler_state").fetchall()
        conn.close()
        for row in rows:
            key, val = row["key"], row["value"]
            data = json.loads(val)
            if key == "alert_down_since":
                # Convert list keys back to tuples
                _alert_down_since = {tuple(k): v for k, v in data.items()} if isinstance(data, dict) else {}
            elif key == "alert_down_sent":
                _alert_down_sent = {tuple(k) for k in data} if isinstance(data, list) else set()
            elif key == "tunnel_last_status":
                _tunnel_last_status = {int(k): v for k, v in data.items()} if isinstance(data, dict) else {}
            elif key == "dns_update_failures":
                _dns_update_failures = {int(k): v for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        import traceback
        add_log("error", f"Failed to load scheduler state: {traceback.format_exc()}")


def _save_scheduler_state() -> None:
    """Persist alert state to database."""
    try:
        conn = get_db()
        # Convert tuples to lists for JSON serialization
        state_items = [
            ("alert_down_since", json.dumps({str(list(k)): v for k, v in _alert_down_since.items()})),
            ("alert_down_sent", json.dumps([list(k) for k in _alert_down_sent])),
            ("tunnel_last_status", json.dumps(_tunnel_last_status)),
            ("dns_update_failures", json.dumps(_dns_update_failures)),
        ]
        for key, value in state_items:
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (key, value),
            )
        conn.commit()
        conn.close()
    except Exception:
        import traceback
        add_log("error", f"Failed to save scheduler state: {traceback.format_exc()}")


# ── Auto-reconcile job ────────────────────────────────────────────────────────

def run_auto_reconcile() -> None:
    """Detect drift on all enabled services and push corrections automatically.

    Only runs if the ``auto_reconcile_enabled`` setting is ``true``.
    Each corrected service fires a webhook notification if configured.
    """
    conn = get_db()
    enabled_row = conn.execute(
        "SELECT value FROM settings WHERE key='auto_reconcile_enabled'"
    ).fetchone()
    conn.close()

    if not enabled_row or enabled_row["value"] != "true":
        return

    # Import inside the function body to avoid circular module-level imports.
    from app.api.sync import _compute_service_drift, _execute_push  # noqa: PLC0415

    conn = get_db()
    services = conn.execute("SELECT * FROM services WHERE enabled=1").fetchall()
    conn.close()

    corrected: list[str] = []
    errors: list[str] = []

    for svc in services:
        sid  = int(svc["id"])
        fqdn = f"{svc['subdomain']}.{svc['domain']}"
        try:
            conn  = get_db()
            drift = _compute_service_drift(conn, svc, sid)
            conn.close()

            if drift.get("ok"):
                continue  # no drift, skip

            result = _execute_push(svc, sid)
            if result.get("ok"):
                corrected.append(fqdn)
                add_log("ok", f"[AutoReconcile] Corrected drift for {fqdn}")
            else:
                err_detail = "; ".join(result.get("errors", []))
                errors.append(f"{fqdn}: {err_detail}")
                add_log("error", f"[AutoReconcile] Push failed for {fqdn}: {err_detail}")
        except Exception as e:
            errors.append(f"{fqdn}: {e}")
            add_log("error", f"[AutoReconcile] {fqdn}: {e}")

    if corrected:
        _fire_reconcile_webhook(corrected, errors)


# ── TCP health check ──────────────────────────────────────────────────────

def _tcp_ok(ip: str, port: int) -> str:
    try:
        with socket.create_connection((ip, port), timeout=3):
            return "ok"
    except OSError:
        return "error"


# ── Job principal ─────────────────────────────────────────────────────────

def run_health_checks() -> None:
    """Check all services, record uptime events, and dispatch alerts."""
    with _lock:
        conn = get_db()
        services = conn.execute(
            "SELECT id, target_ip, target_port, subdomain, domain, status, expose_mode FROM services WHERE enabled=1"
        ).fetchall()

        changed: list[dict] = []
        for svc in services:
            old_status = svc["status"] or "unknown"

            # Tunnel services are health-checked via the Cloudflare API, not TCP.
            # Running TCP against cfargotunnel.com or similar targets always fails.
            if (svc["expose_mode"] or "").strip().lower() == "tunnel":
                continue

            new_status = _tcp_ok(svc["target_ip"], svc["target_port"])

            conn.execute(
                "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
                (new_status, svc["id"]),
            )
            conn.execute(
                "INSERT INTO uptime_events (service_id, status) VALUES (?,?)",
                (svc["id"], new_status),
            )

            if old_status != new_status:
                fqdn = f"{svc['subdomain']}.{svc['domain']}"
                changed.append(
                    {
                        "service_id": svc["id"],
                        "fqdn": fqdn,
                        "old": old_status,
                        "new": new_status,
                    }
                )
                add_log(
                    "ok" if new_status == "ok" else "error",
                    f"[Auto] {fqdn} : {old_status} → {new_status}",
                    conn,
                )

        _run_dns_auto_updates(conn)
        changed.extend(_run_tunnel_health_checks(conn))

        # Purge events older than 7 days
        conn.execute("DELETE FROM uptime_events WHERE created_at < datetime('now', '-7 days')")
        conn.commit()
        conn.close()

        # Persist scheduler state after closing connection to avoid locks
        _save_scheduler_state()

        if changed:
            _fire_global_webhook(changed)
        _fire_service_webhooks()


def _run_tunnel_health_checks(conn) -> list[dict]:
    """Check cloudflare_tunnel providers and return status transitions for notifications."""
    global _tunnel_last_status

    rows = conn.execute(
        "SELECT id, name, type, enabled FROM providers WHERE type='cloudflare_tunnel' AND enabled=1"
    ).fetchall()
    if not rows:
        _tunnel_last_status.clear()
        return []

    changed: list[dict] = []
    seen_ids: set[int] = set()

    for row in rows:
        provider_id = int(row["id"])
        seen_ids.add(provider_id)

        new_status = "error"
        detail = "unreachable"
        try:
            provider = create_provider(row)
            if hasattr(provider, "health_status"):
                health = provider.health_status()
                new_status = "ok" if health.get("ok") else "error"
                detail = str(health.get("status") or detail)
            else:
                ok = bool(provider.test_connection())
                new_status = "ok" if ok else "error"
                detail = "healthy" if ok else "down"
        except Exception as e:
            new_status = "error"
            detail = str(e)

        old_status = _tunnel_last_status.get(provider_id, "unknown")
        _tunnel_last_status[provider_id] = new_status

        if old_status != new_status and old_status != "unknown":
            label = f"tunnel:{row['name']}"
            changed.append(
                {
                    "provider_id": provider_id,
                    "fqdn": label,
                    "old": old_status,
                    "new": new_status,
                }
            )
            add_log(
                "ok" if new_status == "ok" else "error",
                f"[Tunnel] {row['name']} : {old_status} → {new_status} ({detail})",
                conn,
            )

    for pid in list(_tunnel_last_status.keys()):
        if pid not in seen_ids:
            _tunnel_last_status.pop(pid, None)

    return changed


def _run_dns_auto_updates(conn) -> None:
    """Refresh DNS targets for services configured with auto public target updates.
    
    Implements a circuit-breaker: after 3 consecutive failures per service,
    auto-update is disabled until manually re-enabled via the UI.
    """
    global _dns_update_failures
    
    services = conn.execute(
        """
        SELECT id, subdomain, domain, dns_ip, dns_provider_id, proxy_provider_id
        FROM services
        WHERE enabled=1
          AND dns_provider_id IS NOT NULL
          AND COALESCE(public_target_mode, 'manual')='auto'
          AND COALESCE(auto_update_dns, 0)=1
        """
    ).fetchall()
    if not services:
        return

    policy = load_public_target_policy(conn)
    server_public_ip = detect_server_public_ip(
        sources=policy["sources"],
        timeout_seconds=policy["timeout_seconds"],
    )

    state_changed = False
    for svc in services:
        sid = int(svc["id"])
        fqdn = f"{svc['subdomain']}.{svc['domain']}"
        current_target = (svc["dns_ip"] or "").strip().lower()

        resolved_target, target_source = resolve_public_target(
            conn,
            mode="auto",
            manual_value="",
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=current_target,
            server_public_ip=server_public_ip,
        )
        if not resolved_target or resolved_target == current_target:
            # Success (no update needed) - reset failure count
            if sid in _dns_update_failures:
                del _dns_update_failures[sid]
                state_changed = True
            continue

        row = conn.execute(
            "SELECT * FROM providers WHERE id=? AND enabled=1",
            (svc["dns_provider_id"],),
        ).fetchone()
        if not row:
            continue

        old_target = current_target or resolved_target
        try:
            dns = create_provider(row)
            if not dns.update_rewrite(fqdn, old_target, fqdn, resolved_target):
                dns.add_rewrite(fqdn, resolved_target)
            conn.execute("UPDATE services SET dns_ip=? WHERE id=?", (resolved_target, svc["id"]))
            add_log("info", f"[AutoDNS] {fqdn}: {old_target} → {resolved_target} ({target_source})", conn)
            # Success - reset failure count
            if sid in _dns_update_failures:
                del _dns_update_failures[sid]
                state_changed = True
        except Exception as e:
            # Increment failure count
            _dns_update_failures[sid] = _dns_update_failures.get(sid, 0) + 1
            state_changed = True
            
            if _dns_update_failures[sid] >= _DNS_FAILURE_THRESHOLD:
                # Circuit-breaker triggered: disable auto-update for this service
                conn.execute("UPDATE services SET auto_update_dns=0 WHERE id=?", (sid,))
                add_log(
                    "error",
                    f"[AutoDNS] Circuit-breaker: {fqdn} disabled after {_DNS_FAILURE_THRESHOLD} consecutive failures. Last error: {e}",
                    conn,
                )
                del _dns_update_failures[sid]
            else:
                add_log("error", f"[AutoDNS] {fqdn}: {e} (failure {_dns_update_failures[sid]}/{_DNS_FAILURE_THRESHOLD})", conn)
    
    if state_changed:
        _save_scheduler_state()


# ── Webhook ───────────────────────────────────────────────────────────────

def _fire_global_webhook(changed: list[dict]) -> None:
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('webhook_url', 'webhook_enabled')"
        ).fetchall()
        conn.close()
        cfg = {r["key"]: r["value"] for r in rows}

        if cfg.get("webhook_enabled") != "true":
            return
        url = cfg.get("webhook_url", "").strip()
        if not url:
            return

        import apprise
        a = apprise.Apprise()
        if not a.add(url):
            return

        down  = [s for s in changed if s["new"] == "error"]
        up    = [s for s in changed if s["new"] == "ok"]
        lines = []
        if down:
            lines.append("Down: " + ", ".join(s["fqdn"] for s in down))
        if up:
            lines.append("Recovered: " + ", ".join(s["fqdn"] for s in up))

        a.notify(title="Vauxtra - Status change", body="\n".join(lines))
    except Exception:
        import traceback
        add_log("error", f"Global webhook failed: {traceback.format_exc()}")


def _fire_service_webhooks() -> None:
    """Dispatch per-service alerts configured in service_alerts/webhooks."""
    global _alert_down_since, _alert_down_sent

    try:
        conn = get_db()
        rows = conn.execute(
            """
            SELECT sa.service_id,
                   sa.webhook_id,
                   sa.on_up,
                   sa.on_down,
                   sa.min_down_minutes,
                   w.url AS webhook_url,
                   s.subdomain,
                   s.domain,
                   s.status
            FROM service_alerts sa
            JOIN webhooks w ON w.id = sa.webhook_id
            JOIN services s ON s.id = sa.service_id
            WHERE w.enabled = 1 AND s.enabled = 1
            """
        ).fetchall()
        conn.close()

        if not rows:
            _alert_down_since.clear()
            _alert_down_sent.clear()
            return

        now = time.monotonic()
        valid_keys: set[tuple[int, int]] = set()
        messages_by_url: dict[str, list[str]] = {}

        for row in rows:
            key = (int(row["service_id"]), int(row["webhook_id"]))
            valid_keys.add(key)

            status = (row["status"] or "unknown").lower()
            fqdn = f"{row['subdomain']}.{row['domain']}"
            on_up = bool(row["on_up"])
            on_down = bool(row["on_down"])
            min_down = max(0, int(row["min_down_minutes"] or 0))

            if status == "error":
                if not on_down:
                    continue

                since = _alert_down_since.get(key)
                if since is None:
                    _alert_down_since[key] = now
                    since = now

                elapsed_minutes = (now - since) / 60.0
                if elapsed_minutes >= min_down and key not in _alert_down_sent:
                    messages_by_url.setdefault(row["webhook_url"], []).append(
                        f"DOWN: {fqdn} ({elapsed_minutes:.1f}m)"
                    )
                    _alert_down_sent.add(key)
            else:
                had_down = key in _alert_down_since or key in _alert_down_sent
                if had_down and status == "ok" and on_up:
                    messages_by_url.setdefault(row["webhook_url"], []).append(
                        f"RECOVERED: {fqdn}"
                    )
                _alert_down_since.pop(key, None)
                _alert_down_sent.discard(key)

        # Cleanup stale state for deleted/disabled alert rules.
        for key in list(_alert_down_since):
            if key not in valid_keys:
                _alert_down_since.pop(key, None)
        for key in list(_alert_down_sent):
            if key not in valid_keys:
                _alert_down_sent.discard(key)

        # Persist state to database to survive restarts
        _save_scheduler_state()

        if not messages_by_url:
            return

        import apprise

        for url, lines in messages_by_url.items():
            a = apprise.Apprise()
            if not a.add(url):
                continue
            a.notify(title="Vauxtra - Service alert", body="\n".join(lines))
    except Exception:
        import traceback
        add_log("error", f"Service webhook failed: {traceback.format_exc()}")


def _fire_reconcile_webhook(corrected: list[str], errors: list[str]) -> None:
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('webhook_url', 'webhook_enabled')"
        ).fetchall()
        conn.close()
        cfg = {r["key"]: r["value"] for r in rows}

        if cfg.get("webhook_enabled") != "true":
            return
        url = cfg.get("webhook_url", "").strip()
        if not url:
            return

        import apprise
        a = apprise.Apprise()
        if not a.add(url):
            return

        lines = [f"Auto-reconcile corrected {len(corrected)} service(s):"]
        lines.extend(f"  ✓ {fqdn}" for fqdn in corrected)
        if errors:
            lines.append(f"Errors ({len(errors)}):")
            lines.extend(f"  ✗ {e}" for e in errors)

        a.notify(title="Vauxtra: Auto-Reconcile", body="\n".join(lines))
    except Exception:
        import traceback
        add_log("error", f"Reconcile webhook failed: {traceback.format_exc()}")


# ── Scheduler control ─────────────────────────────────────────────────────

def configure(interval_minutes: int) -> None:
    """Reconfigure the health-check interval (0 = disabled)."""
    if _scheduler.get_job("health_check"):
        _scheduler.remove_job("health_check")
    if interval_minutes > 0:
        _scheduler.add_job(
            run_health_checks,
            "interval",
            minutes=interval_minutes,
            id="health_check",
            replace_existing=True,
        )


def configure_reconcile(enabled: bool, interval_minutes: int) -> None:
    """Reconfigure the auto-reconcile job (enabled=False or interval=0 disables it)."""
    if _scheduler.get_job("auto_reconcile"):
        _scheduler.remove_job("auto_reconcile")
    if enabled and interval_minutes > 0:
        _scheduler.add_job(
            run_auto_reconcile,
            "interval",
            minutes=interval_minutes,
            id="auto_reconcile",
            replace_existing=True,
        )


def start(interval_minutes: int = 0) -> None:
    """Start the scheduler. Call once at application startup."""
    # Load persisted alert state from database
    _load_scheduler_state()
    
    configure(interval_minutes)

    # Load auto-reconcile settings from DB
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('auto_reconcile_enabled', 'auto_reconcile_interval')"
        ).fetchall()
        conn.close()
        cfg = {r["key"]: r["value"] for r in rows}
        enabled   = cfg.get("auto_reconcile_enabled") == "true"
        interval  = int(cfg.get("auto_reconcile_interval") or 0)
        configure_reconcile(enabled, interval)
    except Exception:
        import traceback
        add_log("error", f"Scheduler auto-reconcile config failed: {traceback.format_exc()}")

    if not _scheduler.running:
        _scheduler.start()
