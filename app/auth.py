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
    """Hash a password with PBKDF2-HMAC-SHA256 and a random salt.

    Format: pbkdf2:sha256:iterations$salt_hex$hash_hex
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
    """Verify a password against a stored pbkdf2:sha256:iterations$salt$hash string.

    Any other format verifies as False. To recover, clear app_password_hash and auth_mode
    from settings and re-run the wizard, or set APP_PASSWORD to a pbkdf2: hash.
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


# Written when a password is first set and never removed: the only way to tell a
# deliberately open install from one that lost its password. setup_completed cannot,
# since the wizard lets the password step be skipped.
AUTH_MODE_KEY = "auth_mode"
AUTH_MODE_PASSWORD = "password"

# Bumped whenever every open session must stop being valid. A session cookie carries the
# epoch it was opened in and is refused once the two disagree.
SESSION_EPOCH_KEY = "session_epoch"


def _read_auth_settings() -> dict[str, str]:
    """Read the password hash and auth mode on one connection.

    Raises on an unreadable database so callers fail closed; {} would look like an open install.
    """
    from app.models import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?, ?)",
            ("app_password_hash", AUTH_MODE_KEY, SESSION_EPOCH_KEY),
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

    Fails closed: an unreadable database counts as "yes", locking the API.
    """
    if APP_PASSWORD:
        return True
    try:
        return _read_auth_settings().get(AUTH_MODE_KEY, "") == AUTH_MODE_PASSWORD
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return True


def current_session_epoch() -> int:
    """Session epoch a cookie must carry to be valid. Fails closed: -1 on a database error,
    which no session matches.
    """
    try:
        return int(_read_auth_settings().get(SESSION_EPOCH_KEY, "0") or "0")
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error):
        return -1


def bump_session_epoch(conn) -> None:
    """Invalidate every open session (used on password change). Runs in the caller's
    transaction.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (SESSION_EPOCH_KEY,)
    ).fetchone()
    try:
        current = int((row["value"] if row else "0") or "0")
    except (TypeError, ValueError):
        current = 0
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (SESSION_EPOCH_KEY, str(current + 1)),
    )


def mark_password_configured(conn) -> None:
    """Record that this instance is password-protected. Call inside the caller's transaction."""
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (AUTH_MODE_KEY, AUTH_MODE_PASSWORD),
    )


def auth_is_downgraded() -> bool:
    """True when a password was set on this instance and its hash is gone.

    No route can cause this (reset and restore keep the hash), so it means the database
    was edited or replaced. Access is then refused rather than granted anonymously.
    """
    return password_was_configured_once() and not has_password_configured()


def is_setup_incomplete() -> bool:
    """True while the instance is an empty install that may be configured anonymously.

    Not just setup_completed, which an abandoned wizard never writes: a password or a
    provider also ends setup. Matches setup_required in GET /api/auth/me. Fails closed.
    """
    try:
        if has_password_configured():
            return False
        # A lost password is not a fresh install: reopening the wizard would let anyone
        # set a new admin password.
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
    """Verify a password against APP_PASSWORD or the database hash.

    A pbkdf2-formatted APP_PASSWORD is a hash; a plaintext one is only honoured when
    explicitly allowed. Otherwise the database hash decides.
    """
    if APP_PASSWORD:
        if APP_PASSWORD.startswith("pbkdf2:"):
            return verify_password_hash(candidate, APP_PASSWORD)
        if _ALLOW_PLAINTEXT_APP_PASSWORD:
            return hmac.compare_digest(candidate, APP_PASSWORD)
        # Log it: otherwise a plaintext APP_PASSWORD fails silently with 401.
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


def password_is_env_managed() -> bool:
    """True when APP_PASSWORD decides logins, so the stored hash is never read.

    Password changes must be refused in that case. Plaintext APP_PASSWORD counts only when
    its opt-in is on; otherwise check_password falls through to the database.
    """
    if not APP_PASSWORD:
        return False
    return APP_PASSWORD.startswith("pbkdf2:") or _ALLOW_PLAINTEXT_APP_PASSWORD


def get_session(request: Request) -> dict:
    try:
        return request.session
    except AssertionError:
        # Direct function calls in tests may construct a raw Request scope
        # without SessionMiddleware; treat the session as empty then.
        return {}


# Scope hierarchy: admin > write > read; a scope satisfies any requirement at or below it.
# An unknown scope ranks -1 and satisfies nothing. The vocabulary is repeated in
# app/api/api_keys.py; tests/test_api_key_scope_vocabulary.py keeps them in sync.
_SCOPE_LEVEL = {"read": 0, "write": 1, "admin": 2}


def _scope_satisfies(granted: list[str], required: str) -> bool:
    req_level = _SCOPE_LEVEL.get(required)
    if req_level is None:
        return False
    return any(_SCOPE_LEVEL.get(g, -1) >= req_level for g in granted)


def _get_auth_context(request: Request) -> dict | None:
    """Return {kind, scopes} if authenticated, else None.

    Sessions and the no-password mode get the admin scope; API keys carry their stored
    scopes.
    """
    if not has_password_configured():
        if password_was_configured_once():
            return None  # the hash vanished -- refuse, do not fall back to anonymous admin
        return {"kind": "open", "scopes": ["admin"]}
    session = get_session(request)
    if session.get("authenticated") is True:
        # The session must carry the current epoch; cookies without one require a re-login.
        if session.get("epoch") == current_session_epoch():
            return {"kind": "session", "scopes": ["admin"]}
        # Drop it rather than merely ignore it: the browser gets an empty cookie back and
        # stops presenting a credential that will never be accepted again.
        session.clear()
    # Bearer token authentication (for MCP and API integrations)
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        if token:
            from app.api.api_keys import verify_api_key
            key_info = verify_api_key(token)
            if key_info:
                # Scopes are normalized by verify_api_key (_split_scopes); a bare split would
                # silently drop padded scopes. Pinned by ScopeReadingIsCoupled.
                scopes = list(key_info.get("scopes") or [])
                # A key with no known scope is refused even on unscoped routes, which never
                # reach _scope_satisfies.
                known = [s for s in scopes if s in _SCOPE_LEVEL]
                if not known:
                    # Say which failure this is: from the caller the 401 is otherwise
                    # indistinguishable from a revoked key.
                    _logger.warning(
                        "API key '%s' carries no scope this build knows (stored: %s), so it "
                        "can authorize nothing and is refused. Revoke it and create a "
                        "replacement granting at least one of %s.",
                        key_info.get("name", "?"),
                        scopes or "nothing",
                        sorted(_SCOPE_LEVEL),
                    )
                    return None
                return {"kind": "api_key", "scopes": scopes}
    return None


def is_authorized(request: Request, scope: str | None = None) -> bool:
    """Like require_auth but returns a bool instead of raising.

    Used by the log stream to re-check the same scope on every tick.
    """
    ctx = _get_auth_context(request)
    if ctx is None:
        return False
    if scope is None:
        return True
    return _scope_satisfies(ctx["scopes"], scope)


def is_authenticated(request: Request) -> bool:
    """True when the caller presents any credential this build accepts, whatever its reach."""
    return is_authorized(request)


def require_auth(request: Request, scope: str | None = None) -> None:
    """Authenticate the request or raise.

    401 when there is no valid credential (with a specific message when the password hash
    has disappeared), 403 when the credential lacks `scope`. scope=None accepts any
    credential.
    """
    ctx = _get_auth_context(request)
    if ctx is None:
        if auth_is_downgraded():
            # A specific message, so the operator does not chase a wrong password.
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
