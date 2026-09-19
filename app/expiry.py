"""One certificate expiry string, read the same way by everyone who reads it.

A proxy hands back `expires_on` as whatever its own storage layer formats. Three things in
Vauxtra then read it: the `/api/certificates/expiry` route, which turns it into
`days_remaining` and through that into `expiring_soon_count` -- the sidebar badge, the
dashboard tile and the "needs attention" entry; the scheduler's expiry scan, which decides
between a CRITICAL and a WARNING line in the activity log; and `parseBackendTimestamp` in
the browser, which draws the countdown on the certificates page.

They used to read it three different ways, and a date written `2027-01-01T00:00:00+00:00`
-- what `datetime.isoformat()` writes, and what RFC 3339 asks for -- was accepted by two of
them and refused by the route. So the route reported the estate clean, and the badge stayed
dark, while the log filled with CRITICAL lines about that certificate and the certificates
page drew its countdown in red. Three surfaces of one product, three verdicts, one string.

`fromisoformat` is the whole rule here, and since 3.11 it is the full ISO 8601 parser: the
`Z`, an offset, a space separator, fractional seconds, a bare date. What this module adds
is the two decisions around it that the callers must not each make for themselves.
"""

import datetime


def parse_expiry(raw: object) -> datetime.datetime | None:
    """A certificate expiry string as a naive UTC datetime, or None if it is not a date.

    Naive, because every comparison downstream is against `datetime.utcnow()`, and mixing
    an aware value into that subtraction does not read wrong, it raises `TypeError`. In the
    scheduler that exception escaped the per-certificate guard and reached the scan's outer
    handler, so one certificate stamped `-03:00` ended the expiry scan for the whole estate
    -- every provider after it, every certificate after it, on every run. The offset is
    applied before it is dropped, so the instant survives and only the label goes.

    `None` means "no date here", which both callers already tell apart from trouble and
    from safety: the route leaves `days_remaining` null and the page draws it as unknown,
    the scheduler skips the entry. Anything that is not a string lands there too, which is
    what a provider bug looks like from here -- and it used to look like a 500, because
    `strptime` raises `TypeError` rather than `ValueError` on a non-string, so one bad
    value took the route down instead of the one certificate.
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
