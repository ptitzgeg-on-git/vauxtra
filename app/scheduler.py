"""Automatic health check and reconcile scheduler."""
import datetime
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.background import BackgroundScheduler

from app.models import add_log, get_db
from app.providers.base import record_value_key
from app.providers.factory import PROVIDER_TYPES, certificate_provider_types, create_provider
from app.public_target import (
    detect_server_public_ip,
    load_public_target_policy,
    one_wan_detection_per_cycle,
    resolve_public_target,
)
from app.security import mask_secret_url

# UTC, like every other time-sensitive default: the host's local zone made interval
# jobs depend on wherever the container happened to run.
_scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
_lock      = threading.Lock()
_alert_down_since: dict[tuple[int, int], float] = {}
_alert_down_sent: set[tuple[int, int]] = set()
_provider_last_status: dict[int, str] = {}
_webhook_service_down_since: dict[tuple[int, int], float] = {}
_webhook_service_last_sent: dict[tuple[int, int], float] = {}
# Circuit-breaker: track consecutive failures per service for DNS auto-update
_dns_update_failures: dict[int, int] = {}
_DNS_FAILURE_THRESHOLD = 3  # Disable auto-update after this many consecutive failures
# Certificate expiry alert state: (provider_id, cert_id) → {"level", "alerted_at"}
_cert_alert_state: dict[tuple[int, int], dict] = {}
# Webhook retry backoff in seconds for successive failed attempts
_WEBHOOK_RETRY_BACKOFF = [60, 300, 1800, 7200, 86400]
# Upper bound on concurrent TCP probes. Run one after the other, thirty unreachable
# services at 3 s each outlasted the shortest (1 minute) health-check interval.
_PROBE_MAX_WORKERS = 16
# Stands in for a WAN lookup that already failed this cycle. `resolve_public_target`
# looks the WAN address up again whenever it is handed an empty one, so an outage cost
# one full round of resolvers per service, all of it under `_lock`. This value is not a
# valid hostname, so it is never offered as a candidate either.
_WAN_LOOKUP_FAILED = "!wan-lookup-failed"


def _utc_stamp(delay_seconds: int = 0) -> str:
    """UTC timestamp in SQLite's own format.

    `datetime('now')` renders "YYYY-MM-DD HH:MM:SS" with a space. The retry queue is
    polled with `next_retry_at <= datetime('now')`, a plain string comparison: an ISO
    "T" separator sorts above the space, so a due retry only ever became due the day
    after.
    """
    stamp = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=delay_seconds)
    return stamp.strftime("%Y-%m-%d %H:%M:%S")


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


# Anything below this is not a wall-clock timestamp. Vauxtra <= 1.1.0 persisted
# `time.monotonic()` values, whose origin is arbitrary per process: reloaded after a
# restart they made every elapsed time nonsensical, and the DOWN alerts never fired
# again. Such leftovers are dropped rather than trusted.
_EPOCH_SANITY_FLOOR = 1_000_000_000.0  # 2001-09-09


def _load_tuple_value_map(data) -> dict[tuple[int, int], float]:
    if not isinstance(data, dict):
        return {}
    result: dict[tuple[int, int], float] = {}
    for raw_key, value in data.items():
        key = _decode_tuple_key(raw_key)
        if key is None:
            continue
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if stamp < _EPOCH_SANITY_FLOOR:
            continue
        result[key] = stamp
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

    corrected: list[str] = []
    errors: list[str] = []

    # Under the scheduler lock, like the health checks: the two ran concurrently and both
    # write the providers and the services table. One WAN detection for the whole cycle
    # (N21), not two per service.
    #
    # M9: the lock is taken per service, not for the whole cycle. Each service costs one
    # `list_hosts()` per proxy and one DNS read per server, sequentially: held across all of
    # them, the lock made the health checks skip their turn and DOWN alerts arrive minutes
    # late. A health check now runs between two services.
    with one_wan_detection_per_cycle():
        conn = get_db()
        service_ids = [
            int(r["id"]) for r in conn.execute("SELECT id FROM services WHERE enabled=1")
        ]
        conn.close()

        for sid in service_ids:
            with _lock:
                _reconcile_one_service(sid, corrected, errors, _compute_service_drift, _execute_push)

    if corrected:
        _fire_reconcile_webhook(corrected, errors)


