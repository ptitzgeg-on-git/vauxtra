"""Certificate management endpoints.

Provides certificate listing and expiry monitoring across every proxy provider that
exposes its certificate store (NPM, Zoraxy).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Request

from app.auth import require_auth
from app.expiry import parse_expiry
from app.models import add_log, get_db_ctx
from app.providers.factory import certificate_provider_types, create_provider

router = APIRouter()

# Certificates expiring within this many days are flagged as "expiring soon"
_EXPIRY_WARN_DAYS = 30


def _certificate_provider_rows(conn) -> list:
    """Enabled providers whose type declares the `certificates` capability."""
    types = certificate_provider_types()
    if not types:
        return []
    placeholders = ",".join("?" * len(types))
    return conn.execute(
        f"SELECT * FROM providers WHERE type IN ({placeholders}) AND enabled=1", types
    ).fetchall()


def _with_domain_names(cert: dict) -> dict:
    """The certificate with `domain_names` filled from `domains` when a provider omits it.

    The providers hand back `domains`; the frontend was written against `domain_names`
    and the Dashboard dereferences it without a guard, so one expiring certificate from
    a provider that only says `domains` took the whole Dashboard route down. Both
    spellings are kept so nothing that reads the other one changes.
    """
    if "domain_names" not in cert:
        cert["domain_names"] = list(cert.get("domains") or [])
    return cert


@router.get("/api/certificates")
def list_certificates(request: Request):
    require_auth(request)
    with get_db_ctx() as conn:
        cert_providers = _certificate_provider_rows(conn)

    result = []
    for p in cert_providers:
        try:
            provider = create_provider(p)
            certs = provider.get_certificates()
            for c in certs:
                _with_domain_names(c)
                c["provider_id"] = p["id"]
                c["provider_name"] = p["name"]
            result.extend(certs)
        except Exception as e:
            add_log("error", f"Certificates {p['name']}: {e}")

    return result


@router.get("/api/certificates/expiry")
def certificate_expiry(request: Request):
    """Return all certificates with parsed expiry data and expiring-soon flags.

    Each entry includes:
      - ``days_remaining``: integer days until expiry (negative = already expired)
      - ``expiring_soon``: true if <= 30 days remaining
      - ``expired``: true if already past expiry

    ``unreachable`` names the enabled certificate providers this call could not read,
    so a partial answer cannot be mistaken for a complete one.
    """
    require_auth(request)
    with get_db_ctx() as conn:
        cert_providers = _certificate_provider_rows(conn)

    now         = datetime.datetime.utcnow()
    result      = []
    unreachable = []

    for p in cert_providers:
        try:
            provider = create_provider(p)
            certs    = provider.get_certificates()
        except Exception as e:
            add_log("error", f"Certificate expiry check {p['name']}: {e}")
            # A store that did not answer is not a store holding nothing. Dropping it here
            # and saying nothing left the page with a total, five counters and a provider
            # filter drawn entirely from whoever did answer: an estate with one certificate
            # expiring tomorrow behind an unreachable proxy read exactly like a clean one.
            # The identity travels so the page can say which store is missing; the error
            # stays in the journal, where it cannot put a URL or a credential on screen.
            unreachable.append({"id": p["id"], "name": p["name"], "type": p["type"]})
            continue

        for c in certs:
            expiry_raw = c.get("expiry_date") or c.get("expires_on") or c.get("valid_to")
            expiry_dt  = parse_expiry(expiry_raw)
            days_remaining: int | None = None
            expiring_soon = False
            expired       = False

            if expiry_dt is not None:
                delta         = expiry_dt - now
                days_remaining = delta.days
                expired        = days_remaining < 0
                expiring_soon  = not expired and days_remaining <= _EXPIRY_WARN_DAYS

            result.append(
                {
                    **_with_domain_names(c),
                    "provider_id":     p["id"],
                    "provider_name":   p["name"],
                    "days_remaining":  days_remaining,
                    "expiring_soon":   expiring_soon,
                    "expired":         expired,
                    "expiry_date_raw": expiry_raw,
                }
            )

    # Sort: expired first, then expiring soon, then by days remaining
    result.sort(key=lambda x: (
        not x["expired"],
        not x["expiring_soon"],
        x["days_remaining"] if x["days_remaining"] is not None else 9999,
    ))

    # One number for two states, deliberately: both need renewing, and a badge showing two
    # figures where an operator wants one would be worse. Nothing is lost by the merge --
    # `expired` and `expiring_soon` travel on every row -- but a reader that wants to say
    # "already broken" rather than "due soon" has to count the rows, because this figure
    # cannot. `certificateUrgency` on the frontend is the one that does.
    expiring_count = sum(1 for c in result if c["expiring_soon"] or c["expired"])
    return {
        "certificates": result,
        "total": len(result),
        "expiring_soon_count": expiring_count,
        "warn_threshold_days": _EXPIRY_WARN_DAYS,
        "unreachable": unreachable,
    }
