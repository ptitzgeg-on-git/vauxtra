import re
import ipaddress

_SUBDOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$')
_HOSTNAME_RE  = re.compile(r'^[a-z0-9][a-z0-9\-\.]{0,253}[a-z0-9]$')
_COLOR_VALID  = {
    "blue", "teal", "green", "red", "orange", "purple",
    "cyan", "yellow", "pink", "lime", "indigo", "azure",
    "secondary", "dark",
}


def is_valid_subdomain(value: str) -> bool:
    return bool(_SUBDOMAIN_RE.match(value.lower())) if value else False


def is_valid_hostname(value: str) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME_RE.match(value.lower()))


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
