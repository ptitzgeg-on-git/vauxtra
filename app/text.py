"""English wording that depends on a count, for server-side text (logs, webhooks, details).

The panel uses the browser's CLDR plurals; these strings never reach it. Replaces
`f"{n} service(s)"`.
"""


def plural(n: int, singular: str, many: str | None = None) -> str:
    """`1 service`, `3 services`, `0 services`. `many` is for irregular plurals."""
    return f"{n} {singular if n == 1 else (many or singular + 's')}"


def time_to_expiry(days_left: int, days_overdue: int) -> str:
    """`expires in 3 days`, `expires in less than a day`, `expired 47 days ago`.

    Takes two counts, each floored on its own side by the caller, because flooring one
    signed count would overstate the time since expiry.
    """
    if days_left < 0:
        if days_overdue < 1:
            return "expired less than a day ago"
        return f"expired {plural(days_overdue, 'day')} ago"
    if days_left == 0:
        return "expires in less than a day"
    return f"expires in {plural(days_left, 'day')}"


def verb(n: int, singular: str, plural_form: str) -> str:
    """The verb agreeing with `plural(n, ...)`: `uses` / `use`, `has` / `have`."""
    return singular if n == 1 else plural_form


def name_list(names: list[str], limit: int = 5) -> str:
    """`web, api, db`, or past `limit` names `web, api, db, mail, vpn, and 7 more`."""
    rest = len(names) - limit
    shown = ", ".join(names[:limit])
    return f"{shown}, and {rest} more" if rest > 0 else shown
