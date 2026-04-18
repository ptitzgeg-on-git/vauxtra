from fastapi import APIRouter, Request, HTTPException
from app.models import get_db
from app.auth import require_auth

router = APIRouter()


@router.get("/api/webhooks")
def list_webhooks(request: Request):
    """Return all configured webhooks (Apprise notification targets)."""
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM webhooks ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/webhooks", status_code=201)
def add_webhook(request: Request, body: dict):
    """Create a new webhook notification target."""
    require_auth(request, scope="write")
    name = body.get("name", "").strip()
    url  = body.get("url",  "").strip()
    if not name or not url:
        raise HTTPException(400, "Name and URL are required")
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO webhooks (name, url, enabled) VALUES (?,?,1)", (name, url)
        )
        wid = cur.lastrowid
        conn.commit()
        return {"id": wid, "name": name, "url": url, "enabled": 1}
    finally:
        conn.close()


@router.put("/api/webhooks/{wid}")
def update_webhook(wid: int, request: Request, body: dict):
    """Update an existing webhook by ID."""
    require_auth(request, scope="write")
    name    = body.get("name", "").strip()
    url     = body.get("url",  "").strip()
    enabled = int(bool(body.get("enabled", True)))
    if not name or not url:
        raise HTTPException(400, "Name and URL are required")
    conn = get_db()
    try:
        conn.execute("UPDATE webhooks SET name=?, url=?, enabled=? WHERE id=?", (name, url, enabled, wid))
        conn.commit()
        return {"id": wid, "name": name, "url": url, "enabled": enabled}
    finally:
        conn.close()


@router.delete("/api/webhooks/{wid}")
def delete_webhook(wid: int, request: Request):
    """Delete a webhook by ID."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        conn.execute("DELETE FROM webhooks WHERE id=?", (wid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/api/webhooks/test-url")
def test_webhook_url(request: Request, body: dict):
    """Test a webhook URL without saving it (for pre-validation in setup wizard)."""
    require_auth(request, scope="write")
    url = body.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        import apprise
        a = apprise.Apprise()
        if not a.add(url):
            raise HTTPException(400, "Invalid or unrecognized Apprise URL")
        ok = a.notify(title="Vauxtra: Test", body="Test notification from Vauxtra.")
        if not ok:
            raise HTTPException(500, "Send failed - check your URL and try again")
        return {"ok": True}
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/webhooks/{wid}/test")
def test_webhook(wid: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM webhooks WHERE id=?", (wid,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Webhook not found")
    try:
        import apprise
        a = apprise.Apprise()
        if not a.add(row["url"]):
            raise HTTPException(400, "Invalid or unrecognized Apprise URL")
        ok = a.notify(title="Vauxtra: Test", body="Test notification from Vauxtra.")
        if not ok:
            raise HTTPException(500, "Send failed")
        return {"ok": True}
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Per-service alerts ─────────────────────────────────────────────────────

@router.get("/api/services/{sid}/alerts")
def get_service_alerts(sid: int, request: Request):
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT sa.*, w.name AS webhook_name, w.url AS webhook_url
               FROM service_alerts sa
               JOIN webhooks w ON w.id = sa.webhook_id
               WHERE sa.service_id=?""", (sid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/services/{sid}/alerts")
def set_service_alerts(sid: int, request: Request, body: dict):
    """Replace all alerts for the service with the provided list."""
    require_auth(request, scope="write")
    alerts = body.get("alerts", [])
    conn   = get_db()
    try:
        conn.execute("DELETE FROM service_alerts WHERE service_id=?", (sid,))
        for a in alerts:
            wid = a.get("webhook_id")
            if not wid:
                continue
            conn.execute(
                """INSERT INTO service_alerts (service_id, webhook_id, on_up, on_down, min_down_minutes)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(service_id, webhook_id) DO UPDATE SET
                     on_up=excluded.on_up, on_down=excluded.on_down,
                     min_down_minutes=excluded.min_down_minutes""",
                (sid, wid,
                 int(bool(a.get("on_up", True))),
                 int(bool(a.get("on_down", True))),
                 int(a.get("min_down_minutes", 0))),
            )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
