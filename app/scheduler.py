"""Automatic health check and reconcile scheduler."""
import json
import socket
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

from app.models import add_log, get_db
from app.providers.factory import create_provider
from app.public_target import (
    detect_server_public_ip,
    load_public_target_policy,
    resolve_public_target,
)

_scheduler = BackgroundScheduler(daemon=True)
_lock      = threading.Lock()
_alert_down_since: dict[tuple[int, int], float] = {}
_alert_down_sent: set[tuple[int, int]] = set()
_provider_last_status: dict[int, str] = {}
_tunnel_last_status = _provider_last_status
_webhook_service_down_since: dict[tuple[int, int], float] = {}
_webhook_service_last_sent: dict[tuple[int, int], float] = {}
# Circuit-breaker: track consecutive failures per service for DNS auto-update
_dns_update_failures: dict[int, int] = {}
_DNS_FAILURE_THRESHOLD = 3  # Disable auto-update after this many consecutive failures
# Certificate expiry alert state: (provider_id, cert_id) → {"level", "alerted_at"}
_cert_alert_state: dict[tuple[int, int], dict] = {}
# Webhook retry backoff in seconds for successive failed attempts
_WEBHOOK_RETRY_BACKOFF = [60, 300, 1800, 7200, 86400]


def _read_retention_days(conn, key: str, default_days: int, *, min_days: int = 1, max_days: int = 365) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default_days
    try:
        value = int(str(row["value"]).strip())
    except Exception:
        return default_days
    return max(min_days, min(max_days, value))


def _decode_tuple_key(raw_key) -> tuple[int, int] | None:
    if isinstance(raw_key, list) and len(raw_key) == 2:
        return int(raw_key[0]), int(raw_key[1])
    if isinstance(raw_key, str):
        try:
            data = json.loads(raw_key)
        except json.JSONDecodeError:
            return None
        if isinstance(data, list) and len(data) == 2:
            return int(data[0]), int(data[1])
    return None


def _load_tuple_value_map(data) -> dict[tuple[int, int], float]:
    if not isinstance(data, dict):
        return {}
    result: dict[tuple[int, int], float] = {}
    for raw_key, value in data.items():
        key = _decode_tuple_key(raw_key)
        if key is not None:
            result[key] = float(value)
    return result


def _load_tuple_set(data) -> set[tuple[int, int]]:
    if not isinstance(data, list):
        return set()
    result: set[tuple[int, int]] = set()
    for raw_key in data:
        key = _decode_tuple_key(raw_key)
        if key is not None:
            result.add(key)
    return result


def _dump_tuple_value_map(data: dict[tuple[int, int], float]) -> str:
    return json.dumps({json.dumps(list(k)): v for k, v in data.items()})


def _dump_tuple_set(data: set[tuple[int, int]]) -> str:
    return json.dumps([list(k) for k in data])


