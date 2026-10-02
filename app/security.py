"""Security utilities for Vauxtra."""

import re
from urllib.parse import urlparse

# The port a browser leaves out of `Origin` because it is the scheme's own.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def validate_cors_origins(origins_str: str, default_origins: str) -> list[str]:
    """Parse and validate CORS origins from the environment.

    Each origin needs an http(s) scheme and a valid port, no wildcard, path, query or
    fragment, and is normalized to what a browser sends in `Origin` (trailing slash and
    default port dropped, IPv6 brackets kept). Falls back to `default_origins` when
    `origins_str` is empty. Returns the list, empty for same-origin deployments.
    Raises ValueError on a malformed origin, or a non-blank setting naming none.
    """
    origins_to_check = origins_str or default_origins
    parsed_origins = []

    for origin in origins_to_check.split(","):
        origin = origin.strip()
        if not origin:
            continue

        try:
            parsed = urlparse(origin)

            if not parsed.scheme or parsed.scheme not in ("http", "https"):
                raise ValueError(f"Invalid scheme: {parsed.scheme}. Must be http or https.")

            if not parsed.hostname:
                raise ValueError(f"Missing hostname in: {origin}")

            if "*" in parsed.hostname:
                raise ValueError(f"Wildcard origins not allowed: {origin}")

            if parsed.port is not None and not (1 <= parsed.port <= 65535):
                raise ValueError(f"Invalid port: {parsed.port}. Must be 1-65535.")

            # A lone trailing "/" (as pasted from the address bar) is accepted and dropped.
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError(f"Origins must not include path/query/fragment: {origin}")

            # Rebuilt exactly as the browser's Origin header spells it, since it is compared
            # character for character.
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            if parsed.port is not None and parsed.port != _DEFAULT_PORTS[parsed.scheme]:
                validated = f"{parsed.scheme}://{host}:{parsed.port}"
            else:
                validated = f"{parsed.scheme}://{host}"

            # Deduplicate after normalization.
            if validated not in parsed_origins:
                parsed_origins.append(validated)

        except ValueError as e:
            raise ValueError(f"Invalid CORS origin '{origin}': {e}")
        except Exception as e:
            raise ValueError(f"Error parsing CORS origin '{origin}': {e}")

    # Empty is the normal same-origin setup, not an error; only a non-blank setting that
    # yields nothing raises.
    if not parsed_origins and origins_to_check.strip():
        raise ValueError(f"No valid CORS origins in: {origins_to_check!r}")

    return parsed_origins


MIN_PASSWORD_LENGTH = 12


def validate_password_strength(
    password: str, min_length: int = MIN_PASSWORD_LENGTH
) -> tuple[bool, str]:
    """Single source of the admin password rule.

    Length floor plus a minimum of distinct characters; no character-class rules
    (NIST SP 800-63B). Returns (is_valid, error_message), message shown to the user as is.
    """
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters"

    if len(set(password)) < 5:
        return False, "Password must use more than a few different characters"

    return True, ""


def mask_secret_url(url: str) -> str:
    """Mask a credential-bearing URL (e.g. Apprise) for display and logs.

    The scheme is kept. The host is kept only after an `@` (`ntfy://***@ntfy.home.lan`);
    without one the authority may itself be the secret, so it is masked too.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    scheme, separator, rest = raw.partition("://")
    if not separator:
        return "***"
    authority = rest.split("?", 1)[0].split("#", 1)[0].split("/", 1)[0]
    host = authority.split("@")[-1] if "@" in authority else ""
    return f"{scheme}://***@{host}" if host else f"{scheme}://***"


# A query parameter whose name ends in one of these carries a credential: Technitium sends its
# session token as `token`, Pi-hole v5 its API key as `auth`.
_SECRET_QUERY = re.compile(
    r"([?&][A-Za-z0-9_.-]*?(?:token|auth|key|pass|password|secret|sid)=)[^&#\s'\"<>)]+",
    re.IGNORECASE,
)


def redact_query_secrets(text: str) -> str:
    """Return `text` with the value of every credential-bearing query parameter masked.

    requests quotes the full URL in its exceptions, and some integrations (Technitium,
    Pi-hole v5) authenticate in the query string. Matching is on parameter name only.
    """
    return _SECRET_QUERY.sub(r"\1***", text or "")


def sanitize_domain(domain: str) -> str:
    """Keep only letters, digits, '.', '-', '_' and '*', trim hyphens and collapse dots.

    Returns "invalid.domain" when nothing is left.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9.\-_*]", "", domain)

    # Ensure doesn't start/end with hyphen
    sanitized = sanitized.strip("-")

    # Collapse multiple dots
    while ".." in sanitized:
        sanitized = sanitized.replace("..", ".")

    return sanitized or "invalid.domain"
