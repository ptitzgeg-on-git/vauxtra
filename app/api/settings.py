import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request

from app.auth import require_auth
from app.models import add_log, ensure_default_docker_endpoint, get_db
from app.security import mask_secret_url
from app.validators import is_valid_domain, normalize_domain

try:
    from sse_starlette.sse import EventSourceResponse as _SSEResponse
    _HAS_SSE = True
except ImportError:
    _SSEResponse = None
    _HAS_SSE = False

router = APIRouter()

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

# `timezone` has no reader anywhere -- not in Python, not in the frontend. It is validated
# for shape rather than against the IANA database on purpose: `zoneinfo` needs the `tzdata`
# package on Windows, and a missing package would otherwise reject every value there.
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
_PROTECTED_SETTINGS = ("app_password_hash", "setup_completed", "schema_version", "auth_mode")

# The placeholders are built from the tuple, never written out by hand: the two DELETE
# statements below used a literal `(?,?,?)`, so adding this fourth key would have raised
# "Incorrect number of bindings" at the exact moment an operator asked for a reset.
_PROTECTED_PLACEHOLDERS = ",".join("?" * len(_PROTECTED_SETTINGS))


def _is_valid_public_target_sources(value: str) -> bool:
    entries = [line.strip() for line in str(value).replace(",", "\n").splitlines() if line.strip()]
    if not entries:
        return False
    return all(entry.startswith(("http://", "https://")) for entry in entries)


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
            return None, "expected one http:// or https:// URL per line"
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
        total = conn.execute("SELECT COUNT(*) FROM logs WHERE level=?", (level,)).fetchone()[0]
        rows  = conn.execute(
            "SELECT * FROM logs WHERE level=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (level, per_page, offset),
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
    require_auth(request, scope="write")
    conn = get_db()
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    add_log("info", "Logs cleared")
    return {"ok": True}


@router.post("/api/settings/test-webhook")
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
            "SELECT id, name, url FROM webhooks WHERE enabled=1 ORDER BY id"
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
            elif a.notify(
                title="Vauxtra — Test",
                body="✓ Test notification — configuration is working correctly.",
            ):
                entry["ok"] = True
            else:
                entry["error"] = "send failed (service unavailable, or the URL is wrong)"
        except Exception as e:  # one bad target must not hide the state of the others
            entry["error"] = str(e)
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


@router.get("/api/logs/stream")
async def stream_logs(request: Request):
    """Server-Sent Events stream: pushes new log rows every 2 s."""
    require_auth(request)
    if not _HAS_SSE:
        raise HTTPException(500, "Package 'sse-starlette' not installed — rebuild the Docker image.")

    async def generator():
        conn = get_db()
        row  = conn.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()
        conn.close()
        last_id: int = row[0]

        while True:
            if await request.is_disconnected():
                break
            conn = get_db()
            rows = conn.execute(
                "SELECT id, level, message, created_at FROM logs "
                "WHERE id > ? ORDER BY id ASC LIMIT 50",
                (last_id,),
            ).fetchall()
            conn.close()
            for r in rows:
                last_id = r["id"]
                yield {"data": json.dumps(dict(r))}
            await asyncio.sleep(2)

    return _SSEResponse(generator())
