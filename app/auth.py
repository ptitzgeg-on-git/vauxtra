import hashlib
import hmac
import secrets

from fastapi import Request, HTTPException
from app.config import APP_PASSWORD

# PBKDF2 parameters (OWASP 2023 recommendations)
PBKDF2_ITERATIONS = 600_000
PBKDF2_HASH_NAME = "sha256"
PBKDF2_SALT_LENGTH = 16
PBKDF2_DK_LENGTH = 32


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt.
    
    Returns a string in format: pbkdf2:sha256:iterations$salt_hex$hash_hex
    """
    salt = secrets.token_bytes(PBKDF2_SALT_LENGTH)
    dk = hashlib.pbkdf2_hmac(
        PBKDF2_HASH_NAME,
        password.encode(),
        salt,
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_DK_LENGTH,
    )
    return f"pbkdf2:{PBKDF2_HASH_NAME}:{PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password_hash(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash.
    
    Supports PBKDF2 format: pbkdf2:sha256:iterations$salt$hash
    
    Note: Legacy SHA-256 format removed in v2.0 — users with old hashes
    must reset their password via `vauxtra reset-password` CLI or re-run setup.
    """
    if not stored_hash:
        return False
    
    # PBKDF2 format only
    if stored_hash.startswith("pbkdf2:"):
        try:
            parts = stored_hash.split("$")
            if len(parts) != 3:
                return False
            header, salt_hex, hash_hex = parts
            _, hash_name, iterations_str = header.split(":")
            iterations = int(iterations_str)
            salt = bytes.fromhex(salt_hex)
            expected_hash = bytes.fromhex(hash_hex)
            
            dk = hashlib.pbkdf2_hmac(
                hash_name,
                password.encode(),
                salt,
                iterations,
                dklen=len(expected_hash),
            )
            return hmac.compare_digest(dk, expected_hash)
        except (ValueError, KeyError):
            return False
    
    return False


def _get_db_password_hash() -> str:
    """Return the password hash stored in settings, or empty string."""
    try:
        from app.models import get_db
        conn = get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='app_password_hash'").fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def has_password_configured() -> bool:
    """True if a password is set (env var OR database)."""
    return bool(APP_PASSWORD) or bool(_get_db_password_hash())


def check_password(candidate: str) -> bool:
    """Verify a password against env var (plaintext) or DB hash."""
    if APP_PASSWORD:
        return hmac.compare_digest(candidate, APP_PASSWORD)
    db_hash = _get_db_password_hash()
    if db_hash:
        return verify_password_hash(candidate, db_hash)
    return False


def get_session(request: Request) -> dict:
    try:
        return request.session
    except AssertionError:
        # Direct function calls in tests may construct a raw Request scope
        # without SessionMiddleware — treat session as empty in that case.
        return {}


# Scope hierarchy: admin > write > read. A granted scope satisfies any required
# scope at its level or below (e.g. admin satisfies write; write satisfies read).
_SCOPE_LEVEL = {"read": 0, "write": 1, "admin": 2}


def _scope_satisfies(granted: list[str], required: str) -> bool:
    req_level = _SCOPE_LEVEL.get(required)
    if req_level is None:
        return False
    return any(_SCOPE_LEVEL.get(g, -1) >= req_level for g in granted)


def _get_auth_context(request: Request) -> dict | None:
    """Return {kind, scopes} if authenticated, else None.

    Sessions (UI login) and the no-password-configured mode are granted
    the 'admin' scope implicitly. Bearer tokens carry the scopes stored
    on the API key row.
    """
    if not has_password_configured():
        return {"kind": "open", "scopes": ["admin"]}
    if get_session(request).get("authenticated") is True:
        return {"kind": "session", "scopes": ["admin"]}
    # Bearer token authentication (for MCP and API integrations)
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        if token:
            from app.api.api_keys import verify_api_key
            key_info = verify_api_key(token)
            if key_info:
                return {"kind": "api_key", "scopes": list(key_info.get("scopes") or [])}
    return None


def is_authenticated(request: Request) -> bool:
    return _get_auth_context(request) is not None


def require_auth(request: Request, scope: str | None = None) -> None:
    ctx = _get_auth_context(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if scope is None:
        return
    if not _scope_satisfies(ctx["scopes"], scope):
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient scope: '{scope}' required",
        )
