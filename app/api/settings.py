import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth import is_authorized, require_auth
from app.config import decrypt_secret
from app.models import add_log, ensure_default_docker_endpoint, get_db, normalise_log_level
from app.security import mask_secret_url
from app.text import name_list, plural, verb
from app.validators import DOMAIN_REASONS, domain_problem, normalize_domain

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
    # Read by app/scheduler.py.
    "auto_reconcile_enabled",
    "auto_reconcile_interval",
    "webhook_retry_retention_days",
}
_VALID_THEMES   = {"light", "dark"}
_VALID_PUBLIC_TARGET_PRIORITY = {"server_public_ip", "proxy_provider_host", "current"}

# Whole-number settings and their allowed range. check_interval is parsed with a bare int()
# at startup, so an invalid value would keep the app from booting.
_SETTING_RANGES = {
    "check_interval": (0, 1440),            # 0 disables automatic health checks
    "log_retention_days": (1, 365),
    "monitoring_retention_days": (1, 365),
    "auto_reconcile_interval": (0, 1440),   # 0 disables auto-reconcile too
    "webhook_retry_retention_days": (1, 90),  # the bounds `_read_retention_days` clamps to
}

# `timezone` only affects frontend rendering (the server stores UTC; the TZ env var sets the
# server clock). Validated for shape, not against IANA data, because zoneinfo needs tzdata
# on Windows.
_TIMEZONE_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9+_-]*(?:/[A-Za-z0-9+_.-]+){0,2}$")

_BOOLEAN_WORDS = {"true", "false", "1", "0", "yes", "no", "on", "off"}

# The settings table also holds the admin password hash; reads go through this whitelist.
_READABLE_SETTINGS = _VALID_SETTINGS | {"schema_version", "setup_completed"}

# An Apprise URL is its own credential: returned with everything past the scheme masked.
_MASKED_SETTINGS = {"webhook_url"}

# Kept across reset and restore: dropping the password hash or auth_mode would reopen the
# instance, importing them would let a backup pick the password, and resetting
# session_epoch to 0 would revive sessions revoked by a password change.
_PROTECTED_SETTINGS = (
    "app_password_hash",
    "setup_completed",
    "schema_version",
    "auth_mode",
    "session_epoch",
)

# Built from the tuple so the bindings always match its length.
_PROTECTED_PLACEHOLDERS = ",".join("?" * len(_PROTECTED_SETTINGS))


# Admin only: these URLs are fetched by the scheduler from inside the network (SSRF).
_ADMIN_ONLY_SETTINGS = frozenset({"public_target_sources"})


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
    """Return `(value to store, "")`, or `(None, reason)` when the value is not acceptable."""
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
        # GET /api/settings masks the URL, so a read-modify-write would store the mask.
        if "***" in text:
            return None, (
                "that is the masked form the API returns, not a usable URL -- "
                "retype the full one, or leave the key out to keep the stored value"
            )
        # Delivery reads the webhooks table; legacy values are migrated at startup.
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


def _not_applied(collected: list[str], key: str, exc: Exception) -> None:
    """Record a setting that was saved but could not be applied to the running scheduler.

    The value stays stored, the key is returned under `not_applied` and the reason logged,
    so the answer is neither a 500 for a saved value nor a silent success.
    """
    collected.append(key)
    add_log("warning", f"Saved {key}, but it could not be applied now: {type(exc).__name__}: {exc}")


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
            # GET also returns read-only keys, so a read-modify-write sends them back.
            # Dropped and listed under `ignored`.
            ignored.append(key)
            continue
        stored, reason = _validate_setting(key, value)
        if stored is None:
            rejected[key] = reason
        else:
            accepted[key] = stored

    if rejected:
        # All or nothing: nothing is written when any field is rejected.
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
    # Applied to the running scheduler below. Anything that cannot be applied is named in
    # the answer rather than dropped -- see `_not_applied`.
    not_applied: list[str] = []

    if "check_interval" in accepted:
        # The int() cannot fail after validation; this guards the scheduler call.
        try:
            from app.scheduler import configure
            configure(int(accepted["check_interval"]))
        except Exception as exc:  # noqa: BLE001 -- named in the answer, not swallowed
            _not_applied(not_applied, "check_interval", exc)

    if {"auto_reconcile_enabled", "auto_reconcile_interval"} & set(accepted):
        # Applied now; the other key is read back since either can change alone.
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
        except Exception as exc:  # noqa: BLE001 -- named in the answer, not swallowed
            # int() is inside the guard here: the value comes from the database, where an
            # older row may hold a non-number.
            _not_applied(not_applied, "auto_reconcile", exc)

    return {
        "ok": True,
        "saved": sorted(accepted),
        "ignored": sorted(ignored),
        "not_applied": sorted(not_applied),
    }


@router.get("/api/stats")
def get_stats(request: Request):
    require_auth(request)
    conn  = get_db()
    # services_ok and services_error count enabled services only: the scheduler never checks
    # disabled ones, so their status is frozen. Matches serviceStatus() in the frontend.
    stats = {
        "services":       conn.execute("SELECT COUNT(*) FROM services").fetchone()[0],
        "providers":      conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0],
        "logs":           conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0],
        "services_ok":    conn.execute("SELECT COUNT(*) FROM services WHERE status='ok' AND enabled=1").fetchone()[0],
        "services_error": conn.execute("SELECT COUNT(*) FROM services WHERE status='error' AND enabled=1").fetchone()[0],
        "tags":           conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
    }
    conn.close()
    return stats


