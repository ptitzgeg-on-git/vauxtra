import hashlib
import hmac
import logging
import os
import secrets
import sqlite3

from fastapi import HTTPException, Request

from app.config import APP_PASSWORD

_logger = logging.getLogger(__name__)

_ALLOW_PLAINTEXT_APP_PASSWORD = os.environ.get(
    "ALLOW_PLAINTEXT_APP_PASSWORD",
    "false",
).strip().lower() in ("1", "true", "yes")

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


# Written the first time a password is set, and never removed afterwards. It is the only
# thing that can tell "this instance was deliberately left open" apart from "this instance
# had a password and no longer does". `setup_completed` cannot: adding a provider writes it
# too, and the wizard offers a *Skip* button on the password step, so a perfectly legitimate
# passwordless install also carries it.
AUTH_MODE_KEY = "auth_mode"
AUTH_MODE_PASSWORD = "password"


def _read_auth_settings() -> dict[str, str]:
    """Read the hash and the mode marker in a single connection.

    Raises on a database it cannot read, so callers can decide -- and they all decide the
    same way: closed. Returning an empty dict here would make an unreadable database look
    exactly like a passwordless install and hand out the admin scope.
    """
    from app.models import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?)",
            ("app_password_hash", AUTH_MODE_KEY),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def _get_db_password_hash() -> str:
    """Return the password hash stored in settings, or empty string."""
    try:
        return _read_auth_settings().get("app_password_hash", "")
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return ""


def has_password_configured() -> bool:
    """True if a password is set (env var OR database)."""
    return bool(APP_PASSWORD) or bool(_get_db_password_hash())


def password_was_configured_once() -> bool:
    """True if this instance has ever had an admin password.

    Fails **closed**: a database we cannot read is reported as "yes, there was a password",
    so a broken or missing database locks the API instead of opening it. The previous code
    read the hash, got an empty string on any error, and concluded the instance was an
    open install -- a database failure was a way in.
    """
    if APP_PASSWORD:
        return True
    try:
        return _read_auth_settings().get(AUTH_MODE_KEY, "") == AUTH_MODE_PASSWORD
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return True


def mark_password_configured(conn) -> None:
    """Record that this instance is password-protected. Call inside the caller's transaction."""
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (AUTH_MODE_KEY, AUTH_MODE_PASSWORD),
    )


def auth_is_downgraded() -> bool:
    """A password was set on this instance, and it is gone.

    Nothing in the application can produce this state: `/api/settings/reset` and
    `/api/restore` both preserve the hash, and no route deletes it. It means the database
    was edited by hand, replaced by an older file, or partially restored -- and until this
    check existed, the instance answered every request with the admin scope and said
    nothing at all about it.
    """
    return password_was_configured_once() and not has_password_configured()


def is_setup_incomplete() -> bool:
    """True while the instance is still an empty install that may be configured anonymously.

    The `setup_completed` flag alone cannot decide this: it is only written at the very last
    step of the wizard, so an abandoned wizard (closed tab, refresh, configuration done over
    REST or through the MCP bridge) would leave the setup bypass open forever. An instance
    that already has a password or a provider is configured, whatever the flag says.

    This mirrors the `setup_required` condition already exposed by GET /api/auth/me, which
    previously disagreed with the server-side check: the UI showed a login screen while the
    API stayed anonymous.

    Fails closed: any error means auth is enforced.
    """
    try:
        if has_password_configured():
            return False
        # A password existed and is gone: this is not a fresh install, whatever the rest of
        # the database says. Re-opening the wizard here would let anyone set a new admin
        # password on an instance that already has data in it.
        if password_was_configured_once():
            return False
        from app.models import get_db
        conn = get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='setup_completed'").fetchone()
            if row and row["value"] == "1":
                return False
            provider_count = conn.execute("SELECT COUNT(*) as c FROM providers").fetchone()["c"]
            return provider_count == 0
        finally:
            conn.close()
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return False


def check_password(candidate: str) -> bool:
    """Verify a password against env hash/plaintext or DB hash.

    Rules:
    - If APP_PASSWORD is PBKDF2-formatted, it is treated as a hash.
    - Plaintext APP_PASSWORD is only honored when explicitly allowed.
    - Database hash remains the safe default fallback.
    """
    if APP_PASSWORD:
        if APP_PASSWORD.startswith("pbkdf2:"):
            return verify_password_hash(candidate, APP_PASSWORD)
        if _ALLOW_PLAINTEXT_APP_PASSWORD:
            return hmac.compare_digest(candidate, APP_PASSWORD)
        # Say so. An operator who sets a plaintext APP_PASSWORD gets a 401 on a password
        # that is, from their point of view, exactly the one they configured -- and the
        # setup wizard refuses to run because `has_password_configured()` sees the variable
        # is not empty. Without this line the whole failure is invisible.
        _logger.error(
            "APP_PASSWORD is set but is not a PBKDF2 hash, and ALLOW_PLAINTEXT_APP_PASSWORD "
            "is not enabled: this login cannot succeed. Generate a hash with "
            "`python -c \"from app.auth import hash_password; print(hash_password('...'))\"` "
            "and put that value in APP_PASSWORD, or set ALLOW_PLAINTEXT_APP_PASSWORD=true "
            "to accept the plaintext one."
        )
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
        if password_was_configured_once():
            return None  # the hash vanished -- refuse, do not fall back to anonymous admin
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
        if auth_is_downgraded():
            # Say which failure this is. A bare "Unauthorized" on an instance whose password
            # has disappeared sends the operator hunting for a wrong password for an hour.
            raise HTTPException(
                status_code=401,
                detail=(
                    "This instance was configured with an admin password and the hash is no "
                    "longer in the database. Access is refused rather than granted "
                    "anonymously. Restore the database, or set APP_PASSWORD to a "
                    "'pbkdf2:'-prefixed hash to regain access."
                ),
            )
        raise HTTPException(status_code=401, detail="Unauthorized")
    if scope is None:
        return
    if not _scope_satisfies(ctx["scopes"], scope):
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient scope: '{scope}' required",
        )


def require_auth_or_setup(request: Request, scope: str | None = None) -> None:
    """Allow access during initial setup, otherwise enforce auth/scope."""
    if is_setup_incomplete():
        return
    require_auth(request, scope=scope)
