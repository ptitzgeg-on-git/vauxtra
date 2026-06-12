import ipaddress
import re

_SUBDOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$')
_HOSTNAME_RE  = re.compile(r'^[a-z0-9][a-z0-9\-\.]{0,253}[a-z0-9]$')
_DOMAIN_RE = re.compile(r'^[a-z0-9.-]+$')
_COLOR_VALID  = {
    "blue", "teal", "green", "red", "orange", "purple",
    "cyan", "yellow", "pink", "lime", "indigo", "azure",
    "secondary", "dark",
}


def is_valid_subdomain(value: str, *, allow_wildcard: bool = False) -> bool:
    if not value:
        return False
    val = value.strip().lower()
    if allow_wildcard and val == "*":
        return True
    return bool(_SUBDOMAIN_RE.match(val))


def is_valid_hostname(value: str) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME_RE.match(value.lower()))


def normalize_domain(value: str) -> str:
    return (value or "").strip().lower().rstrip(".")


def is_valid_domain(value: str, *, require_dot: bool = False) -> bool:
    val = normalize_domain(value)
    if not val:
        return False
    if require_dot and "." not in val:
        return False
    if any(token in val for token in ("://", "/", "@", "*")):
        return False
    if len(val) > 253 or ".." in val:
        return False
    if not _DOMAIN_RE.match(val):
        return False
    try:
        ipaddress.ip_address(val)
        return False
    except ValueError:
        pass

    labels = val.split(".")
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
    return True


def is_valid_port(value) -> bool:
    try:
        return 1 <= int(value) <= 65535
    except (TypeError, ValueError):
        return False


def is_valid_url(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(("http://", "https://"))
        and len(value) < 512
    )


def is_valid_tag_color(value: str) -> bool:
    return value in _COLOR_VALID
