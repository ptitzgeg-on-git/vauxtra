import asyncio
import json
import logging
import re
import secrets
import threading
import weakref

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.auth import _get_auth_context, get_session, require_auth
from app.limiter import limiter
from app.models import add_log, ensure_default_docker_endpoint, get_db, normalise_log_level
from app.security import mask_secret_url
from app.validators import is_valid_domain, normalize_domain
from app.webhook_targets import refuse_local_connections, targets_the_local_network

try:
    from sse_starlette.sse import EventSourceResponse as _SSEResponse
    _HAS_SSE = True
except ImportError:
    _SSEResponse = None
    _HAS_SSE = False

router = APIRouter()
logger = logging.getLogger(__name__)

_VALID_SETTINGS = {
    "theme",
    "timezone",
    "webhook_url",
    "webhook_enabled",
    "check_interval",
    "log_retention_days",
    "monitoring_retention_days",
    "public_target_sources",
    "public_target_timeout",
    "public_target_priority",
    # Read by `app/scheduler.py` since 1.1.0, and until now writable by nobody: the
    # auto-reconcile job could not be switched on, and `CHANGELOG.md` advertised the webhook
    # retention as "configurable via `webhook_retry_retention_days`" while the key was
    # dropped by `save_settings` on the way in.
    "auto_reconcile_enabled",
    "auto_reconcile_interval",
    "webhook_retry_retention_days",
}
_VALID_THEMES   = {"light", "dark"}
_VALID_PUBLIC_TARGET_PRIORITY = {"server_public_ip", "proxy_provider_host", "current"}

# Whole-number settings, with the range each one is allowed to hold. `check_interval` is the
# one that mattered: it is read back with a bare `int()` at startup (`app/main.py`), so any
# `write`-scoped caller writing "later" there kept the application from booting again --
# a denial of service that survived every restart.
_SETTING_RANGES = {
    "check_interval": (0, 1440),            # 0 disables automatic health checks
    "log_retention_days": (1, 365),
    "monitoring_retention_days": (1, 365),
    "auto_reconcile_interval": (0, 1440),   # 0 disables auto-reconcile too
    "webhook_retry_retention_days": (1, 90),  # the bounds `_read_retention_days` clamps to
}

# `timezone` is read by the frontend (`hooks/useFormat.ts`) and by nothing on this side: the
# server stores UTC and this decides how it is rendered. `TZ`, the container variable, is
# the separate one -- it sets the clock the scheduler and the log lines are written from.
# The value is validated for shape rather than against the IANA database on purpose:
# `zoneinfo` needs the `tzdata` package on Windows, and a missing package would otherwise
# reject every value there.
_TIMEZONE_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9+_-]*(?:/[A-Za-z0-9+_.-]+){0,2}$")

_BOOLEAN_WORDS = {"true", "false", "1", "0", "yes", "no", "on", "off"}

# The `settings` table also stores a server-side secret (the admin password hash) and internal
# bookkeeping. Reads must go through this whitelist so an API key -- `read` by default -- never
# walks away with a hash it can crack offline.
_READABLE_SETTINGS = _VALID_SETTINGS | {"schema_version", "setup_completed"}

# Readable, but never in full: `webhook_url` is an Apprise URL, so its value is the
# credential. The key stays in the response -- the operator needs to know one is set --
# with everything past the scheme removed.
_MASKED_SETTINGS = {"webhook_url"}

# Keys that must survive a reset or a restore: wiping the password hash would drop the instance
# back to anonymous-admin, and importing one would let a backup file pick the admin password.
# `auth_mode` belongs here for the same reason -- it is what makes that drop *visible*.
# `session_epoch` is here for a subtler reason than the rest: it is a counter that only
# ever goes up, and deleting it sends it back to 0 -- which is the epoch every cookie
# minted before the last password change is still carrying. A reset would have handed
# those sessions back their access.
# `app_password_env_fingerprint` follows from it: wiped, the next boot finds no stored
# fingerprint, records the current one as the "first" and revokes nothing, so a change of
# `APP_PASSWORD` made around a reset or a restore left every older session alive.
_PROTECTED_SETTINGS = (
    "app_password_hash",
    "setup_completed",
    "schema_version",
    "auth_mode",
    "session_epoch",
    "app_password_env_fingerprint",
)

# The placeholders are built from the tuple, never written out by hand: the two DELETE
# statements below used a literal `(?,?,?)`, so adding this fourth key would have raised
# "Incorrect number of bindings" at the exact moment an operator asked for a reset.
_PROTECTED_PLACEHOLDERS = ",".join("?" * len(_PROTECTED_SETTINGS))


