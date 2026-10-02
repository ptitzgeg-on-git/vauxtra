from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth import require_auth
from app.config import decrypt_secret, encrypt_secret
from app.models import get_db
from app.security import mask_secret_url

router = APIRouter()

_WEBHOOK_SCOPE_TYPES = {"all", "provider", "service"}

#: The table each scope word names. `scope_ref_id` is a bare INTEGER with no foreign key, so
#: the word is the only thing that says which id space the number is in.
_SCOPE_TARGET_TABLE = {"provider": "providers", "service": "services"}


class WebhookIn(BaseModel):
    """Body of POST /api/webhooks.

    Types only; the routes keep their own validation messages. scope_type and
    scope_ref_id stay untyped so `_normalize_scope` can return a specific error for each case.
    """

    name: str
    url: str
    enabled: bool = True
    scope_type: Any = "all"
    scope_ref_id: Any = None
    repeat_interval_minutes: int | None = 0
    alert_on_any_down: bool | None = False
    alert_on_any_up: bool | None = False
    alert_on_integration_down: bool | None = False
    alert_on_integration_up: bool | None = False
    min_down_minutes: int | None = 0


class WebhookUpdateIn(BaseModel):
    """Body of PUT /api/webhooks/{wid}; every field optional.

    The route reads model_dump(exclude_unset=True), so an omitted field keeps the stored
    value while an explicit null is still seen.
    """

    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    scope_type: Any = None
    scope_ref_id: Any = None
    repeat_interval_minutes: int | None = None
    alert_on_any_down: bool | None = None
    alert_on_any_up: bool | None = None
    alert_on_integration_down: bool | None = None
    alert_on_integration_up: bool | None = None
    min_down_minutes: int | None = None


class WebhookUrlIn(BaseModel):
    """The body of `POST /api/webhooks/test-url`. An empty one is still `URL is required`."""

    url: str


class ServiceAlertIn(BaseModel):
    """One rule of POST /api/services/{sid}/alerts. `webhook_id` is required: the route
    replaces the whole list, so a skipped entry would silently delete a rule.
    """

    webhook_id: int
    on_up: bool | None = True
    on_down: bool | None = True
    min_down_minutes: int | None = 0


class ServiceAlertsIn(BaseModel):
    """Body of POST /api/services/{sid}/alerts. `alerts` is required, so `{}` is refused
    rather than read as "delete every rule"; send `{"alerts": []}` to clear.
    """

    alerts: list[ServiceAlertIn]


