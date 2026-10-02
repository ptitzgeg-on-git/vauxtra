"""Hostname validation, with a stable code naming the rule a refused name broke.

`*_problem` functions return the code (the panel translates it); the `is_valid_*`
booleans are built on them so each rule exists once. `fqdn_problem` checks the joined
name, which neither half can. frontend/src/lib/hostname.ts mirrors these rules and
tests/test_hostname_rules.py runs both against one table.
"""

import ipaddress
import re

# One DNS label, after case folding. Underscores are refused on purpose, stricter than
# DNS: Let's Encrypt will not issue a certificate for such a name, and services are
# published over HTTPS.
_LABEL_RE = re.compile(r"^[a-z0-9-]+$")
_HOSTNAME_RE  = re.compile(r'^[a-z0-9][a-z0-9\-\.]{0,253}[a-z0-9]$')
_COLOR_VALID  = {
    "blue", "teal", "green", "red", "orange", "purple",
    "cyan", "yellow", "pink", "lime", "indigo", "azure",
    "secondary", "dark",
}

# Every code `subdomain_problem` can return, in the order it tests them. Exported so the
# parity test and the locale files have one list to check themselves against.
SUBDOMAIN_PROBLEMS = (
    "empty",
    "too_long",
    "dot_edge",
    "wildcard",
    "charset",
    "label_length",
    "hyphen_edge",
)

# Same, for `domain_problem`.
DOMAIN_PROBLEMS = (
    "empty",
    "url",
    "wildcard",
    "too_long",
    "no_dot",
    "ip_address",
    "dot_edge",
    "charset",
    "label_length",
    "hyphen_edge",
)

# Same, for `fqdn_problem`. One code today; the tuple exists so the panel, the locale files
# and the parity test check themselves against a list rather than against a literal.
FQDN_PROBLEMS = ("too_long",)


# English sentence per code for API clients; the panel translates the code instead.
SUBDOMAIN_REASONS = {
    "empty": "a subdomain is required",
    "too_long": "a subdomain is 253 characters at most",
    "dot_edge": "a subdomain may not start or end with a dot, nor hold two in a row",
    "wildcard": 'a wildcard is a part of its own and the leftmost one: "*" or "*.app"',
    "charset": "a subdomain holds lowercase letters, digits, hyphens and dots only",
    "label_length": "each part of a subdomain is 63 characters at most",
    "hyphen_edge": "no part of a subdomain may start or end with a hyphen",
}

DOMAIN_REASONS = {
    "empty": "a domain is required",
    "url": "a domain is a name, not an address: no scheme, no path, no @",
    "wildcard": "a wildcard belongs in the subdomain, not in the domain",
    "too_long": "a domain is 253 characters at most",
    "no_dot": "a domain needs at least one dot",
    "ip_address": "an IP address is not a domain",
    "dot_edge": "a domain may not start or end with a dot, nor hold two in a row",
    "charset": "a domain holds lowercase letters, digits, hyphens and dots only",
    "label_length": "each part of a domain is 63 characters at most",
    "hyphen_edge": "no part of a domain may start or end with a hyphen",
}

FQDN_REASONS = {
    "too_long": "a subdomain and a domain make a name of 253 characters at most",
}


def _label_problem(label: str) -> str | None:
    """The rule a single DNS label breaks, wildcards already handled by the caller."""
    if not label:
        return "dot_edge"
    if "*" in label:
        return "wildcard"
    if not _LABEL_RE.match(label):
        return "charset"
    if len(label) > 63:
        return "label_length"
    if label.startswith("-") or label.endswith("-"):
        return "hyphen_edge"
    return None


def subdomain_problem(value: str, *, allow_wildcard: bool = False) -> str | None:
    """Return the rule `value` breaks as a subdomain, or None.

    Dots are allowed (multi-label subdomains). A wildcard must be the whole leftmost label.
    """
    val = (value or "").strip().lower()
    if not val:
        return "empty"
    if len(val) > 253:
        return "too_long"
    for index, label in enumerate(val.split(".")):
        if label == "*":
            if not allow_wildcard or index != 0:
                return "wildcard"
            continue
        problem = _label_problem(label)
        if problem:
            return problem
    return None


def is_valid_subdomain(value: str, *, allow_wildcard: bool = False) -> bool:
    return subdomain_problem(value, allow_wildcard=allow_wildcard) is None


def is_valid_hostname(value: str) -> bool:
    # `ip_address` accepts an IPv6 scope id made of almost any text, quotes and newlines
    # included, and the target ends up inside the proxy's nginx configuration.
    if not value or "%" in value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME_RE.match(value.lower()))


def normalize_domain(value: str) -> str:
    return (value or "").strip().lower().rstrip(".")


def domain_problem(value: str, *, require_dot: bool = False) -> str | None:
    """The rule *value* breaks as a domain, or None when it breaks none."""
    val = normalize_domain(value)
    if not val:
        return "empty"
    if any(token in val for token in ("://", "/", "@")):
        return "url"
    if "*" in val:
        return "wildcard"
    if len(val) > 253:
        return "too_long"
    if require_dot and "." not in val:
        return "no_dot"
    try:
        ipaddress.ip_address(val)
        return "ip_address"
    except ValueError:
        pass
    for label in val.split("."):
        problem = _label_problem(label)
        if problem:
            return problem
    return None


def is_valid_domain(value: str, *, require_dot: bool = False) -> bool:
    return domain_problem(value, require_dot=require_dot) is None


def normalize_subdomain(value: str) -> str:
    """What `ServiceIn` stores, so the rule below measures the name that gets published."""
    return (value or "").strip().lower()


def fqdn_problem(subdomain: str, domain: str) -> str | None:
    """Return the rule the joined name breaks (e.g. total length), or None.

    Each half can be within its own limit while the joined name is not.
    """
    joined = f"{normalize_subdomain(subdomain)}.{normalize_domain(domain)}".strip(".")
    return "too_long" if len(joined) > 253 else None


def is_valid_fqdn(subdomain: str, domain: str) -> bool:
    return fqdn_problem(subdomain, domain) is None


def is_valid_port(value) -> bool:
    try:
        return 1 <= int(value) <= 65535
    except (TypeError, ValueError):
        return False


# Port of a DNS-only service: nothing forwards to it and nothing probes it. 0 because the
# column is NOT NULL and readers expect an integer.
NO_PORT = 0


def is_valid_service_port(value) -> bool:
    """A service port: 1 to 65535, or `NO_PORT` for a DNS-only service.

    The range is written out because the API/MCP parity gate reads the bounds from this
    comparison. Whether 0 is allowed depends on other fields, so ServiceIn checks that.
    """
    try:
        return 0 <= int(value) <= 65535
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