# `write` means "may change how this instance is configured". This key means something
# else: it names URLs the instance itself then fetches, on a schedule, from inside the
# network it is deployed in -- and `save_settings` asked for `write` and nothing more, so a
# key minted for a monitoring dashboard could aim the scheduler at an internal admin panel
# or a cloud metadata endpoint. The other two WAN-policy keys stay at `write`: a timeout is
# a number, and a priority is one of three fixed words.
_ADMIN_ONLY_SETTINGS = frozenset({"public_target_sources"})


def _is_valid_public_target_sources(value: str) -> bool:
    entries = [line.strip() for line in str(value).replace(",", "\n").splitlines() if line.strip()]
    if not entries:
        return False
    # B14: https only. The answer is written into public DNS; over plain http anyone on the
    # path could choose it.
    return all(entry.lower().startswith("https://") for entry in entries)


def _is_valid_public_target_priority(value: str) -> bool:
    items = [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]
    if not items:
        return False
    return all(item in _VALID_PUBLIC_TARGET_PRIORITY for item in items)


def _validate_setting(key: str, value) -> tuple[str | None, str]:
    """Return `(value to store, "")`, or `(None, reason)` when the value is not acceptable.

    Every branch here used to be a bare `continue` in the write loop, followed by
    `{"ok": true}`: the operator got a "Saved" toast and the previous value, with nothing
    naming the field that was thrown away.
    """
    text = str(value).strip() if value is not None else ""

    if key in _SETTING_RANGES:
        low, high = _SETTING_RANGES[key]
        try:
            number = int(text)
        except (TypeError, ValueError):
            return None, f"expected a whole number between {low} and {high}, got {text!r}"
        if not low <= number <= high:
            return None, f"expected a whole number between {low} and {high}, got {number}"
        return str(number), ""

    if key == "theme":
        if text not in _VALID_THEMES:
            return None, f"expected one of {sorted(_VALID_THEMES)}"
        return text, ""

    if key == "timezone":
        if not _TIMEZONE_SHAPE.match(text):
            return None, "expected an IANA name such as UTC or Europe/Paris"
        return text, ""

    if key == "auto_reconcile_enabled":
        if text.lower() not in _BOOLEAN_WORDS:
            return None, "expected true or false"
        return "true" if text.lower() in {"true", "1", "yes", "on"} else "false", ""

    if key in {"webhook_url", "webhook_enabled"}:
        # `GET /api/settings` masks the URL, and an agent that reads the settings and posts
        # them back would otherwise store `discord://***` as the notification target: the
        # alerting would keep reporting itself configured, and reach nobody. That message
        # comes first because it names a mistake worth naming.
        if "***" in text:
            return None, (
                "that is the masked form the API returns, not a usable URL -- "
                "retype the full one, or leave the key out to keep the stored value"
            )
        # Neither key has fed delivery since the `webhooks` table arrived: the schedulers
        # read `webhooks.url`. Writing here used to look like configuring alerting while
        # configuring nothing. `_migrate_legacy_webhook_url` moves any stored value across
        # at the next start; new ones go to the table directly.
        return None, (
            "the global notification URL is retired -- create a target with "
            "POST /api/webhooks, which is what alert delivery reads"
        )

    if key == "public_target_sources":
        if not _is_valid_public_target_sources(text):
            return None, "expected one https:// URL per line"
        return text, ""

    if key == "public_target_priority":
        if not _is_valid_public_target_priority(text):
            return None, f"expected a comma-separated list of {sorted(_VALID_PUBLIC_TARGET_PRIORITY)}"
        return text, ""

    if key == "public_target_timeout":
        try:
            timeout = float(text)
        except (TypeError, ValueError):
            return None, f"expected a number of seconds between 0.5 and 10.0, got {text!r}"
        if not 0.5 <= timeout <= 10.0:
            return None, f"expected a number of seconds between 0.5 and 10.0, got {timeout}"
        return str(timeout), ""

    return text, ""