def _load_scheduler_state() -> None:
    """Load persisted alert state from database on startup."""
    global _alert_down_since, _alert_down_sent, _provider_last_status
    global _webhook_service_down_since, _webhook_service_last_sent, _dns_update_failures
    try:
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM scheduler_state").fetchall()
        conn.close()
        for row in rows:
            key, val = row["key"], row["value"]
            data = json.loads(val)
            if key == "alert_down_since":
                _alert_down_since = _load_tuple_value_map(data)
            elif key == "alert_down_sent":
                _alert_down_sent = _load_tuple_set(data)
            elif key in {"provider_last_status", "tunnel_last_status"}:
                _provider_last_status = {int(k): v for k, v in data.items()} if isinstance(data, dict) else {}
            elif key == "webhook_service_down_since":
                _webhook_service_down_since = _load_tuple_value_map(data)
            elif key == "webhook_service_last_sent":
                _webhook_service_last_sent = _load_tuple_value_map(data)
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
            ("alert_down_since", _dump_tuple_value_map(_alert_down_since)),
            ("alert_down_sent", _dump_tuple_set(_alert_down_sent)),
            ("provider_last_status", json.dumps(_provider_last_status)),
            ("webhook_service_down_since", _dump_tuple_value_map(_webhook_service_down_since)),
            ("webhook_service_last_sent", _dump_tuple_value_map(_webhook_service_last_sent)),
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
                add_log("info", f"[AutoReconcile] Corrected drift for {fqdn}")
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

        # Sync NPM enable/disable state once per cycle (not on every API call)
        try:
            from app.api.services import _sync_npm_statuses  # noqa: PLC0415
            _sync_npm_statuses(conn)
            conn.commit()
        except Exception:
            pass

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
        changed.extend(_run_provider_health_checks(conn))
        _run_cert_expiry_alerts(conn)
        _run_webhook_retry(conn)

        # Purge old monitoring history/logs according to settings.
        monitoring_retention_days = _read_retention_days(conn, "monitoring_retention_days", 14)
        log_retention_days = _read_retention_days(conn, "log_retention_days", 30)
        webhook_retry_retention_days = _read_retention_days(
            conn, "webhook_retry_retention_days", 7, min_days=1, max_days=90
        )
        conn.execute(
            "DELETE FROM uptime_events WHERE created_at < datetime('now', ?)" ,
            (f"-{monitoring_retention_days} days",),
        )
        conn.execute(
            "DELETE FROM logs WHERE created_at < datetime('now', ?)",
            (f"-{log_retention_days} days",),
        )
        try:
            conn.execute(
                """DELETE FROM webhook_delivery_log
                   WHERE status IN ('delivered', 'failed')
                     AND updated_at < datetime('now', ?)""",
                (f"-{webhook_retry_retention_days} days",),
            )
        except Exception:
            pass
        conn.commit()
        conn.close()

        # Persist scheduler state after closing connection to avoid locks
        _save_scheduler_state()

        if changed:
            _fire_global_webhook()
        if any("provider_id" in c for c in changed):
            _fire_integration_webhook(changed)
        _fire_service_webhooks()


def _run_provider_health_checks(conn) -> list[dict]:
    """Check enabled providers and return status transitions for notifications."""
    global _provider_last_status

    rows = conn.execute(
        "SELECT id, name, type, url, username, password, extra, enabled FROM providers WHERE enabled=1"
    ).fetchall()
    if not rows:
        _provider_last_status.clear()
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

        old_status = _provider_last_status.get(provider_id, "unknown")
        _provider_last_status[provider_id] = new_status

        if old_status != new_status and old_status != "unknown":
            label = f"provider:{row['name']}"
            changed.append(
                {
                    "provider_id": provider_id,
                    "provider_type": row["type"],
                    "fqdn": label,
                    "old": old_status,
                    "new": new_status,
                }
            )
            add_log(
                "info" if new_status == "ok" else "error",
                f"[Provider] {row['name']} : {old_status} → {new_status} ({detail})",
                conn,
            )

    for pid in list(_provider_last_status.keys()):
        if pid not in seen_ids:
            _provider_last_status.pop(pid, None)

    return changed


def _run_tunnel_health_checks(conn) -> list[dict]:
    """Backward-compatible wrapper for older callers/tests."""
    return _run_provider_health_checks(conn)


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


# ── Certificate expiry alerts ────────────────────────────────────────────