def _reconcile_one_service(sid: int, corrected: list, errors: list, compute_drift, execute_push) -> None:
    """One service of `run_auto_reconcile`, under the scheduler lock held by the caller."""
    fqdn = f"service {sid}"
    try:
        # Re-read right before the push: the list is a snapshot, and a service disabled or
        # edited meanwhile was pushed back as it had been. Closed in a finally: a drift
        # computation that raised used to leak the connection.
        conn = get_db()
        try:
            svc = conn.execute(
                "SELECT * FROM services WHERE id=? AND enabled=1", (sid,)
            ).fetchone()
            if svc is None:
                return
            fqdn  = f"{svc['subdomain']}.{svc['domain']}"
            drift = compute_drift(conn, svc, sid)
        finally:
            conn.close()

        if drift.get("ok"):
            return  # no drift, skip

        result = execute_push(svc, sid)
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


# ── TCP health check ──────────────────────────────────────────────────────

def _tcp_ok(ip: str, port: int) -> str:
    try:
        with socket.create_connection((ip, port), timeout=3):
            return "ok"
    except (OSError, ValueError):
        # ValueError covers UnicodeError: a malformed name (`a..b`, a label over 63
        # characters) fails IDNA encoding before any socket exists, and used to escape
        # here and stop the whole cycle.
        return "error"


