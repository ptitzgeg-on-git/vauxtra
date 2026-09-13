from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth import require_auth
from app.models import get_db
from app.security import mask_secret_url

router = APIRouter()

_WEBHOOK_SCOPE_TYPES = {"all", "provider", "service"}

#: The table each scope word names. `scope_ref_id` is a bare INTEGER with no foreign key, so
#: the word is the only thing that says which id space the number is in.
_SCOPE_TARGET_TABLE = {"provider": "providers", "service": "services"}


class WebhookIn(BaseModel):
    """The body of `POST /api/webhooks`, typed so a wrong value is answered as one.

    These four routes read their body as a plain `dict` and reached for what they wanted, so
    every value arrived unchecked: a `name` that was a number raised on `.strip()`, a
    `min_down_minutes` that was a word raised inside `int()`, and both came back as a 500
    with no sentence for whoever sent it. The panel cannot produce any of that -- it types
    its own state and clamps the minutes before it posts -- but `create_webhook`
    (`vauxtra_mcp/tools/admin.py`) hands the body straight through, and what is on the other
    side of it is a language model writing JSON.

    Only the types live here. Every refusal these routes already wrote by hand stays where it
    was, so the sentence an operator reads for a mistake they can actually make does not
    change: the empty name is still `Name and URL are required`, the URL apprise will not
    parse is still refused by `_validate_apprise_url`, and a scope target naming nothing is
    still `_normalize_scope`. `scripts/check_api_mcp_parity.py` compares an MCP tool's
    payload against the model of the route it calls, and had nothing to compare for these.

    `scope_type` and `scope_ref_id` stay untyped on purpose. `_normalize_scope` already reads
    both through `str()` and `int()` inside a handler and answers a different sentence for
    each way they can be wrong -- an unknown word, a missing target, a target that is not a
    number, a number naming no row. Declaring them here would refuse those before it, and
    replace four accurate sentences with one generic one.
    """

    name: str
    url: str
    scope_type: Any = "all"
    scope_ref_id: Any = None
    repeat_interval_minutes: int | None = 0
    alert_on_any_down: bool | None = False
    alert_on_any_up: bool | None = False
    alert_on_integration_down: bool | None = False
    alert_on_integration_up: bool | None = False
    min_down_minutes: int | None = 0


class WebhookUpdateIn(BaseModel):
    """The body of `PUT /api/webhooks/{wid}`, where every field is optional.

    The enable toggle sends `{"enabled": false}` and nothing else, and the route fills the
    rest from the stored row. A model whose fields all default to `None` cannot tell that
    apart from a caller who wrote `null` on purpose, so the route reads
    `model_dump(exclude_unset=True)`: what is in it is what the caller actually sent, which
    is the question `body.get(name, existing[name])` was asking of the dict all along.
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
    """One rule of `POST /api/services/{sid}/alerts`.

    `webhook_id` is required. It used to be read with `.get()` and the entry skipped when it
    was absent, which was silent in the one place silence costs most: this route replaces the
    whole list, so an entry that arrived without its id did not fail to be added, it removed
    the rule that was already there. The answer was `{"ok": True}` and the service stopped
    alerting. `ServiceAlertInput` in `frontend/src/types/api.ts` has always declared it
    required; this is the API saying the same thing.
    """

    webhook_id: int
    on_up: bool | None = True
    on_down: bool | None = True
    min_down_minutes: int | None = 0


class ServiceAlertsIn(BaseModel):
    """The body of `POST /api/services/{sid}/alerts`.

    `alerts` has no default, so a body that does not carry it is refused instead of being
    read as an empty list. Sending `{}` used to delete every rule of the service and answer
    `ok`: the route is a replace, and a replace with nothing in it cannot be told apart from
    a payload whose one key was misspelled. Clearing the rules is still a single request --
    it is `{"alerts": []}`, which says so.
    """

    alerts: list[ServiceAlertIn]


def _normalize_scope(body: dict, existing: dict | None = None, *, conn) -> tuple[str, int | None]:
    """The `(word, id)` a webhook is stored under, refused when it names nothing.

    `webhooks.scope_ref_id` carries no foreign key, which `_provider_webhook_dependents`
    (`app/api/providers.py`) and `_webhooks_scoped_to` (`app/api/services.py`) both explain
    at length: nothing cascades through a reference the schema does not declare, so deleting
    the target leaves the webhook behind still holding its id. Both of them warn in the
    journal when that happens. This is the other end of the same problem -- an id that named
    nothing on the way *in* -- and it had no warning at all. `_service_matches_scope`
    (`app/scheduler.py`) answers False for every service from then on and Settings goes on
    showing the webhook as enabled: dead, and it looks armed.

    The two words share one id space and mean different things in it, so a scope that changes
    word cannot keep the id it had. Service 4 and provider 4 are unrelated rows, and carrying
    the number across turns an alert on a service into an alert on whichever provider happens
    to hold that id -- not silence, which someone would eventually notice, but a wrong
    subject reported with confidence. Changing the word therefore asks for the new target.
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
    conn = get_db()
    try:
        # After the connection is open: a scope target is checked against the table its word
        # names, and an unknown one is a 400 before anything is written. It reads the keys the
        # caller sent rather than the model, so an omitted `scope_type` still means "all".
        scope_type, scope_ref_id = _normalize_scope(body.model_dump(exclude_unset=True), conn=conn)
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
        url     = supplied.get("url", existing["url"])
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
    """Delete a webhook by ID. 404 when there is nothing at that id.

    `DELETE /api/webhooks/999999` on an instance that has no webhook 999999 answered
    `200 {"ok": true}` -- a receipt for a deletion that never happened, handed to a caller
    that had just been told the row existed. `update_webhook` above already answers 404 for
    that same missing row, so the two verbs disagreed about whether the id was real, and the
    MCP bridge (`vauxtra_mcp/tools/admin.py::delete_webhook`) returned the `ok` to its own
    caller as proof. The lookup is the half that was missing here.
    """
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


# Both test routes below send one message and report what came back. A refusal from the
# target answers 502, not 500. Nothing broke here: the URL parsed, the request left, and the
# far end is the one that said no -- an unreachable collector, a revoked Discord webhook, a
# host that drops the packet. Answering 500 made the notification tab tell the operator the
# server had a problem, and sent them reading Vauxtra's own journal for a fault that was
# never ours. 503 would be the same accusation written in another number, since it says
# *this* server is unavailable; 504 would claim a timeout, and apprise reports a refusal and
# a timeout with the same bare `False`. What stays 500 is the missing package: that one is
# genuinely a broken installation of Vauxtra.
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
        if not a.add(row["url"]):
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
        # `a.add()` answered above, so anything raised past it comes out of the send.
        raise HTTPException(502, str(e))


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
def set_service_alerts(sid: int, request: Request, body: ServiceAlertsIn):
    """Replace all alerts for the service with the provided list."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        # Both ids are real foreign keys, and both were left to the index to report. An id
        # naming nothing raised `IntegrityError` out of the INSERT -- a 500 carrying no
        # sentence -- and the DELETE that empties the service had already run in the same
        # transaction. The rollback saved the rules that time, which is luck and not design:
        # nothing in the route said its deletion depended on every id in the list being real.
        #
        # They are asked about first because they can be. An id that names no row is known
        # before any work is done, unlike a hostname a second writer takes mid-request
        # (`_hostname_taken`, `app/api/services.py`), where only the unique index can still
        # tell and the route has to be ready to be refused by it.
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