def _run_cert_expiry_alerts(conn) -> None:
    """Scan NPM proxy providers for certificates close to expiry and log alerts.

    Alerts:  < 30 days → warn   |   < 7 days → error
    Re-alerts after 24 h or when the severity level changes.
    """
    global _cert_alert_state
    import datetime as _dt

    try:
        npm_rows = conn.execute(
            "SELECT * FROM providers WHERE type='npm' AND enabled=1"
        ).fetchall()
        if not npm_rows:
            return

        now_utc = _dt.datetime.utcnow()
        seen_keys: set[tuple[int, int]] = set()

        for prow in npm_rows:
            provider_id = int(prow["id"])
            try:
                provider = create_provider(prow)
                certs = provider.get_certificates()
            except Exception:
                continue

            for cert in certs:
                cert_id = cert.get("id")
                if cert_id is None:
                    continue

                expires_raw = (cert.get("expires_on") or "").strip()
                if not expires_raw:
                    continue

                try:
                    # Strip timezone info for naïve comparison with utcnow()
                    normalized = expires_raw.replace("Z", "").split("+")[0].split(".")[0]
                    expires = _dt.datetime.fromisoformat(normalized)
                except Exception:
                    continue

                days_left = (expires - now_utc).days
                key = (provider_id, cert_id)
                seen_keys.add(key)

                if days_left < 7:
                    level = "error"
                    msg = (
                        f"[CertExpiry] CRITICAL: '{cert.get('nice_name')}' (ID {cert_id}) "
                        f"expires in {days_left} day(s)"
                    )
                elif days_left < 30:
                    level = "warn"
                    msg = (
                        f"[CertExpiry] WARNING: '{cert.get('nice_name')}' (ID {cert_id}) "
                        f"expires in {days_left} day(s)"
                    )
                else:
                    _cert_alert_state.pop(key, None)
                    continue

                prior = _cert_alert_state.get(key, {})
                elapsed = time.monotonic() - prior.get("alerted_at", 0)
                if prior.get("level") != level or elapsed > 86400:
                    add_log(level, msg, conn)
                    _cert_alert_state[key] = {"level": level, "alerted_at": time.monotonic()}

        for key in list(_cert_alert_state):
            if key not in seen_keys:
                _cert_alert_state.pop(key, None)

    except Exception:
        import traceback
        add_log("error", f"[CertExpiry] Check failed: {traceback.format_exc()}")


# ── Webhook retry ─────────────────────────────────────────────────────────