def _normalize_scope(body: dict, existing: dict | None = None, *, conn) -> tuple[str, int | None]:
    """Return the (scope_type, scope_ref_id) to store, refusing ids that name no row.

    Changing scope_type requires a new target: service and provider ids share one space.
    """
    current_scope_type = (existing or {}).get("scope_type", "all")
    current_scope_ref_id = (existing or {}).get("scope_ref_id")

    scope_type = str(body.get("scope_type", current_scope_type) or "all").strip().lower()
    if scope_type not in _WEBHOOK_SCOPE_TYPES:
        raise HTTPException(400, "Invalid scope type")
    if scope_type == "all":
        return "all", None

    inherited = current_scope_ref_id if scope_type == current_scope_type else None
    scope_ref_raw = body.get("scope_ref_id", inherited)
    if scope_ref_raw in (None, "", 0, "0"):
        raise HTTPException(400, "A provider or service target is required for this scope")
    try:
        scope_ref_id = int(scope_ref_raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid scope target")
    if scope_ref_id <= 0:
        raise HTTPException(400, "Invalid scope target")

    table = _SCOPE_TARGET_TABLE[scope_type]
    found = conn.execute(
        f"SELECT 1 FROM {table} WHERE id=?",  # noqa: S608 -- `table` is picked by a word already
        (scope_ref_id,),                      # checked against `_WEBHOOK_SCOPE_TYPES`; the id is bound
    ).fetchone()
    if not found:
        raise HTTPException(400, f"Nothing to alert on -- unknown {scope_type} {scope_ref_id}")
    return scope_type, scope_ref_id


# Apprise schemes that POST a caller-chosen body to a caller-chosen host. Allowed, but
# admin only, since otherwise a dashboard key could fire arbitrary requests from inside the
# network. Exhaustive for apprise 1.10.
_GENERIC_HTTP_SCHEMES = ("json://", "jsons://", "form://", "forms://", "xml://", "xmls://")


def _validate_apprise_url(url: str, request: Request | None = None) -> str:
    """Validate and normalize an Apprise URL string.

    `request` is passed where the caller chose the URL; None skips the admin-scheme check
    so a partial update of an existing webhook does not demand admin.
    """
    value = (url or "").strip()
    if not value:
        raise HTTPException(400, "URL is required")
    # Refuse the masked form a read returns, so a read-modify-write cannot store it.
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

    Dropped rather than masked so a read-modify-write cannot store the mask; PUT keeps the
    stored URL when `url` is omitted.
    """
    data = dict(row)
    data["url_masked"] = mask_secret_url(decrypt_secret(data.pop("url", "")))
    return data


@router.get("/api/webhooks")
def list_webhooks(request: Request):
    """Return all configured webhooks (Apprise notification targets), URLs masked."""
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM webhooks ORDER BY name").fetchall()
        return [_public_webhook(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/webhooks", status_code=201)
def add_webhook(request: Request, body: WebhookIn):
    """Create a new webhook notification target."""
    require_auth(request, scope="write")
    name = body.name.strip()
    url  = _validate_apprise_url(body.url, request)
    if not name or not url:
        raise HTTPException(400, "Name and URL are required")
    alert_on_any_down        = int(bool(body.alert_on_any_down))
    alert_on_any_up          = int(bool(body.alert_on_any_up))
    alert_on_integration_down = int(bool(body.alert_on_integration_down))
    alert_on_integration_up  = int(bool(body.alert_on_integration_up))
    min_down_minutes         = max(0, body.min_down_minutes or 0)
    repeat_interval_minutes  = max(0, body.repeat_interval_minutes or 0)
    enabled                  = int(bool(body.enabled))
    conn = get_db()
    try:
        # Uses the keys actually sent, so an omitted scope_type still means "all".
        scope_type, scope_ref_id = _normalize_scope(body.model_dump(exclude_unset=True), conn=conn)
        cur = conn.execute(
            """INSERT INTO webhooks
               (name, url, enabled, scope_type, scope_ref_id, repeat_interval_minutes,
                alert_on_any_down, alert_on_any_up, alert_on_integration_down,
                alert_on_integration_up, min_down_minutes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                encrypt_secret(url),
                enabled,
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
        return {"id": wid, "name": name, "url_masked": mask_secret_url(url), "enabled": enabled,
                "scope_type": scope_type, "scope_ref_id": scope_ref_id,
                "repeat_interval_minutes": repeat_interval_minutes,
                "alert_on_any_down": alert_on_any_down, "alert_on_any_up": alert_on_any_up,
                "alert_on_integration_down": alert_on_integration_down,
                "alert_on_integration_up": alert_on_integration_up,
                "min_down_minutes": min_down_minutes}
    finally:
        conn.close()


@router.put("/api/webhooks/{wid}")
def update_webhook(wid: int, request: Request, body: WebhookUpdateIn):
    """Update an existing webhook by ID."""
    require_auth(request, scope="write")
    # What the caller sent, which is not the same as what has a value: every field of the
    # model defaults to `None`, and an absent one means "keep what is stored".
    supplied = body.model_dump(exclude_unset=True)
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM webhooks WHERE id=?", (wid,)).fetchone()
        if not existing:
            raise HTTPException(404, "Webhook not found")

        # Support partial updates (e.g. toggle sends only { enabled }).
        name    = supplied.get("name", existing["name"])
        url     = supplied.get("url", decrypt_secret(existing["url"]))
        enabled = int(bool(supplied.get("enabled", existing["enabled"])))
        scope_type, scope_ref_id = _normalize_scope(supplied, dict(existing), conn=conn)
        alert_on_any_down         = int(bool(supplied.get("alert_on_any_down",        existing["alert_on_any_down"])))
        alert_on_any_up           = int(bool(supplied.get("alert_on_any_up",          existing["alert_on_any_up"])))
        alert_on_integration_down = int(bool(supplied.get("alert_on_integration_down", existing["alert_on_integration_down"])))
        alert_on_integration_up   = int(bool(supplied.get("alert_on_integration_up",   existing["alert_on_integration_up"])))
        min_down_minutes          = max(0, int(supplied.get("min_down_minutes", existing["min_down_minutes"]) or 0))
        repeat_interval_minutes   = max(0, int(supplied.get("repeat_interval_minutes", existing["repeat_interval_minutes"]) or 0))

        name = (name or "").strip()
        # Only when the caller supplied one: `url` otherwise holds what is already stored.
        url  = _validate_apprise_url(url, request if "url" in supplied else None)
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
                encrypt_secret(url),
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
    """Delete a webhook by ID. 404 when there is nothing at that id."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM webhooks WHERE id=?", (wid,)).fetchone()
        if not row:
            raise HTTPException(404, "Webhook not found")
        # `webhook_delivery_log` cascades off this one statement (schema 11): the queued
        # sends go with the row, which is why nothing else is deleted here by hand.
        conn.execute("DELETE FROM webhooks WHERE id=?", (wid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# The test routes answer 502 when the target refuses (upstream failure), and 500 only
# when apprise is not installed.
@router.post("/api/webhooks/test-url")
def test_webhook_url(request: Request, body: WebhookUrlIn):
    """Test a webhook URL without saving it (for pre-validation in setup wizard)."""
    require_auth(request, scope="write")
    url = _validate_apprise_url(body.url, request)
    try:
        import apprise
        a = apprise.Apprise()
        a.add(url)
        ok = a.notify(title="Vauxtra: Test", body="Test notification from Vauxtra.")
        if not ok:
            raise HTTPException(
                502,
                "The notification target refused the message. Check the URL, "
                "and that the destination is reachable from this instance.",
            )
        return {"ok": True}
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
    except HTTPException:
        raise
    except Exception as e:
        # `_validate_apprise_url` already parsed this URL above the try, so whatever apprise
        # raises from here on comes out of the send itself.
        raise HTTPException(502, str(e))


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
        if not a.add(decrypt_secret(row["url"])):
            raise HTTPException(400, "Invalid or unrecognized Apprise URL")
        ok = a.notify(title="Vauxtra: Test", body="Test notification from Vauxtra.")
        if not ok:
            # Same reattribution as `test-url` above, on a URL the operator stored earlier.
            raise HTTPException(502, "The notification target refused the message.")
        return {"ok": True}
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
    except HTTPException:
        raise
    except Exception as e:
        # `a.add()` answered above, so anything raised past it comes out of the send. Only
        # the class is returned: the stored URL is a credential, and the text may quote it.
        raise HTTPException(
            502, f"The notification target could not be reached ({type(e).__name__})."
        )


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
            item["webhook_url_masked"] = mask_secret_url(decrypt_secret(item.pop("webhook_url", "")))
            out.append(item)
        return out
    finally:
        conn.close()


@router.post("/api/services/{sid}/alerts")
def set_service_alerts(sid: int, request: Request, body: ServiceAlertsIn):
    """Replace all alerts for the service with the provided list."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        # Check the ids before the DELETE, so an unknown one is a 400 with nothing changed.
        if not conn.execute("SELECT 1 FROM services WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Service not found")
        unknown = sorted({
            alert.webhook_id
            for alert in body.alerts
            if not conn.execute(
                "SELECT 1 FROM webhooks WHERE id=?", (alert.webhook_id,)
            ).fetchone()
        })
        if unknown:
            raise HTTPException(
                400,
                "Nothing to alert through -- unknown webhook "
                + ", ".join(str(webhook_id) for webhook_id in unknown),
            )

        conn.execute("DELETE FROM service_alerts WHERE service_id=?", (sid,))
        for alert in body.alerts:
            conn.execute(
                """INSERT INTO service_alerts (service_id, webhook_id, on_up, on_down, min_down_minutes)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(service_id, webhook_id) DO UPDATE SET
                     on_up=excluded.on_up, on_down=excluded.on_down,
                     min_down_minutes=excluded.min_down_minutes""",
                (sid, alert.webhook_id,
                 int(bool(alert.on_up)),
                 int(bool(alert.on_down)),
                 max(0, alert.min_down_minutes or 0)),
            )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
