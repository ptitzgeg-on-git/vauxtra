"""Security utilities for Vauxtra."""

import re
from urllib.parse import urlparse

# The port a browser leaves out of `Origin` because it is the scheme's own.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def validate_cors_origins(origins_str: str, default_origins: str) -> list[str]:
    """
    Validate and parse CORS origins from environment variable.
    
    Ensures:
    - Valid URLs with scheme (http/https)
    - No localhost wildcards (*) or overly permissive patterns
    - Proper port numbers (1-65535)
    - No URL injection attempts
    - Normalised to the exact string a browser puts in `Origin`: a bare trailing slash
      is dropped, a default port for the scheme is dropped, an IPv6 literal keeps its
      brackets
    
    Args:
        origins_str: Comma-separated CORS origins from environment
        default_origins: Fallback if origins_str is empty
    
    Returns:
        List of validated CORS origins -- empty when nothing was configured, which is
        the same-origin deployment and not a failure.

    Raises:
        ValueError: If any origin is malformed or dangerous, or if a non-blank setting
            names no origin at all
    """
    origins_to_check = origins_str or default_origins
    parsed_origins = []

    for origin in origins_to_check.split(","):
        origin = origin.strip()
        if not origin:
            continue

        try:
            parsed = urlparse(origin)

            # ✅ Must have scheme (http/https)
            if not parsed.scheme or parsed.scheme not in ("http", "https"):
                raise ValueError(f"Invalid scheme: {parsed.scheme}. Must be http or https.")

            # ✅ Must have hostname
            if not parsed.hostname:
                raise ValueError(f"Missing hostname in: {origin}")

            # ✅ Reject wildcards and overly permissive patterns
            if "*" in parsed.hostname:
                raise ValueError(f"Wildcard origins not allowed: {origin}")

            # ✅ Validate port number if present
            if parsed.port is not None and not (1 <= parsed.port <= 65535):
                raise ValueError(f"Invalid port: {parsed.port}. Must be 1-65535.")

            # ✅ Ensure no path, query, or fragment
            #
            # An origin carries no path and a browser never sends one, but a single "/" is
            # what the address bar shows and therefore what gets pasted. It used to refuse
            # the setting -- and with it every other origin in the list, since one bad
            # entry refuses them all -- over a character that means nothing here. It is
            # accepted and dropped; a path with anything in it is not an origin and still
            # fails.
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError(f"Origins must not include path/query/fragment: {origin}")

            # ✅ Rebuild valid origin
            #
            # This string is compared to the browser's `Origin` header character for
            # character, so a rebuild that is merely equivalent is a rejection with no
            # message anywhere: the request fails in the browser, and the log says the
            # origin was validated. Two spellings used to come out of here matching
            # nothing -- an explicit default port, where `Origin` reads `https://host` and
            # never `https://host:443`, and an IPv6 literal, which `urlparse` returns
            # stripped of the brackets that make it an authority.
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            if parsed.port is not None and parsed.port != _DEFAULT_PORTS[parsed.scheme]:
                validated = f"{parsed.scheme}://{host}:{parsed.port}"
            else:
                validated = f"{parsed.scheme}://{host}"

            # Two spellings of one origin are one origin. Normalising is what makes that
            # reachable: `https://host/` and `https://host:443` arrive here identical, and
            # the duplicate would otherwise be counted in the "N allowed" line at startup.
            if validated not in parsed_origins:
                parsed_origins.append(validated)

        except ValueError as e:
            raise ValueError(f"Invalid CORS origin '{origin}': {e}")
        except Exception as e:
            raise ValueError(f"Error parsing CORS origin '{origin}': {e}")

    # Nothing configured is an answer, not an accident: the interface is served by this
    # same application, so the deployment the documentation recommends allows no
    # cross-origin caller and reaches this line with an empty list. Raising here made that
    # recommended configuration write an `error` at every single boot -- an alarm nobody
    # can silence by fixing anything, which is the fastest way to teach an operator to
    # stop reading the log. A setting that is *not* blank and still names no origin is a
    # different animal: something was asked for and nothing took effect, so it still
    # raises.
    if not parsed_origins and origins_to_check.strip():
        raise ValueError(f"No valid CORS origins in: {origins_to_check!r}")

    return parsed_origins


MIN_PASSWORD_LENGTH = 12


def validate_password_strength(
    password: str, min_length: int = MIN_PASSWORD_LENGTH
) -> tuple[bool, str]:
    """The admin password rule -- and the only place it is written.

    Until now there were two. This function asked for 12 characters and four character
    classes and was called by nothing but its own tests, while `setup_password` and
    `change_password` each carried an inline `len(password) < 8`. The policy that ran was
    the one nobody had thought about.

    The rule that survives is length, not composition. NIST SP 800-63B stopped recommending
    character-class requirements because of what they do to real passwords: asked for an
    uppercase, a digit and a symbol, people produce `Password1!` -- eleven characters a
    wordlist finds instantly, and which the old rule accepted the moment it reached twelve.
    A floor of twelve with no class rules leaves a passphrase as the obvious way to pass,
    and a passphrase is what we actually want.

    The distinct-character check is the one thing kept from the old spirit: it costs nothing
    and it refuses `aaaaaaaaaaaa` and `abababababab`, which length alone waves through.

    Returns (is_valid, error_message); the message is shown to the user as-is.
    """
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters"

    if len(set(password)) < 5:
        return False, "Password must use more than a few different characters"

    return True, ""


def mask_secret_url(url: str) -> str:
    """Reduce a credential-bearing URL to what is safe to show and to log.

    An Apprise URL *is* the credential: `discord://<id>/<token>`, `tgram://<bot
    token>/<chat id>`, `slack://<tokens>/<channel>`. There is no separate password
    field to clear -- masking is the only way to name a webhook without handing over
    the ability to post to it.

    The authority is kept only when a `@` proves it is a server address and the
    credential sits in front of it (`ntfy://user:pass@ntfy.home.lan/topic` ->
    `ntfy://***@ntfy.home.lan`). Without a `@` the authority is opaque and is itself
    half the secret, so it goes too. The scheme always survives: it is what tells the
    operator which of their webhooks a line is about.
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


def sanitize_domain(domain: str) -> str:
    """
    Sanitize domain name to prevent injection attacks.
    
    Removes/escapes potentially dangerous characters while preserving valid DNS names.
    """
    # Allow only alphanumeric, dots, hyphens
    sanitized = re.sub(r"[^a-zA-Z0-9.\-_*]", "", domain)

    # Ensure doesn't start/end with hyphen
    sanitized = sanitized.strip("-")

    # Collapse multiple dots
    while ".." in sanitized:
        sanitized = sanitized.replace("..", ".")

    return sanitized or "invalid.domain"