@router.get("/api/settings")
def get_settings(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {
        r["key"]: (mask_secret_url(r["value"]) if r["key"] in _MASKED_SETTINGS else r["value"])
        for r in rows
        if r["key"] in _READABLE_SETTINGS
    }


@router.post("/api/settings")
def save_settings(request: Request, body: dict):
    require_auth(request, scope="write")
    # Before validation, so the answer is a single 403 rather than a payload half refused
    # for scope and half for syntax. The route is already all-or-nothing; this keeps it so.
    if _ADMIN_ONLY_SETTINGS & set(body):
        require_auth(request, scope="admin")

    accepted: dict[str, str] = {}
    rejected: dict[str, str] = {}
    ignored: list[str] = []

    for key, value in body.items():
        if key not in _VALID_SETTINGS:
            # Not an error: `GET /api/settings` also returns `schema_version` and
            # `setup_completed`, so a caller that reads the settings and posts them back --
            # the MCP bridge does exactly that -- hands over keys nobody may write. They are
            # dropped, but the answer says which, instead of pretending they were saved.
            ignored.append(key)
            continue
        stored, reason = _validate_setting(key, value)
        if stored is None:
            rejected[key] = reason
        else:
            accepted[key] = stored

    if rejected:
        # Nothing is written when part of the payload is bad. A settings form applied by
        # halves is harder to reason about than one that was refused outright, and the
        # operator now learns which field, and why.
        detail = "; ".join(f"{k}: {reason}" for k, reason in sorted(rejected.items()))
        raise HTTPException(400, f"Nothing was saved -- {detail}")

    conn = get_db()
    for key, stored in accepted.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, stored),
        )
    conn.commit()
    conn.close()
    if "check_interval" in accepted:
        try:
            from app.scheduler import configure
            configure(int(accepted["check_interval"]))
        except (ImportError, TypeError, ValueError):
            pass

    if {"auto_reconcile_enabled", "auto_reconcile_interval"} & set(accepted):
        # Applied now rather than at the next restart, the same way `check_interval` is.
        # Either key can move alone, so the other is read back from the database instead of
        # being assumed unchanged.
        try:
            from app.scheduler import configure_reconcile
            conn = get_db()
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key IN "
                "('auto_reconcile_enabled', 'auto_reconcile_interval')"
            ).fetchall()
            conn.close()
            cfg = {r["key"]: r["value"] for r in rows}
            configure_reconcile(
                cfg.get("auto_reconcile_enabled") == "true",
                int(cfg.get("auto_reconcile_interval") or 0),
            )
        except (ImportError, TypeError, ValueError):
            pass

    return {"ok": True, "saved": sorted(accepted), "ignored": sorted(ignored)}


@router.get("/api/stats")
def get_stats(request: Request):
    require_auth(request)
    conn  = get_db()
    stats = {
        "services":       conn.execute("SELECT COUNT(*) FROM services").fetchone()[0],
        "providers":      conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0],
        "logs":           conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0],
        "services_ok":    conn.execute("SELECT COUNT(*) FROM services WHERE status='ok'").fetchone()[0],
        "services_error": conn.execute("SELECT COUNT(*) FROM services WHERE status='error'").fetchone()[0],
        "tags":           conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
    }
    conn.close()
    return stats


