"""What Vauxtra accepts as a name, and which rule a refused name broke.

`is_valid_subdomain` and `is_valid_domain` answer yes or no; `subdomain_problem` and
`domain_problem` answer *which* rule was broken, as a short stable code. The two share one
implementation -- the boolean is `..._problem(...) is None` -- because a rule written twice
is a rule neither copy can be trusted to hold.

`fqdn_problem` answers the question neither of the other two can: each half may sit inside
its own limit while the name they make sits outside it, and only the pair knows that.

Those codes are a contract with the panel, which turns each into a sentence of its own
(`expose.validation.subdomain.<code>`). `frontend/src/lib/hostname.ts` carries the same
rules so the field can refuse before a round trip, and `tests/test_hostname_rules.py` runs
the same table through both.
"""

import ipaddress
import re

# One DNS label: what may sit between two dots. Case is folded before this is applied.
#
# The underscore is left out on purpose, and that is stricter than DNS itself. Measured on
# 2026-09-12 against a real Cloudflare zone: a label carrying an underscore was accepted by
# the API, served by the zone's own nameservers, and resolved by 1.1.1.1 and 8.8.8.8 alike
# in under five seconds. The name works. The certificate does not: Let's Encrypt refuses
# the order with `Domain name contains an invalid character`,
# where the same request for a hyphenated name succeeds. Vauxtra publishes services over
# HTTPS, so allowing the underscore would trade a refusal now for a route that resolves,
# answers, and can never hold a certificate. Widen this pattern only for a character that
# passes both halves of that test.
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


# The sentence each code becomes for a client that carries none of its own. The API answers
# in English on every route, and curl and `vauxtra_mcp` are clients too; the panel never
# reads these, it translates the code, so a word changed here changes nothing it shows.
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
    """The rule *value* breaks as a subdomain, or None when it breaks none.

    Dots are allowed: `grafana.metrics` under `example.com` publishes
    `grafana.metrics.example.com`, which every provider here already resolves by walking
    labels from the right to find the zone. A wildcard has to be a whole label and the
    leftmost one, which is the only position DNS gives it any meaning.
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
    """The rule the name the two halves make breaks, or None when it breaks none.

    Neither field can see the other, and each can sit inside its own 253-character limit
    while the name they make sits outside it. Nothing downstream caught that: the route was
    saved, and every provider then refused the record on its own, one push at a time, with
    the failure arriving as a provider error rather than as a name that was never publishable.
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


def is_valid_url(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(("http://", "https://"))
        and len(value) < 512
    )


def is_valid_tag_color(value: str) -> bool:
    return value in _COLOR_VALID