def _try_send_apprise(url: str, title: str, body: str, conn, webhook_id=None) -> bool:
    """Send a notification via Apprise. Log failed deliveries for later retry."""
    import apprise as _apprise
    a = _apprise.Apprise()
    if not a.add(url):
        return False
    try:
        ok = bool(a.notify(title=title, body=body))
        if not ok:
            raise RuntimeError("Apprise.notify() returned False")
        return True
    except Exception as exc:
        try:
            import datetime as _dt
            delay = _WEBHOOK_RETRY_BACKOFF[0]
            next_retry = (_dt.datetime.utcnow() + _dt.timedelta(seconds=delay)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            conn.execute(
                """INSERT INTO webhook_delivery_log
                   (webhook_id, url, title, body, status, attempt, next_retry_at, error_msg)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (webhook_id, url, title, body, "pending", 1, next_retry, str(exc)),
            )
        except Exception:
            pass
        return False


def _run_webhook_retry(conn) -> None:
    """Retry pending webhook deliveries that are due, applying exponential backoff."""
    import apprise as _apprise
    import datetime as _dt

    MAX_ATTEMPTS = len(_WEBHOOK_RETRY_BACKOFF)
    try:
        rows = conn.execute(
            """SELECT id, webhook_id, url, title, body, attempt
               FROM webhook_delivery_log
               WHERE status='pending' AND next_retry_at <= datetime('now')
               ORDER BY next_retry_at
               LIMIT 20"""
        ).fetchall()
    except Exception:
        return

    for row in rows:
        dlid = int(row["id"])
        attempt = int(row["attempt"] or 0)

        if attempt >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE webhook_delivery_log SET status='failed', updated_at=datetime('now') WHERE id=?",
                (dlid,),
            )
            add_log("error", f"[Webhook] Delivery abandoned after {attempt} attempts: {row['url']}", conn)
            continue

        a = _apprise.Apprise()
        if not a.add(row["url"]):
            conn.execute(
                "UPDATE webhook_delivery_log SET status='failed', updated_at=datetime('now') WHERE id=?",
                (dlid,),
            )
            continue

        try:
            result = a.notify(title=row["title"] or "", body=row["body"] or "")
            if result:
                conn.execute(
                    "UPDATE webhook_delivery_log SET status='delivered', updated_at=datetime('now') WHERE id=?",
                    (dlid,),
                )
            else:
                raise RuntimeError("notify() returned falsy")
        except Exception as exc:
            new_attempt = attempt + 1
            if new_attempt >= MAX_ATTEMPTS:
                conn.execute(
                    """UPDATE webhook_delivery_log
                       SET status='failed', attempt=?, error_msg=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (new_attempt, str(exc), dlid),
                )
                add_log("error", f"[Webhook] Delivery abandoned: {row['url']}", conn)
            else:
                delay = _WEBHOOK_RETRY_BACKOFF[new_attempt - 1]
                next_retry = (_dt.datetime.utcnow() + _dt.timedelta(seconds=delay)).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                conn.execute(
                    """UPDATE webhook_delivery_log
                       SET attempt=?, next_retry_at=?, error_msg=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (new_attempt, next_retry, str(exc), dlid),
                )


# ── Webhook ───────────────────────────────────────────────────────────────

def _service_matches_scope(row, extra_target_map: dict[int, set[int]]) -> bool:
    scope_type = (row["scope_type"] or "all").lower()
    scope_ref_id = row["scope_ref_id"]
    if scope_type == "all":
        return True
    if scope_type == "service":
        return scope_ref_id == row["service_id"]
    if scope_type == "provider":
        provider_ids = {
            row["dns_provider_id"],
            row["proxy_provider_id"],
            row["tunnel_provider_id"],
        }
        provider_ids.update(extra_target_map.get(int(row["service_id"]), set()))
        return scope_ref_id in {pid for pid in provider_ids if pid is not None}
    return False


def _fire_global_webhook() -> None:
    """Fire scoped service-status alerts with optional repeat reminders."""
    global _webhook_service_down_since, _webhook_service_last_sent
    try:
        conn = get_db()
        rows = conn.execute(
            """
            SELECT w.id AS webhook_id,
                   w.url,
                   w.scope_type,
                   w.scope_ref_id,
                   w.alert_on_any_down,
                   w.alert_on_any_up,
                   w.min_down_minutes,
                   w.repeat_interval_minutes,
                   s.id AS service_id,
                   s.subdomain,
                   s.domain,
                   s.status,
                   s.dns_provider_id,
                   s.proxy_provider_id,
                   s.tunnel_provider_id
            FROM webhooks w
            JOIN services s ON s.enabled=1
            WHERE w.enabled=1
              AND (w.alert_on_any_down=1 OR w.alert_on_any_up=1)
            """
        ).fetchall()
        extra_target_rows = conn.execute(
            "SELECT service_id, provider_id FROM service_push_targets"
        ).fetchall()
        conn.close()

        if not rows:
            _webhook_service_down_since.clear()
            _webhook_service_last_sent.clear()
            return

        extra_target_map: dict[int, set[int]] = {}
        for extra in extra_target_rows:
            extra_target_map.setdefault(int(extra["service_id"]), set()).add(int(extra["provider_id"]))

        now = time.monotonic()
        messages_by_url: dict[str, list[str]] = {}
        valid_keys: set[tuple[int, int]] = set()

        import apprise

        for row in rows:
            if not _service_matches_scope(row, extra_target_map):
                continue
            key = (int(row["webhook_id"]), int(row["service_id"]))
            valid_keys.add(key)
            fqdn = f"{row['subdomain']}.{row['domain']}"
            status = (row["status"] or "unknown").lower()
            min_down = max(0, int(row["min_down_minutes"] or 0))
            repeat_interval = max(0, int(row["repeat_interval_minutes"] or 0))

            if status == "error":
                if not bool(row["alert_on_any_down"]):
                    continue
                since = _webhook_service_down_since.get(key)
                if since is None:
                    _webhook_service_down_since[key] = now
                    since = now
                elapsed_minutes = (now - since) / 60.0
                last_sent = _webhook_service_last_sent.get(key)
                should_send = False
                message = f"DOWN: {fqdn} ({elapsed_minutes:.1f}m)"
                if elapsed_minutes >= min_down and last_sent is None:
                    should_send = True
                elif last_sent is not None and repeat_interval > 0 and (now - last_sent) >= repeat_interval * 60:
                    should_send = True
                    message = f"REMINDER: {fqdn} still down ({elapsed_minutes:.1f}m)"

                if should_send:
                    messages_by_url.setdefault(row["url"], []).append(message)
                    _webhook_service_last_sent[key] = now
            else:
                had_down = key in _webhook_service_down_since or key in _webhook_service_last_sent
                if had_down and status == "ok" and bool(row["alert_on_any_up"]):
                    messages_by_url.setdefault(row["url"], []).append(f"RECOVERED: {fqdn}")
                _webhook_service_down_since.pop(key, None)
                _webhook_service_last_sent.pop(key, None)

        for key in list(_webhook_service_down_since):
            if key not in valid_keys:
                _webhook_service_down_since.pop(key, None)
        for key in list(_webhook_service_last_sent):
            if key not in valid_keys:
                _webhook_service_last_sent.pop(key, None)

        if not messages_by_url:
            return

        _save_scheduler_state()

        for url, lines in messages_by_url.items():
            a = apprise.Apprise()
            if not a.add(url):
                continue
            a.notify(title="Vauxtra - Service alert", body="\n".join(lines))
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


def _fire_integration_webhook(changed: list[dict]) -> None:
    """Fire integration-status alerts to webhooks that opted into integration alerts."""
    try:
        integration_changed = [c for c in changed if "provider_id" in c]
        if not integration_changed:
            return

        down = [c for c in integration_changed if c["new"] == "error"]
        up   = [c for c in integration_changed if c["new"] == "ok"]
        if not down and not up:
            return

        conn = get_db()
        webhooks = conn.execute(
            """SELECT url, alert_on_integration_down, alert_on_integration_up,
                      scope_type, scope_ref_id
               FROM webhooks WHERE enabled=1
               AND (alert_on_integration_down=1 OR alert_on_integration_up=1)"""
        ).fetchall()
        conn.close()

        import apprise

        for wh in webhooks:
            scope_type = (wh["scope_type"] or "all").lower()
            scope_ref_id = wh["scope_ref_id"]
            scoped_down = down
            scoped_up = up
            if scope_type == "provider" and scope_ref_id:
                scoped_down = [c for c in down if c.get("provider_id") == scope_ref_id]
                scoped_up = [c for c in up if c.get("provider_id") == scope_ref_id]
            elif scope_type == "service":
                scoped_down = []
                scoped_up = []

            lines = []
            if wh["alert_on_integration_down"] and scoped_down:
                lines.append("Integration down: " + ", ".join(c["fqdn"] for c in scoped_down))
            if wh["alert_on_integration_up"] and scoped_up:
                lines.append("Integration recovered: " + ", ".join(c["fqdn"] for c in scoped_up))
            if not lines:
                continue
            a = apprise.Apprise()
            if not a.add(wh["url"]):
                continue
            a.notify(title="Vauxtra - Integration alert", body="\n".join(lines))
    except Exception:
        import traceback
        add_log("error", f"Integration webhook failed: {traceback.format_exc()}")


def _fire_reconcile_webhook(corrected: list[str], errors: list[str]) -> None:
    try:
        conn = get_db()
        webhooks = conn.execute(
            "SELECT url FROM webhooks WHERE enabled=1 AND alert_on_any_down=1"
        ).fetchall()
        conn.close()
        if not webhooks:
            return

        import apprise
        lines = [f"Auto-reconcile corrected {len(corrected)} service(s):"]
        lines.extend(f"  ✓ {fqdn}" for fqdn in corrected)
        if errors:
            lines.append(f"Errors ({len(errors)}):")
            lines.extend(f"  ✗ {e}" for e in errors)
        body = "\n".join(lines)

        for wh in webhooks:
            a = apprise.Apprise()
            if not a.add(wh["url"]):
                continue
            a.notify(title="Vauxtra: Auto-Reconcile", body=body)
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
