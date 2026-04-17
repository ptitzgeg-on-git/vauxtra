import asyncio
import json

from fastapi import APIRouter, Request, HTTPException
from app.models import get_db, get_db_ctx, add_log
from app.auth import require_auth

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
    "public_target_sources",
    "public_target_timeout",
    "public_target_priority",
}
_VALID_THEMES   = {"light", "dark"}
_VALID_PUBLIC_TARGET_PRIORITY = {"server_public_ip", "proxy_provider_host", "current"}


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


@router.get("/api/settings")
def get_settings(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


@router.post("/api/settings")
def save_settings(request: Request, body: dict):
    require_auth(request, scope="write")
    conn = get_db()
    for key, value in body.items():
        if key not in _VALID_SETTINGS:
            continue
        if key == "theme" and value not in _VALID_THEMES:
            continue
        if key == "public_target_sources" and not _is_valid_public_target_sources(str(value)):
            continue
        if key == "public_target_timeout":
            try:
                timeout = float(value)
                if timeout < 0.5 or timeout > 10.0:
                    continue
            except Exception:
                continue
        if key == "public_target_priority" and not _is_valid_public_target_priority(str(value)):
            continue
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    conn.commit()
    conn.close()
    if "check_interval" in body:
        try:
            from app.scheduler import configure
            configure(int(body["check_interval"]))
        except (ValueError, Exception):
            pass
    return {"ok": True}


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
    require_auth(request)
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('webhook_url', 'webhook_enabled')"
    ).fetchall()
    conn.close()
    cfg = {r["key"]: r["value"] for r in rows}
    url = cfg.get("webhook_url", "").strip()
    if not url:
        raise HTTPException(400, "No notification URL configured")
    try:
        import apprise
        a = apprise.Apprise()
        if not a.add(url):
            raise HTTPException(400, "Invalid or unrecognized Apprise URL")
        ok = a.notify(
            title="Vauxtra — Test",
            body="✓ Test notification — configuration is working correctly.",
        )
        if not ok:
            raise HTTPException(500, "Send failed (incorrect URL or service unavailable)")
        return {"ok": True}
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed — rebuild the Docker image")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/reset")
def reset_all(request: Request):
    require_auth(request, scope="admin")
    conn = get_db()
    conn.executescript("""
        DELETE FROM service_alerts;
        DELETE FROM service_tags;
        DELETE FROM service_environments;
        DELETE FROM uptime_events;
        DELETE FROM services;
        DELETE FROM webhooks;
        DELETE FROM providers;
        DELETE FROM tags;
        DELETE FROM environments;
        DELETE FROM domains;
        DELETE FROM logs;
        DELETE FROM settings;
    """)
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
    name = body.get("name", "").strip().lower()
    if not name or "." not in name:
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
