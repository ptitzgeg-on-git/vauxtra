"""Certificate management endpoints.

Provides certificate listing and expiry monitoring across all NPM providers.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Request

from app.auth import require_auth
from app.models import get_db_ctx, add_log
from app.providers.factory import create_provider

router = APIRouter()

# Certificates expiring within this many days are flagged as "expiring soon"
_EXPIRY_WARN_DAYS = 30


def _parse_expiry(raw: str | None) -> datetime.datetime | None:
    """Parse a certificate expiry string into a datetime, or return None."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


@router.get("/api/certificates")
def list_certificates(request: Request):
    require_auth(request)
    with get_db_ctx() as conn:
        npm_providers = conn.execute(
            "SELECT * FROM providers WHERE type='npm' AND enabled=1"
        ).fetchall()

    result = []
    for p in npm_providers:
        try:
            provider = create_provider(p)
            certs = provider.get_certificates()
            for c in certs:
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
    """
    require_auth(request)
    with get_db_ctx() as conn:
        npm_providers = conn.execute(
            "SELECT * FROM providers WHERE type='npm' AND enabled=1"
        ).fetchall()

    now    = datetime.datetime.utcnow()
    result = []

    for p in npm_providers:
        try:
            provider = create_provider(p)
            certs    = provider.get_certificates()
        except Exception as e:
            add_log("error", f"Certificate expiry check {p['name']}: {e}")
            continue

        for c in certs:
            expiry_raw = c.get("expiry_date") or c.get("expires_on") or c.get("valid_to")
            expiry_dt  = _parse_expiry(expiry_raw)
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
                    **c,
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

    expiring_count = sum(1 for c in result if c["expiring_soon"] or c["expired"])
    return {
        "certificates": result,
        "total": len(result),
        "expiring_soon_count": expiring_count,
        "warn_threshold_days": _EXPIRY_WARN_DAYS,
    }