def _probe_services(services) -> list[tuple]:
    """TCP-probe the services concurrently and return `(svc, status)` pairs in order.

    Only the network calls run in the pool; the rows are plain data and nothing here
    touches SQLite, whose connections must not cross threads.
    """
    if not services:
        return []
    workers = max(1, min(_PROBE_MAX_WORKERS, len(services)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vauxtra-probe") as pool:
        statuses = list(
            pool.map(lambda svc: _tcp_ok(svc["target_ip"], svc["target_port"]), services)
        )
    return list(zip(services, statuses, strict=True))


def _run_phase(name: str, func, conn=None):
    """Run one phase of the health cycle, logging a failure instead of raising it.

    The phases are independent: a provider API that throws, a malformed certificate date
    or a broken retry row used to abort the cycle, and every phase after it (the purge
    included) was skipped until the cause went away.

    With `conn`, the phase is committed when it returns. What a failed phase (or its
    commit) left uncommitted is rolled back first, so the log line below is not stuck
    behind its write lock and the next phase starts on a clean transaction.
    """
    try:
        result = func()
        if conn is not None:
            conn.commit()
        return result
    except Exception:
        import traceback
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        try:
            add_log("error", f"[Scheduler] {name} failed: {traceback.format_exc()}")
        except Exception:
            pass
        return None


# ── Job principal ─────────────────────────────────────────────────────────

def _sync_npm_once() -> None:
    """Pull NPM's enable/disable state onto our services, once per cycle.

    Its own connection, because it is its own transaction: it talks to NPM between two
    services and nothing else in the cycle needs to see, or wait for, what it writes.
    """
    try:
        from app.api.services import _sync_npm_statuses  # noqa: PLC0415
        conn = get_db()
        try:
            _sync_npm_statuses(conn)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _purge_history(conn) -> None:
    """Drop monitoring history, logs and settled webhook rows past their retention."""
    monitoring_retention_days = _read_retention_days(conn, "monitoring_retention_days", 14)
    log_retention_days = _read_retention_days(conn, "log_retention_days", 30)
    webhook_retry_retention_days = _read_retention_days(
        conn, "webhook_retry_retention_days", 7, min_days=1, max_days=90
    )
    conn.execute(
        "DELETE FROM uptime_events WHERE created_at < datetime('now', ?)",
        (f"-{monitoring_retention_days} days",),
    )
    conn.execute(
        "DELETE FROM logs WHERE created_at < datetime('now', ?)",
        (f"-{log_retention_days} days",),
    )
    try:
        conn.execute(
            """DELETE FROM webhook_delivery_log
               WHERE status IN ('delivered', 'failed', 'cancelled')
                 AND updated_at < datetime('now', ?)""",
            (f"-{webhook_retry_retention_days} days",),
        )
    except Exception:
        pass


def _retry_former_proxy_routes(conn) -> int:
    """Retry the removal of routes services left on proxies they no longer use (C3)."""
    from app.api.sync import retry_pending_proxy_removals  # noqa: PLC0415 - circular import

    return retry_pending_proxy_removals(conn)


def run_health_checks() -> None:
    """Check all services, record uptime events, and dispatch alerts.

    Every phase of this cycle talks to the network, and SQLite admits one writer at a
    time. A write transaction left open across an HTTP call therefore does not merely
    slow the panel down, it stops it: every other writer waits out `busy_timeout`
    (15 s) and then fails with "database is locked" -- which is why `_sync_npm_statuses`
    had grown a branch that swallows exactly that message.

    So the cycle never holds a transaction across a network call. The probes below run
    with no connection open at all, their results are written in one short burst, and
    each phase after that commits per item rather than per phase.
    """
    with _lock:
        _sync_npm_once()

        conn = get_db()
        try:
            services = conn.execute(
                "SELECT id, target_ip, target_port, subdomain, domain, status, expose_mode FROM services WHERE enabled=1"
            ).fetchall()
        finally:
            conn.close()

        # Probe with nothing open. A TCP probe against a host that is simply gone costs
        # the full 3 s timeout, and a panel can hold dozens of services: run between two
        # UPDATEs on one transaction, that alone was minutes of held write lock a cycle.
        #
        # Tunnel services are health-checked via the Cloudflare API, not TCP. Running TCP
        # against cfargotunnel.com or similar targets always fails.
        probes = _run_phase(
            "Service probes",
            lambda: _probe_services(
                [s for s in services if (s["expose_mode"] or "").strip().lower() != "tunnel"]
            ),
        ) or []

        changed: list[dict] = []
        conn = get_db()
        try:
            # Each phase on its own: one that raises is logged and rolled back, and the
            # ones after it still run.
            _run_phase(
                "Service status update",
                lambda: _record_probe_results(conn, probes, changed),
                conn,
            )
            _run_phase("DNS auto-update", lambda: _run_dns_auto_updates_once(conn), conn)
            changed.extend(
                _run_phase("Provider health", lambda: _run_provider_health_checks(conn), conn)
                or []
            )
            _run_phase("Certificate expiry", lambda: _run_cert_expiry_alerts(conn), conn)
            _run_phase("Former proxy routes", lambda: _retry_former_proxy_routes(conn), conn)
            _run_phase("Webhook retry", lambda: _run_webhook_retry(conn), conn)
            _run_phase("History purge", lambda: _purge_history(conn), conn)
        finally:
            conn.close()

        # Persist scheduler state after closing connection to avoid locks
        _run_phase("Scheduler state save", _save_scheduler_state)

        if changed:
            _run_phase("Global webhook", _fire_global_webhook)
        if any("provider_id" in c for c in changed):
            _run_phase("Integration webhook", lambda: _fire_integration_webhook(changed))
        _run_phase("Service webhooks", _fire_service_webhooks)


def _record_probe_results(conn, probes, changed: list[dict]) -> None:
    """Write the probe results in one short burst and collect the status transitions."""
    transitions: list[dict] = []
    for svc, new_status in probes:
        old_status = svc["status"] or "unknown"

        cur = conn.execute(
            "UPDATE services SET status=?, last_checked=datetime('now') WHERE id=?",
            (new_status, svc["id"]),
        )
        if cur.rowcount == 0:
            # Deleted while it was being probed. Its uptime event would violate the
            # foreign key, and that one failure rolled back every result of the round.
            continue
        conn.execute(
            """INSERT INTO uptime_events (service_id, status)
               SELECT ?, ? WHERE EXISTS (SELECT 1 FROM services WHERE id=?)""",
            (svc["id"], new_status, svc["id"]),
        )

        if old_status != new_status:
            fqdn = f"{svc['subdomain']}.{svc['domain']}"
            transitions.append(
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
    # One commit for the whole round of probes: these are writes only, with no network
    # between them, so the lock is held for as long as they take and no longer. Every
    # phase after this one commits per item instead, for the same reason.
    conn.commit()
    # Reported only once committed: a round rolled back must not announce transitions
    # that were never stored.
    changed.extend(transitions)


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

        # Per provider, not per loop: the next iteration reaches across the network to
        # somebody else's API, and a transaction still open while it does is a writer
        # every other caller has to wait out.
        conn.commit()

    for pid in list(_provider_last_status.keys()):
        if pid not in seen_ids:
            _provider_last_status.pop(pid, None)

    return changed


def _run_tunnel_health_checks(conn) -> list[dict]:
    """Backward-compatible wrapper for older callers/tests."""
    return _run_provider_health_checks(conn)


def _propagate_to_secondary_dns(conn, sid: int, fqdn: str) -> None:
    """H4: carry a new WAN address to the service's secondary DNS targets.

    Only the primary DNS provider was updated, and the reconcile does not correct it
    either (`dns_target_mismatch` is a warning): visitors resolving through a secondary
    server kept reaching the former address, which a dynamic IP hands to someone else.
    The push resolves the same target again; within the cycle the WAN lookup is cached.
    """
    extra_dns = {
        int(r["provider_id"])
        for r in conn.execute(
            "SELECT provider_id FROM service_push_targets WHERE service_id=? AND role='dns'",
            (sid,),
        )
    }
    if not extra_dns:
        _secondary_dns_pending.discard(sid)
        return
    conn.commit()
    from app.api.sync import _push_service_row  # noqa: PLC0415 - circular import

    try:
        svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if svc is None:
            _secondary_dns_pending.discard(sid)
            return
        failures = _push_service_row(conn, svc, sid, only_provider_ids=extra_dns)["errors"]
    except Exception as e:
        failures = [str(e)]
    # A secondary that missed the move is tried again on the next cycles: once the primary
    # holds the new address, nothing else would bring it here again, and the reconcile
    # only warns about the mismatch (`dns_target_mismatch`).
    if failures:
        _secondary_dns_pending.add(sid)
    else:
        _secondary_dns_pending.discard(sid)
    for message in failures:
        add_log("warn", f"[AutoDNS] {fqdn}: a secondary DNS target was not updated: {message}", conn)


# Services whose secondary DNS targets missed the last address move (H4). In memory: after
# a restart the reconcile's `dns_target_mismatch` warning still points at them.
_secondary_dns_pending: set[int] = set()


def _run_dns_auto_updates_once(conn) -> bool:
    """`_run_dns_auto_updates` with one WAN lookup for the primary and secondary targets."""
    with one_wan_detection_per_cycle():
        return _run_dns_auto_updates(conn)


def _run_dns_auto_updates(conn) -> bool:
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
        return False

    policy = load_public_target_policy(conn)
    # The WAN lookup and every provider call below are network round trips: none of them
    # may run with SQLite's single writer lock held by an earlier write on this connection.
    conn.commit()
    if "server_public_ip" in list(policy.get("priority") or []):
        server_public_ip = detect_server_public_ip(
            sources=policy["sources"],
            timeout_seconds=policy["timeout_seconds"],
        )
    else:
        # B15, as in `resolve_dns_target`: a source the operator removed from the priority
        # is not consulted at all, so the resolvers are not called every cycle for nothing.
        server_public_ip = _WAN_LOOKUP_FAILED
    if not server_public_ip:
        if list(policy.get("priority") or [])[:1] == ["server_public_ip"]:
            # C6: the WAN address is the preferred answer and is unknown for now. Falling
            # through to the next candidate published the proxy host (often a LAN address)
            # until the next cycle switched back: every name flapped. Keep what is there.
            return False
        # Remembered for the rest of the cycle: handed an empty address, every
        # `resolve_public_target` below ran the whole resolver list again, each at its
        # full timeout. The other candidates (proxy host, current value) still apply.
        server_public_ip = _WAN_LOOKUP_FAILED

    public_dns_ids = {
        r["id"]
        for r in conn.execute("SELECT id, type FROM providers").fetchall()
        if PROVIDER_TYPES.get(r["type"], {}).get("capabilities", {}).get("public_dns")
    }
    state_changed = False
    for svc in services:
        sid = int(svc["id"])
        fqdn = f"{svc['subdomain']}.{svc['domain']}"
        current_target = (svc["dns_ip"] or "").strip().lower()

        # H2: the same address goes to every DNS target of the service (the secondaries get
        # it through `_propagate_to_secondary_dns`), so it must suit the most exposed one,
        # as `resolve_dns_target` decides for the push. Judged on the primary alone, a LAN
        # primary got the proxy host, the push then gave a public secondary the WAN address
        # and stored it, and the next cycle moved the primary again: every cycle flapped.
        dns_ids = [svc["dns_provider_id"], *(
            r["provider_id"] for r in conn.execute(
                "SELECT provider_id FROM service_push_targets WHERE service_id=? AND role='dns'",
                (sid,),
            )
        )]
        resolved_target, target_source = resolve_public_target(
            conn,
            mode="auto",
            manual_value="",
            proxy_provider_id=svc["proxy_provider_id"],
            current_value=current_target,
            server_public_ip=server_public_ip,
            public_only=any(pid in public_dns_ids for pid in dns_ids),
        )
        if not resolved_target or record_value_key(resolved_target) == record_value_key(current_target):
            # Success (no update needed) - reset failure count
            if sid in _dns_update_failures:
                del _dns_update_failures[sid]
                state_changed = True
            if resolved_target and sid in _secondary_dns_pending:
                _propagate_to_secondary_dns(conn, sid, fqdn)
                conn.commit()
            continue

        row = conn.execute(
            "SELECT * FROM providers WHERE id=? AND enabled=1",
            (svc["dns_provider_id"],),
        ).fetchone()
        if not row:
            continue

        old_target = current_target or "(none)"
        # Released before this service's provider calls: the previous one's UPDATE and log
        # line would otherwise hold the writer lock across them.
        conn.commit()
        # Re-read right before the provider call: the list above was read before the WAN
        # lookup and every earlier service's API calls, and a service disabled, switched
        # to manual or retargeted meanwhile was still pushed as it had been.
        stored_target = svc["dns_ip"] or ""
        fresh = conn.execute(
            """SELECT id FROM services
               WHERE id=? AND enabled=1
                 AND COALESCE(auto_update_dns, 0)=1
                 AND COALESCE(public_target_mode, 'manual')='auto'
                 AND dns_provider_id IS ?
                 AND COALESCE(dns_ip, '')=?""",
            (sid, svc["dns_provider_id"], stored_target),
        ).fetchone()
        if fresh is None:
            continue
        try:
            dns = create_provider(row)
            # Every outcome is checked now. A refused write used to be followed by the UPDATE
            # below anyway, so the row named an address the provider never received.
            if not current_target:
                if not dns.add_rewrite(fqdn, resolved_target):
                    raise RuntimeError("the DNS provider refused the new record")
            elif not dns.update_rewrite(fqdn, current_target, fqdn, resolved_target):
                if not dns.add_rewrite(fqdn, resolved_target):
                    raise RuntimeError("the DNS provider refused the new record")
                if not dns.delete_rewrite(fqdn, current_target):
                    add_log(
                        "warn",
                        f"[AutoDNS] {fqdn}: the old record {current_target} could not be "
                        "removed and is still live beside the new one",
                        conn,
                    )
            # Conditional on the value the provider calls were based on: an edit saved while
            # they ran wins over this cycle's result instead of being overwritten by it.
            cur = conn.execute(
                "UPDATE services SET dns_ip=? WHERE id=? AND COALESCE(dns_ip, '')=?",
                (resolved_target, sid, stored_target),
            )
            if cur.rowcount == 0:
                add_log(
                    "warn",
                    f"[AutoDNS] {fqdn}: the service changed while its record was being "
                    f"updated to {resolved_target}; the saved target was left as it is",
                    conn,
                )
            else:
                add_log("info", f"[AutoDNS] {fqdn}: {old_target} → {resolved_target} ({target_source})", conn)
                _propagate_to_secondary_dns(conn, sid, fqdn)
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

        # Per service. The next iteration resolves a public target and then calls a DNS
        # provider's API, and the write above must not still be uncommitted while it does.
        conn.commit()

    # No _save_scheduler_state() here, the flag is returned instead. Scheduler state is
    # persisted once per cycle, by run_health_checks(), after the connection is closed --
    # one file written once beats one written per service, and a helper that never guesses
    # whether it owns the cycle cannot get the answer wrong.
    return state_changed


# ── Certificate expiry alerts ────────────────────────────────────────────

def _run_cert_expiry_alerts(conn) -> None:
    """Scan certificate-capable proxy providers for certificates close to expiry and log alerts.

    Alerts:  < 30 days → warn   |   < 7 days → error
    Re-alerts after 24 h or when the severity level changes.
    """
    global _cert_alert_state
    # The certificates page's own parser, so both read the same expiry: it accepts every
    # format the providers send and converts an offset to UTC rather than cutting it off.
    from app.api.certificates import _parse_expiry  # noqa: PLC0415

    try:
        cert_types = certificate_provider_types()
        if not cert_types:
            return
        placeholders = ",".join("?" * len(cert_types))
        cert_rows = conn.execute(
            f"SELECT * FROM providers WHERE type IN ({placeholders}) AND enabled=1",
            cert_types,
        ).fetchall()
        if not cert_rows:
            return

        now_utc = datetime.datetime.now(datetime.UTC)
        seen_keys: set[tuple[int, int]] = set()

        for prow in cert_rows:
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

                # Both sides aware UTC. Comparing an aware expiry with a naive "now" raised
                # TypeError, and the offset used to be cut off rather than applied.
                expires = _parse_expiry(expires_raw)
                if expires is None:
                    continue
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=datetime.UTC)
                else:
                    expires = expires.astimezone(datetime.UTC)

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

            # Per provider: fetching the next one's certificate list is an HTTPS round
            # trip, and the alerts just logged must not be holding the write lock for it.
            conn.commit()

        for key in list(_cert_alert_state):
            if key not in seen_keys:
                _cert_alert_state.pop(key, None)

    except Exception:
        import traceback
        add_log("error", f"[CertExpiry] Check failed: {traceback.format_exc()}")


# ── Webhook retry ─────────────────────────────────────────────────────────

def _local_target_allowed(webhook_id, conn) -> bool:
    """Whether a webhook was saved by an admin, so it may reach the local network.

    An ad-hoc send (no webhook row) is allowed, as before. Fails closed: an unreadable
    approval is no approval. Only a LAN target is refused, so an ordinary public webhook
    is still sent.
    """
    if webhook_id is None:
        return True
    try:
        if conn is not None:
            row = conn.execute(
                "SELECT local_target_allowed FROM webhooks WHERE id=?", (webhook_id,)
            ).fetchone()
        else:
            own = get_db()
            try:
                row = own.execute(
                    "SELECT local_target_allowed FROM webhooks WHERE id=?", (webhook_id,)
                ).fetchone()
            finally:
                own.close()
    except Exception:
        return False
    return row is None or row["local_target_allowed"] is None or bool(int(row["local_target_allowed"]))


def _log_local_refusal(url: str, conn) -> None:
    add_log(
        "error",
        f"[Webhook] Not sent: {mask_secret_url(url)} now points inside the local network "
        "and was not saved by an admin. Save it again with an admin session to allow it.",
        conn,
    )


def _local_send_refused(url: str, webhook_id, conn, apprise_obj) -> bool:
    """M11: True (and logged) when a webhook stored without `admin` now targets the LAN.

    The save-time check resolves the name once; a DNS answer that later moves onto a
    private address would otherwise turn the webhook into a standing request into the
    network. Checked against the hosts apprise will really connect to, on every send.
    H1: this only reads the URL; `refuse_local_connections` around `notify()` covers the
    redirects, discovery lookups and DNS answers that change after this check.
    """
    if _local_target_allowed(webhook_id, conn):
        return False
    from app.webhook_targets import targets_the_local_network

    if not targets_the_local_network(url, apprise_obj):
        return False
    _log_local_refusal(url, conn)
    return True


def _try_send_apprise(url: str, title: str, body: str, conn=None, webhook_id=None) -> bool:
    """Send a notification via Apprise, queueing it for retry when the send fails.

    Returns True when the notification was delivered **or** safely queued in
    `webhook_delivery_log`, and False only when the URL itself is unusable, in which
    case nothing was sent and nothing will ever be retried. Callers that hold a
    "already alerted" flag must clear it on False, or the alert stays silent forever.

    `conn` is optional: the callers that notify run outside the health-check
    transaction and hold no connection.
    """
    import apprise as _apprise
    a = _apprise.Apprise()
    # The URL carries the token. `add_log` writes straight into the `logs` table, which
    # `GET /api/logs` and its SSE stream hand to any key -- masking here is what keeps a
    # transient Discord outage from persisting the secret in normal operation.
    safe_url = mask_secret_url(url)
    if not a.add(url):
        add_log("error", f"[Webhook] Unusable notification URL, nothing sent: {safe_url}")
        return False
    if _local_send_refused(url, webhook_id, conn, a):
        return False
    from app.webhook_targets import refuse_local_connections

    guarded = not _local_target_allowed(webhook_id, conn)
    try:
        with refuse_local_connections(guarded, a) as guard:
            ok = bool(a.notify(title=title, body=body))
        if guard.refused:
            # H1: a redirect, a discovery lookup or a fresh DNS answer led inside the
            # network. Not queued: a retry would only be refused again.
            _log_local_refusal(url, conn)
            return False
        if not ok:
            raise RuntimeError("Apprise.notify() returned False")
        return True
    except Exception as exc:
        queued = False
        try:
            delay = _WEBHOOK_RETRY_BACKOFF[0]
            next_retry = _utc_stamp(delay)
            sql = """INSERT INTO webhook_delivery_log
                     (webhook_id, url, title, body, status, attempt, next_retry_at, error_msg)
                     VALUES (?,?,?,?,?,?,?,?)"""
            params = (webhook_id, url, title, body, "pending", 1, next_retry, str(exc))
            if conn is not None:
                conn.execute(sql, params)
            else:
                own = get_db()
                try:
                    own.execute(sql, params)
                    own.commit()
                finally:
                    own.close()
            queued = True
        except Exception:
            import traceback
            add_log("error", f"[Webhook] Could not queue a retry: {traceback.format_exc()}")
        add_log("warning", f"[Webhook] Delivery failed for {safe_url}: {exc}")
        return queued


def _run_webhook_retry(conn) -> None:
    """Retry pending webhook deliveries that are due, applying exponential backoff."""

    import apprise as _apprise

    from app.webhook_targets import refuse_local_connections

    # +1 because attempt 1 is the original send, already counted when the row was
    # queued: without it the last backoff tier (24 h) was never reachable.
    MAX_ATTEMPTS = len(_WEBHOOK_RETRY_BACKOFF) + 1
    try:
        # Settled for good when their webhook is switched off or gone: the query below
        # skips them, so they used to sit in 'pending' forever, never sent and never
        # purged. 'cancelled' is terminal, and purged like 'delivered' and 'failed'.
        # An ad-hoc send (NULL webhook_id) has no parent to lose and is left alone.
        conn.execute(
            """UPDATE webhook_delivery_log
               SET status='cancelled', updated_at=datetime('now')
               WHERE status='pending'
                 AND webhook_id IS NOT NULL
                 AND webhook_id NOT IN (SELECT id FROM webhooks WHERE enabled=1)"""
        )
        conn.commit()
    except Exception:
        import traceback
        conn.rollback()
        add_log("error", f"[Webhook] Could not cancel orphaned retries: {traceback.format_exc()}")
    try:
        rows = conn.execute(
            # Joined on the webhook as it is now: a webhook switched off kept receiving the
            # retries queued before, and an edited URL kept getting the old one. An ad-hoc
            # send (NULL webhook_id) has no webhook to follow and keeps its own URL.
            """SELECT d.id, d.webhook_id, COALESCE(w.url, d.url) AS url, d.title, d.body,
                      d.attempt
               FROM webhook_delivery_log d
               LEFT JOIN webhooks w ON w.id = d.webhook_id
               WHERE d.status='pending' AND d.next_retry_at <= datetime('now')
                 AND (d.webhook_id IS NULL OR w.enabled = 1)
               ORDER BY d.next_retry_at
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
            add_log(
                "error",
                f"[Webhook] Delivery abandoned after {attempt} attempts: "
                f"{mask_secret_url(row['url'])}",
                conn,
            )
            conn.commit()
            continue

        a = _apprise.Apprise()
        if not a.add(row["url"]) or _local_send_refused(row["url"], row["webhook_id"], conn, a):
            conn.execute(
                "UPDATE webhook_delivery_log SET status='failed', updated_at=datetime('now') WHERE id=?",
                (dlid,),
            )
            conn.commit()
            continue

        try:
            with refuse_local_connections(
                not _local_target_allowed(row["webhook_id"], conn), a
            ) as guard:
                result = a.notify(title=row["title"] or "", body=row["body"] or "")
            if guard.refused:
                _log_local_refusal(row["url"], conn)
                conn.execute(
                    "UPDATE webhook_delivery_log SET status='failed', updated_at=datetime('now') WHERE id=?",
                    (dlid,),
                )
                conn.commit()
                continue
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
                add_log("error", f"[Webhook] Delivery abandoned: {mask_secret_url(row['url'])}", conn)
            else:
                delay = _WEBHOOK_RETRY_BACKOFF[new_attempt - 1]
                next_retry = _utc_stamp(delay)
                conn.execute(
                    """UPDATE webhook_delivery_log
                       SET attempt=?, next_retry_at=?, error_msg=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (new_attempt, next_retry, str(exc), dlid),
                )

        # Per row. Up to twenty of these run in a cycle, each one an HTTP POST to somebody
        # else's endpoint; one transaction around the lot held the write lock for the sum
        # of them, and lost every outcome in it if the process went down in the middle.
        conn.commit()


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

        # Wall clock, not monotonic: this map is persisted and reloaded across restarts.
        now = time.time()
        messages_by_url: dict[str, list[str]] = {}
        # Which "already alerted" flags each URL carries, so they can be released if the
        # URL turns out to be unusable.
        keys_by_url: dict[str, list[tuple[int, int]]] = {}
        webhook_id_by_url: dict[str, int] = {}
        valid_keys: set[tuple[int, int]] = set()

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
                    keys_by_url.setdefault(row["url"], []).append(key)
                    webhook_id_by_url.setdefault(row["url"], int(row["webhook_id"]))
                    _webhook_service_last_sent[key] = now
            else:
                had_down = key in _webhook_service_down_since or key in _webhook_service_last_sent
                if had_down and status == "ok" and bool(row["alert_on_any_up"]):
                    messages_by_url.setdefault(row["url"], []).append(f"RECOVERED: {fqdn}")
                    # Tied to its webhook like a DOWN, so a retry follows it if it is
                    # switched off, instead of being queued as a parentless send.
                    webhook_id_by_url.setdefault(row["url"], int(row["webhook_id"]))
                _webhook_service_down_since.pop(key, None)
                _webhook_service_last_sent.pop(key, None)

        for key in list(_webhook_service_down_since):
            if key not in valid_keys:
                _webhook_service_down_since.pop(key, None)
        for key in list(_webhook_service_last_sent):
            if key not in valid_keys:
                _webhook_service_last_sent.pop(key, None)

        if not messages_by_url:
            _save_scheduler_state()
            return

        for url, lines in messages_by_url.items():
            handled = _try_send_apprise(
                url, "Vauxtra - Service alert", "\n".join(lines),
                webhook_id=webhook_id_by_url.get(url),
            )
            if not handled:
                # Unusable URL: nothing left and nothing is queued for retry. Releasing
                # the flags lets the next cycle try again instead of staying silent.
                for key in keys_by_url.get(url, ()):
                    _webhook_service_last_sent.pop(key, None)

        # Persisted after sending, so the flags describe what actually went out.
        _save_scheduler_state()
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

        # Wall clock, not monotonic: see _load_tuple_value_map.
        now = time.time()
        valid_keys: set[tuple[int, int]] = set()
        messages_by_url: dict[str, list[str]] = {}
        keys_by_url: dict[str, list[tuple[int, int]]] = {}
        webhook_id_by_url: dict[str, int] = {}

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
                    keys_by_url.setdefault(row["webhook_url"], []).append(key)
                    webhook_id_by_url.setdefault(row["webhook_url"], int(row["webhook_id"]))
                    _alert_down_sent.add(key)
            else:
                had_down = key in _alert_down_since or key in _alert_down_sent
                if had_down and status == "ok" and on_up:
                    messages_by_url.setdefault(row["webhook_url"], []).append(
                        f"RECOVERED: {fqdn}"
                    )
                    webhook_id_by_url.setdefault(row["webhook_url"], int(row["webhook_id"]))
                _alert_down_since.pop(key, None)
                _alert_down_sent.discard(key)

        # Cleanup stale state for deleted/disabled alert rules.
        for key in list(_alert_down_since):
            if key not in valid_keys:
                _alert_down_since.pop(key, None)
        for key in list(_alert_down_sent):
            if key not in valid_keys:
                _alert_down_sent.discard(key)

        if not messages_by_url:
            _save_scheduler_state()
            return

        for url, lines in messages_by_url.items():
            handled = _try_send_apprise(
                url, "Vauxtra - Service alert", "\n".join(lines),
                webhook_id=webhook_id_by_url.get(url),
            )
            if not handled:
                # `_alert_down_sent` is a one-shot latch: leaving a key in it after a
                # send that never happened and is not queued silences that service for
                # good.
                for key in keys_by_url.get(url, ()):
                    _alert_down_sent.discard(key)

        # Persist state to database to survive restarts -- after sending, so the latch
        # reflects what actually went out.
        _save_scheduler_state()
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
            """SELECT id, url, alert_on_integration_down, alert_on_integration_up,
                      scope_type, scope_ref_id
               FROM webhooks WHERE enabled=1
               AND (alert_on_integration_down=1 OR alert_on_integration_up=1)"""
        ).fetchall()
        conn.close()

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
            _try_send_apprise(
                wh["url"], "Vauxtra - Integration alert", "\n".join(lines),
                webhook_id=wh["id"],
            )
    except Exception:
        import traceback
        add_log("error", f"Integration webhook failed: {traceback.format_exc()}")


def _fire_reconcile_webhook(corrected: list[str], errors: list[str]) -> None:
    try:
        conn = get_db()
        webhooks = conn.execute(
            "SELECT id, url, scope_type, scope_ref_id FROM webhooks "
            "WHERE enabled=1 AND alert_on_any_down=1"
        ).fetchall()
        services = conn.execute(
            """SELECT id AS service_id, subdomain, domain, dns_provider_id,
                      proxy_provider_id, tunnel_provider_id
               FROM services"""
        ).fetchall()
        extra_rows = conn.execute(
            "SELECT service_id, provider_id FROM service_push_targets"
        ).fetchall()
        conn.close()
        if not webhooks:
            return

        extra_target_map: dict[int, set[int]] = {}
        for extra in extra_rows:
            extra_target_map.setdefault(int(extra["service_id"]), set()).add(int(extra["provider_id"]))
        by_fqdn = {f"{s['subdomain']}.{s['domain']}".lower(): dict(s) for s in services}

        for wh in webhooks:
            # The report went to every webhook, a webhook scoped to one service or one
            # provider included. Each now gets the services its scope covers.
            if (wh["scope_type"] or "all").lower() != "all":
                covered = {
                    fqdn for fqdn, svc in by_fqdn.items()
                    if _service_matches_scope(
                        {**svc, "scope_type": wh["scope_type"], "scope_ref_id": wh["scope_ref_id"]},
                        extra_target_map,
                    )
                }
                mine = [f for f in corrected if str(f).lower() in covered]
                # Errors read "<fqdn>: <detail>"; a scoped webhook gets those of its services.
                my_errors = [
                    e for e in errors if str(e).split(":", 1)[0].strip().lower() in covered
                ]
            else:
                mine, my_errors = list(corrected), list(errors)
            if not mine and not my_errors:
                continue

            lines = [f"Auto-reconcile corrected {len(mine)} service(s):"]
            lines.extend(f"  ✓ {fqdn}" for fqdn in mine)
            if my_errors:
                lines.append(f"Errors ({len(my_errors)}):")
                lines.extend(f"  ✗ {e}" for e in my_errors)
            _try_send_apprise(
                wh["url"], "Vauxtra: Auto-Reconcile", "\n".join(lines), webhook_id=wh["id"]
            )
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
