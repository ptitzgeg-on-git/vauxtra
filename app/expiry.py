"""Shared parser for certificate expiry strings (API route, scheduler, UI)."""

import datetime


def parse_expiry(raw: object) -> datetime.datetime | None:
    """Parse an expiry string to a naive UTC datetime, or None if it is not a date.

    Offsets are applied then dropped because callers compare with utcnow().
    Non-string input returns None instead of raising.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(datetime.UTC).replace(tzinfo=None)