@router.get("/api/logs")
def get_logs(request: Request, page: int = 1, per_page: int = 50, level: str = ""):
    require_auth(request)
    page     = max(1, page)
    per_page = min(200, max(1, per_page))
    offset   = (page - 1) * per_page
    conn     = get_db()

    if level:
        # Rows written before `add_log` normalised the level still say "warn"; asking for
        # "warning" has to find them, or the filter hides ten rows out of twelve.
        wanted = {normalise_log_level(level), (level or "").strip().lower()}
        if "warning" in wanted:
            wanted.add("warn")
        values = sorted(wanted)
        marks  = ",".join("?" * len(values))
        total = conn.execute(
            f"SELECT COUNT(*) FROM logs WHERE level IN ({marks})", values
        ).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM logs WHERE level IN ({marks}) ORDER BY id DESC LIMIT ? OFFSET ?",
            (*values, per_page, offset),
        ).fetchall()
    else:
        total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        rows  = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()

    conn.close()
    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    max(1, -(-total // per_page)),
        "items":    [dict(r) for r in rows],
    }


@router.post("/api/logs/clear")
def clear_logs(request: Request):
    # The journal is the audit trail: a `write` key that could empty it could also erase
    # the record of what it had just done.
    require_auth(request, scope="admin")
    conn = get_db()
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    add_log("info", "Logs cleared")
    return {"ok": True}


@router.post("/api/settings/test-webhook")
@limiter.limit("10/minute")
def test_webhook(request: Request):
    """Send a test notification to every enabled webhook.

    This used to read `settings.webhook_url`, a key nothing delivered through: it answered
    `{"ok": true}` from a target that would never carry a real alert. It now exercises the
    rows the schedulers actually read, so a success here means alerting works.

    `write`, like every other test-send route: this delivers a real notification to whatever
    the operator configured, which is a side effect outside Vauxtra.
    """
    require_auth(request, scope="write")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, url, local_target_allowed FROM webhooks WHERE enabled=1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(400, "No enabled webhook configured -- create one with POST /api/webhooks")

    try:
        import apprise
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed — rebuild the Docker image")

    results = []
    for row in rows:
        entry = {"id": row["id"], "name": row["name"], "ok": False, "error": ""}
        try:
            a = apprise.Apprise()
            if not a.add(row["url"]):
                entry["error"] = "invalid or unrecognized Apprise URL"
            elif not int(row["local_target_allowed"]) and targets_the_local_network(row["url"], a):
                # M11: same refusal as the scheduler's senders.
                entry["error"] = "points inside the local network; an admin must save it again"
            else:
                # H1: redirects, discovery lookups and changed DNS answers, checked on
                # the connection itself.
                with refuse_local_connections(not int(row["local_target_allowed"]), a) as guard:
                    sent = a.notify(
                        title="Vauxtra — Test",
                        body="✓ Test notification — configuration is working correctly.",
                    )
                if guard.refused:
                    entry["error"] = "points inside the local network; an admin must save it again"
                elif sent:
                    entry["ok"] = True
                else:
                    entry["error"] = "send failed (service unavailable, or the URL is wrong)"
        except Exception as e:  # one bad target must not hide the state of the others
            # The exception text can carry the target URL and its token, or say whether
            # some internal host answered: it goes to the server log, not to the caller.
            logger.warning("Test notification to webhook %s failed: %s", row["id"], type(e).__name__)
            entry["error"] = "send failed; see the server log"
        results.append(entry)

    # 200 either way: a per-target report says more than a single failed status, and the
    # caller can see which one is broken instead of guessing.
    return {"ok": all(r["ok"] for r in results), "results": results}


@router.post("/api/reset")
def reset_all(request: Request):
    require_auth(request, scope="admin")
    conn = get_db()
    # Five tables used to survive this: `docker_endpoints` kept a Docker host and its TLS
    # material, `service_templates` kept provider ids pointing at deleted rows,
    # `webhook_delivery_log` kept queued notifications addressed to deleted webhooks --
    # which the retry job would then try to send -- and `scheduler_state` kept the alert
    # bookkeeping of services that no longer exist. A reset that leaves credentials and a
    # send queue behind is not the reset the button offers.
    conn.executescript("""
        DELETE FROM service_alerts;
        DELETE FROM service_tags;
        DELETE FROM service_environments;
        DELETE FROM service_push_targets;
        DELETE FROM uptime_events;
        DELETE FROM services;
        DELETE FROM webhook_delivery_log;
        DELETE FROM webhooks;
        DELETE FROM service_templates;
        DELETE FROM pending_proxy_removals;
        DELETE FROM docker_endpoints;
        DELETE FROM providers;
        DELETE FROM tags;
        DELETE FROM environments;
        DELETE FROM domains;
        DELETE FROM scheduler_state;
        DELETE FROM logs;
    """)
    # Reset the business data, never the credentials: deleting `app_password_hash` would leave
    # the instance answering anonymously with admin scope, and nothing in the {"ok": true}
    # response would say so.
    conn.execute(
        f"DELETE FROM settings WHERE key NOT IN ({_PROTECTED_PLACEHOLDERS})",  # noqa: S608
        _PROTECTED_SETTINGS,
    )
    # `docker_endpoints` was just emptied, and it is normally seeded at startup: without this
    # the instance would sit with no Docker host at all until someone restarted it.
    ensure_default_docker_endpoint(conn)
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/domains")
def list_domains(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute("SELECT name FROM domains ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


@router.post("/api/domains", status_code=201)
def add_domain(request: Request, body: dict):
    require_auth(request, scope="write")
    name = normalize_domain(body.get("name", ""))
    if not is_valid_domain(name, require_dot=True):
        raise HTTPException(400, "Invalid domain name")
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return {"name": name}


@router.delete("/api/domains/{name:path}")
def delete_domain(name: str, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    conn.execute("DELETE FROM domains WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return {"ok": True}


# Open log streams, capped. Each one polls SQLite every two seconds for as long as its
# client stays connected, so the number of them is the number of pollers.
_MAX_LOG_STREAMS = 10
_open_log_streams = 0
# Per caller as well: with only the global cap, one `read` key holding ten streams kept the
# administrator from opening the log view. Admin callers may go past the global cap (never
# past their own), so no lower-scoped key can lock them out.
_MAX_LOG_STREAMS_PER_IDENTITY = 5
_log_streams_by_identity: dict[str, int] = {}
_log_streams_lock = threading.Lock()


def _stream_identity(request: Request) -> tuple[str, bool]:
    """(who is asking, whether they hold admin) for the stream caps."""
    try:
        ctx = _get_auth_context(request)
    except Exception:  # an identity we cannot read shares one anonymous budget
        ctx = None
    if not ctx:
        return "unknown", False
    is_admin = "admin" in (ctx.get("scopes") or [])
    if ctx.get("kind") == "api_key":
        return f"key:{ctx.get('key_id')}", is_admin
    if ctx.get("kind") == "session":
        # B10: every signed-in browser used to share the one identity "session", so five
        # tabs anywhere (or the streams a dropped connection had not released yet) made the
        # next EventSource get a 429, after which it does not retry. Each login gets its own
        # budget, kept in the signed session cookie.
        session = get_session(request)
        stream_id = session.get("stream_id")
        if not isinstance(stream_id, str) or not stream_id:
            stream_id = secrets.token_hex(8)
            session["stream_id"] = stream_id
        return f"session:{stream_id}", is_admin
    return str(ctx.get("kind") or "ui"), is_admin


def _reserve_log_stream(identity: str, is_admin: bool) -> bool:
    """Count the stream in before the response exists, so a burst cannot overshoot."""
    global _open_log_streams
    with _log_streams_lock:
        if _log_streams_by_identity.get(identity, 0) >= _MAX_LOG_STREAMS_PER_IDENTITY:
            return False
        if _open_log_streams >= _MAX_LOG_STREAMS and not is_admin:
            return False
        _open_log_streams += 1
        _log_streams_by_identity[identity] = _log_streams_by_identity.get(identity, 0) + 1
        return True


def _release_log_stream(identity: str) -> None:
    global _open_log_streams
    with _log_streams_lock:
        _open_log_streams = max(0, _open_log_streams - 1)
        left = _log_streams_by_identity.get(identity, 0) - 1
        if left > 0:
            _log_streams_by_identity[identity] = left
        else:
            _log_streams_by_identity.pop(identity, None)


def _max_log_id() -> int:
    conn = get_db()
    try:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
    finally:
        conn.close()


def _logs_after(last_id: int) -> list[dict]:
    conn = get_db()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, level, message, created_at FROM logs "
                "WHERE id > ? ORDER BY id ASC LIMIT 50",
                (last_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


@router.get("/api/logs/stream")
async def stream_logs(request: Request):
    """Server-Sent Events stream: pushes new log rows every 2 s."""
    # Every SQLite access below goes through the threadpool. They ran on the event loop, so
    # one busy database (a restore holds an exclusive lock) froze every request of the
    # instance for up to `busy_timeout`, not only this stream.
    await run_in_threadpool(require_auth, request)
    if not _HAS_SSE:
        raise HTTPException(500, "Package 'sse-starlette' not installed — rebuild the Docker image.")
    identity, is_admin = await run_in_threadpool(_stream_identity, request)
    if not _reserve_log_stream(identity, is_admin):
        raise HTTPException(429, "Too many open log streams")

    released = threading.Event()

    def _release_once() -> None:
        if not released.is_set():
            released.set()
            _release_log_stream(identity)

    async def generator():
        try:
            last_id: int = await run_in_threadpool(_max_log_id)
            while True:
                if await request.is_disconnected():
                    break
                # Checked again on every pass, not only when the stream opened: a revoked key
                # or a session ended by a password change used to keep reading new log lines
                # for as long as the connection lived.
                try:
                    await run_in_threadpool(require_auth, request)
                except HTTPException:
                    break
                for r in await run_in_threadpool(_logs_after, last_id):
                    last_id = r["id"]
                    yield {"data": json.dumps(r)}
                await asyncio.sleep(2)
        finally:
            _release_once()

    gen = generator()
    # A generator that is never iterated (the client left before the first chunk) never
    # reaches its `finally`; the finalizer gives the slot back when it is collected.
    weakref.finalize(gen, _release_once)
    try:
        return _SSEResponse(gen)
    except BaseException:
        _release_once()
        raise