@router.get("/api/logs")
def get_logs(request: Request, page: int = 1, per_page: int = 50, level: str = ""):
    """Read the activity log. Admin scope: it records sign-ins, key creation with scopes and
    password changes, which a read key must not see.
    """
    require_auth(request, scope="admin")
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
    """Empty the activity log. `admin`, and the emptying is itself recorded."""
    # Admin, not write: the log is the audit trail, and a write key must not erase it.
    # The clear itself is logged.
    require_auth(request, scope="admin")
    conn = get_db()
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    add_log("info", "Logs cleared")
    return {"ok": True}


@router.post("/api/settings/test-webhook")
def test_webhook(request: Request):
    """Send a test notification to every enabled webhook.

    Write scope: it delivers a real notification outside Vauxtra.
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
            if not a.add(decrypt_secret(row["url"])):
                entry["error"] = "invalid or unrecognized Apprise URL"
            elif a.notify(
                title="Vauxtra — Test",
                body="✓ Test notification — configuration is working correctly.",
            ):
                entry["ok"] = True
            else:
                entry["error"] = "send failed (service unavailable, or the URL is wrong)"
        except Exception as e:  # one bad target must not hide the state of the others
            # Class name only: the exception may quote the Apprise URL, a credential.
            entry["error"] = f"the send raised {type(e).__name__}"
        results.append(entry)

    # 200 either way: a per-target report says more than a single failed status, and the
    # caller can see which one is broken instead of guessing.
    return {"ok": all(r["ok"] for r in results), "results": results}


@router.post("/api/reset")
def reset_all(request: Request):
    require_auth(request, scope="admin")
    conn = get_db()
    # Wipes every business table, including Docker endpoints, templates, the webhook
    # delivery queue and scheduler state, so no credential or pending send survives.
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
    # Never the credentials: without the password hash the instance would answer anonymously.
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


class DomainIn(BaseModel):
    """Body of POST /api/domains. Validation messages come from `domain_problem`."""

    name: str


@router.post("/api/domains", status_code=201)
def add_domain(request: Request, body: DomainIn):
    require_auth(request, scope="write")
    name = normalize_domain(body.name)
    problem = domain_problem(name, require_dot=True)
    if problem:
        raise HTTPException(400, f"Invalid domain name: {DOMAIN_REASONS[problem]}")
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return {"name": name}


def _log_domain_removal(conn, name: str, services: list[str], templates: list[str]) -> None:
    """Log a domain deletion with the services and templates that still use the name.

    Discovery and imports re-add domains automatically, so the log is the only record.
    """
    total = len(services) + len(templates)
    if not total:
        add_log("info", f"Domain deleted: {name}", conn)
        return
    holders = []
    if services:
        holders.append(plural(len(services), "service"))
    if templates:
        holders.append(plural(len(templates), "service template"))
    names = name_list(services + templates)
    add_log(
        "warn",
        f"Domain deleted: {name} -- {' and '.join(holders)} still "
        f"{verb(total, 'uses', 'use')} the name and {verb(total, 'keeps', 'keep')} "
        f"working ({names})",
        conn,
    )


@router.delete("/api/domains/{name:path}")
def delete_domain(name: str, request: Request):
    """Delete a root domain by name (normalized like add_domain). 404 when absent."""
    require_auth(request, scope="write")
    wanted = normalize_domain(name)
    conn = get_db()
    try:
        if not conn.execute("SELECT name FROM domains WHERE name=?", (wanted,)).fetchone():
            raise HTTPException(404, "Domain not found")
        # Nothing else is touched: services and templates hold the domain as plain text and
        # keep routing. They are only collected for the log.
        services = [
            # `.strip(".")` the way every other fqdn in the API is built: an apex route
            # stores an empty subdomain, and the naive join names it `.example.test`.
            f"{r['subdomain']}.{r['domain']}".strip(".")
            for r in conn.execute(
                "SELECT subdomain, domain FROM services WHERE domain=? ORDER BY subdomain",
                (wanted,),
            )
        ]
        templates = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM service_templates WHERE domain=? ORDER BY name", (wanted,)
            )
        ]
        conn.execute("DELETE FROM domains WHERE name=?", (wanted,))
        _log_domain_removal(conn, wanted, services, templates)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


async def _log_stream(request: Request):
    """Yield every new log line for as long as the credential stays valid.

    Module level so tests can drive the loop without an HTTP client.
    """
    conn = get_db()
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()
    conn.close()
    last_id: int = row[0]

    while True:
        if await request.is_disconnected():
            break
        # Re-check auth (admin scope) on every tick so a password change or key revocation
        # ends an open stream. Checked before reading, so nothing written after revocation is sent.
        if not is_authorized(request, scope="admin"):
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


@router.get("/api/logs/stream")
async def stream_logs(request: Request):
    """Server-Sent Events stream of new log rows every 2 s. Admin scope, like GET /api/logs.

    Auth is checked here (401 instead of an empty stream) and again on every tick.
    """
    require_auth(request, scope="admin")
    if not _HAS_SSE:
        raise HTTPException(500, "Package 'sse-starlette' not installed — rebuild the Docker image.")
    return _SSEResponse(_log_stream(request))
