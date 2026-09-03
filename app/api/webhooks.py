from fastapi import APIRouter, HTTPException, Request

from app.auth import require_auth
from app.models import get_db
from app.security import mask_secret_url

router = APIRouter()

_WEBHOOK_SCOPE_TYPES = {"all", "provider", "service"}


def _normalize_scope(body: dict, existing: dict | None = None) -> tuple[str, int | None]:
    current_scope_type = (existing or {}).get("scope_type", "all")
    current_scope_ref_id = (existing or {}).get("scope_ref_id")

    scope_type = str(body.get("scope_type", current_scope_type) or "all").strip().lower()
    if scope_type not in _WEBHOOK_SCOPE_TYPES:
        raise HTTPException(400, "Invalid scope type")

    scope_ref_raw = body.get("scope_ref_id", current_scope_ref_id)
    if scope_type == "all":
        return "all", None
    if scope_ref_raw in (None, "", 0, "0"):
        raise HTTPException(400, "A provider or service target is required for this scope")
    try:
        scope_ref_id = int(scope_ref_raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid scope target")
    if scope_ref_id <= 0:
        raise HTTPException(400, "Invalid scope target")
    return scope_type, scope_ref_id


# apprise carries a family of generic schemes whose whole purpose is to POST a body to a
# host the caller names: `json://`, `form://`, `xml://` and their TLS forms. For an operator
# that is a notification target; for anyone else it is an arbitrary outbound request fired
# from inside the network this instance runs in, and `POST /api/webhooks/test-url` fires one
# immediately without storing anything.
#
# They are not refused -- posting JSON to your own service is a fair reason to run a tool
# like this. They ask for `admin`, the same line drawn around `public_target_sources`, so
# that a key minted for a dashboard cannot reach past the dashboard. The list is exhaustive
# for apprise 1.10: no other scheme it accepts lets the caller choose the request body.
_GENERIC_HTTP_SCHEMES = ("json://", "jsons://", "form://", "forms://", "xml://", "xmls://")


def _validate_apprise_url(url: str, request: Request | None = None) -> str:
    """Validate and normalize an Apprise URL string.

    `request` is passed by the routes where the *caller* chose the URL. Left None, the
    scheme check is skipped -- that is how a partial update keeps working: toggling
    `enabled` on a webhook an admin created must not demand admin.
    """
    value = (url or "").strip()
    if not value:
        raise HTTPException(400, "URL is required")
    # The read routes answer with `discord://***`. A caller that reads a webhook and writes
    # it back -- an agent through the MCP bridge, a script -- would otherwise store the mask
    # as the real URL and silently kill the alerting. apprise would likely refuse it anyway;
    # relying on that would make the error message depend on which service the mask is for.
    if "***" in value:
        raise HTTPException(
            400,
            "That URL is the masked form the API returns, not a usable one. "
            "Notification URLs are never readable back -- retype the full URL, "
            "or omit the field to keep the one already stored.",
        )
    if request is not None and value.lower().startswith(_GENERIC_HTTP_SCHEMES):
        require_auth(request, scope="admin")

    try:
        import apprise
        a = apprise.Apprise()
        if not a.add(value):
            raise HTTPException(400, "Invalid or unrecognized Apprise URL")
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    return value


def _public_webhook(row) -> dict:
    """A webhook row without its URL, plus a masked form for display.

    `url` is dropped rather than masked: a client that reads a webhook and writes it
    back would otherwise store `discord://***` as the real URL. The update route
    supports partial bodies, so a round-trip that omits `url` keeps the stored one.
    """
    data = dict(row)
    data["url_masked"] = mask_secret_url(data.pop("url", ""))
    return data


@router.get("/api/webhooks")
def list_webhooks(request: Request):
    """Return all configured webhooks (Apprise notification targets), URLs masked.

    An API key is `read` by default and every GET is in that scope, so this list is the
    widest door onto the one secret a webhook holds. `docs/HOWTO.md` promises `read`
    grants "every GET" -- so the fix belongs here, not in the scope table.
    """
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM webhooks ORDER BY name").fetchall()
        return [_public_webhook(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/webhooks", status_code=201)
def add_webhook(request: Request, body: dict):
    """Create a new webhook notification target."""
    require_auth(request, scope="write")
    name = body.get("name", "").strip()
    url  = _validate_apprise_url(body.get("url", ""), request)
    if not name or not url:
        raise HTTPException(400, "Name and URL are required")
    alert_on_any_down        = int(bool(body.get("alert_on_any_down", False)))
    alert_on_any_up          = int(bool(body.get("alert_on_any_up", False)))
    alert_on_integration_down = int(bool(body.get("alert_on_integration_down", False)))
    alert_on_integration_up  = int(bool(body.get("alert_on_integration_up", False)))
    min_down_minutes         = max(0, int(body.get("min_down_minutes", 0) or 0))
    repeat_interval_minutes  = max(0, int(body.get("repeat_interval_minutes", 0) or 0))
    scope_type, scope_ref_id = _normalize_scope(body)
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO webhooks
               (name, url, enabled, scope_type, scope_ref_id, repeat_interval_minutes,
                alert_on_any_down, alert_on_any_up, alert_on_integration_down,
                alert_on_integration_up, min_down_minutes)
               VALUES (?,?,1,?,?,?,?,?,?,?,?)""",
            (
                name,
                url,
                scope_type,
                scope_ref_id,
                repeat_interval_minutes,
                alert_on_any_down,
                alert_on_any_up,
                alert_on_integration_down,
                alert_on_integration_up,
                min_down_minutes,
            ),
        )
        wid = cur.lastrowid
        conn.commit()
        return {"id": wid, "name": name, "url_masked": mask_secret_url(url), "enabled": 1,
                "scope_type": scope_type, "scope_ref_id": scope_ref_id,
                "repeat_interval_minutes": repeat_interval_minutes,
                "alert_on_any_down": alert_on_any_down, "alert_on_any_up": alert_on_any_up,
                "alert_on_integration_down": alert_on_integration_down,
                "alert_on_integration_up": alert_on_integration_up,
                "min_down_minutes": min_down_minutes}
    finally:
        conn.close()


@router.put("/api/webhooks/{wid}")
def update_webhook(wid: int, request: Request, body: dict):
    """Update an existing webhook by ID."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM webhooks WHERE id=?", (wid,)).fetchone()
        if not existing:
            raise HTTPException(404, "Webhook not found")

        # Support partial updates (e.g. toggle sends only { enabled }).
        name    = body.get("name", existing["name"])
        url     = body.get("url", existing["url"])
        enabled = int(bool(body.get("enabled", existing["enabled"])))
        scope_type, scope_ref_id = _normalize_scope(body, dict(existing))
        alert_on_any_down         = int(bool(body.get("alert_on_any_down",        existing["alert_on_any_down"])))
        alert_on_any_up           = int(bool(body.get("alert_on_any_up",          existing["alert_on_any_up"])))
        alert_on_integration_down = int(bool(body.get("alert_on_integration_down", existing["alert_on_integration_down"])))
        alert_on_integration_up   = int(bool(body.get("alert_on_integration_up",   existing["alert_on_integration_up"])))
        min_down_minutes          = max(0, int(body.get("min_down_minutes", existing["min_down_minutes"]) or 0))
        repeat_interval_minutes   = max(0, int(body.get("repeat_interval_minutes", existing["repeat_interval_minutes"]) or 0))

        name = (name or "").strip()
        # Only when the caller supplied one: `url` otherwise holds what is already stored.
        url  = _validate_apprise_url(url, request if "url" in body else None)
        if not name or not url:
            raise HTTPException(400, "Name and URL are required")

        conn.execute(
            """UPDATE webhooks SET name=?, url=?, enabled=?, scope_type=?, scope_ref_id=?,
               repeat_interval_minutes=?, alert_on_any_down=?, alert_on_any_up=?,
               alert_on_integration_down=?, alert_on_integration_up=?,
               min_down_minutes=?
               WHERE id=?""",
            (
                name,
                url,
                enabled,
                scope_type,
                scope_ref_id,
                repeat_interval_minutes,
                alert_on_any_down,
                alert_on_any_up,
                alert_on_integration_down,
                alert_on_integration_up,
                min_down_minutes,
                wid,
            ),
        )
        conn.commit()
        # Masked on the way out too: a partial update (the enable/disable toggle sends
        # only `enabled`) would otherwise echo back a URL the caller never sent.
        return {"id": wid, "name": name, "url_masked": mask_secret_url(url), "enabled": enabled,
                "scope_type": scope_type, "scope_ref_id": scope_ref_id,
                "repeat_interval_minutes": repeat_interval_minutes,
                "alert_on_any_down": alert_on_any_down, "alert_on_any_up": alert_on_any_up,
                "alert_on_integration_down": alert_on_integration_down,
                "alert_on_integration_up": alert_on_integration_up,
                "min_down_minutes": min_down_minutes}
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
    url = _validate_apprise_url(body.get("url", ""), request)
    try:
        import apprise
        a = apprise.Apprise()
        a.add(url)
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
        # Second door onto the same secret, and this one has no scope at all.
        out = []
        for r in rows:
            item = dict(r)
            item["webhook_url_masked"] = mask_secret_url(item.pop("webhook_url", ""))
            out.append(item)
        return out
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
