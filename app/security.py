"""Security utilities for Vauxtra."""

import re
from urllib.parse import urlparse


def validate_cors_origins(origins_str: str, default_origins: str) -> list[str]:
    """
    Validate and parse CORS origins from environment variable.
    
    Ensures:
    - Valid URLs with scheme (http/https)
    - No localhost wildcards (*) or overly permissive patterns
    - Proper port numbers (1-65535)
    - No URL injection attempts
    
    Args:
        origins_str: Comma-separated CORS origins from environment
        default_origins: Fallback if origins_str is empty
    
    Returns:
        List of validated CORS origins
    
    Raises:
        ValueError: If any origin is malformed or dangerous
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

            # ✅ Rebuild valid origin
            if parsed.port:
                validated = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            else:
                validated = f"{parsed.scheme}://{parsed.hostname}"

            # ✅ Ensure no path, query, or fragment
            if parsed.path or parsed.query or parsed.fragment:
                raise ValueError(f"Origins must not include path/query/fragment: {origin}")

            parsed_origins.append(validated)

        except ValueError as e:
            raise ValueError(f"Invalid CORS origin '{origin}': {e}")
        except Exception as e:
            raise ValueError(f"Error parsing CORS origin '{origin}': {e}")

    if not parsed_origins:
        raise ValueError("No valid CORS origins provided")

    return parsed_origins


def validate_password_strength(password: str, min_length: int = 12) -> tuple[bool, str]:
    """
    Validate password strength for sensitive operations.
    
    Requirements:
    - Minimum length (default 12)
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    
    Args:
        password: Password to validate
        min_length: Minimum required length
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters"

    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"

    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"

    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"

    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};:'\",.<>?/\\|`~]", password):
        return False, "Password must contain at least one special character"

    return True, ""


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
