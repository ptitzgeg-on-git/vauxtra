"""Authentication endpoints — login, logout, session check, password setup."""

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth import (
    AUTH_MODE_KEY,
    AUTH_MODE_PASSWORD,
    bump_session_epoch,
    check_password,
    current_session_epoch,
    get_session,
    has_password_configured,
    hash_password,
    is_authenticated,
    mark_password_configured,
    password_is_env_managed,
    password_was_configured_once,
    require_auth,
    require_auth_or_setup,
)
from app.limiter import limiter
from app.models import add_log, get_db
from app.security import validate_password_strength

router = APIRouter()


class LoginBody(BaseModel):
    password: str


class SetPasswordBody(BaseModel):
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.get("/api/auth/me")
def auth_me(request: Request):
    """Return current authentication status and setup state."""
    conn = get_db()
    try:
        # Check if setup wizard was completed
        setup_row = conn.execute("SELECT value FROM settings WHERE key='setup_completed'").fetchone()
        setup_completed = setup_row and setup_row["value"] == "1"

        # Setup is considered pending only on an empty install.
        provider_count = conn.execute("SELECT COUNT(*) as c FROM providers").fetchone()["c"]
    finally:
        conn.close()

    return {
        "authenticated": is_authenticated(request),
        "auth_required": has_password_configured(),
        "setup_required": (not setup_completed) and provider_count == 0,
        # "open" means every request on this instance carries the admin scope, with no
        # credential at all. The wizard lets you choose that, and until now nothing in the
        # interface ever mentioned it again.
        "auth_mode": "password" if has_password_configured() else "open",
        # Which of the two stores actually decides a login. The interface offered a
        # change-password form in both cases; in one of them the form could not work.
        "password_source": "environment" if password_is_env_managed() else "database",
    }


@router.post("/api/auth/setup-complete")
def mark_setup_complete(request: Request):
    """Mark the setup wizard as completed (stored server-side)."""
    # Open while the wizard is running, `admin` afterwards: `setup_completed` is the flag
    # that decides whether the setup routes stay open at all.
    require_auth_or_setup(request, scope="admin")
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_completed', '1')")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/auth/login")
@limiter.limit("5/minute;20/hour")
def auth_login(request: Request, body: LoginBody):
    """Verify password and create an authenticated session.
    
    Rate limited to 5 attempts per minute, 20 per hour.
    Failed attempts include a small delay to slow down brute-force attacks.
    """
    if not has_password_configured():
        raise HTTPException(400, "Authentication is disabled — no password configured")

    if not check_password(body.password):
        # Add delay on failed attempt to slow brute-force
        time.sleep(0.5)
        # Somebody guessing at the password was the one thing this instance never recorded.
        # No address in the line: `request.client.host` is the reverse proxy for every
        # caller unless `FORWARDED_ALLOW_IPS` is set (app/limiter.py says why), so an
        # address here would be a false lead in the investigation it is written for.
        #
        # Only the attempts that reach this branch are written. The 429s the limiter raises
        # above them are not, and that is what bounds this to five lines a minute: a line
        # per refused request would let an unauthenticated caller fill the table at its own
        # rate.
        add_log("warn", "Sign-in refused: wrong password")
        raise HTTPException(401, "Invalid password")

    session = get_session(request)
    session["authenticated"] = True
    session["epoch"] = current_session_epoch()
    session["issued_at"] = int(time.time())
    add_log("info", "Signed in")
    return {"ok": True}


@router.post("/api/auth/logout")
def auth_logout(request: Request):
    """End the session, and every copy of its cookie with it.

    Clearing the cookie in this browser left a copy taken elsewhere fully valid: the cookie
    is self-contained and signed. Bumping the epoch revokes it, at the cost of also ending
    the operator's sessions on other devices. Only a live session may do it, so an anonymous
    POST cannot sign everybody out.
    """
    session = get_session(request)
    if session.get("authenticated") is True and is_authenticated(request):
        conn = get_db()
        try:
            bump_session_epoch(conn)
            conn.commit()
        finally:
            conn.close()
    session.clear()
    return {"ok": True}


@router.post("/api/auth/setup-password")
@limiter.limit("3/minute")
def setup_password(request: Request, body: SetPasswordBody):
    """Set the admin password during initial setup (only when no password ever existed)."""
    if has_password_configured():
        raise HTTPException(400, "Password is already configured")
    # "Has a password now" is not the whole question. An instance whose hash row went
    # missing (an old database restored, a hand edit) reads as passwordless, and this
    # anonymous route used to let the first caller pick the new admin password.
    # `password_was_configured_once` remembers, and answers yes on a database it cannot read.
    if password_was_configured_once():
        raise HTTPException(
            409,
            "This instance had an admin password and its hash is no longer in the database. "
            "Restore the database, or set APP_PASSWORD to a 'pbkdf2:'-prefixed hash.",
        )

    password = body.password.strip()
    ok, why = validate_password_strength(password)
    if not ok:
        raise HTTPException(400, why)

    password_hash = hash_password(password)
    conn = get_db()
    try:
        # The check above and this write are two moments: two requests can both pass the
        # first. The condition is therefore asked again inside the write, as one statement,
        # which SQLite runs atomically, and the loser writes nothing.
        cur = conn.execute(
            """INSERT OR REPLACE INTO settings (key, value)
               SELECT 'app_password_hash', ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM settings
                    WHERE (key = 'app_password_hash' AND value <> '')
                       OR (key = ? AND value = ?)
               )""",
            (password_hash, AUTH_MODE_KEY, AUTH_MODE_PASSWORD),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise HTTPException(400, "Password is already configured")
        # Close the setup bypass here, not at the final click of the wizard: an abandoned
        # wizard must never leave the API anonymous.
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_completed', '1')"
        )
        mark_password_configured(conn)
        # A cookie signed before the password existed must not survive it: whatever was
        # opened while the instance was anonymous ends here, this session excepted below.
        bump_session_epoch(conn)
        add_log("info", "Admin password set from the setup wizard", conn)
        conn.commit()
    finally:
        conn.close()

    # Auto-login after setting password
    session = get_session(request)
    session["authenticated"] = True
    session["epoch"] = current_session_epoch()
    session["issued_at"] = int(time.time())
    return {"ok": True}


@router.post("/api/auth/change-password")
@limiter.limit("3/minute")
def change_password(request: Request, body: ChangePasswordBody):
    """Change the admin password (requires current password verification)."""
    # `admin`, not merely "authenticated": an API key minted for a monitoring dashboard
    # has no business attempting the admin password, even though the current password is
    # still required below -- each attempt also burns the shared 3/minute rate limit.
    require_auth(request, scope="admin")

    # Before anything is verified or written: with `APP_PASSWORD` set, `check_password`
    # never reads the stored hash, so this route used to write one nobody would read and
    # then bump the session epoch -- logging the operator out of everything and refusing
    # the password they had just chosen.
    if password_is_env_managed():
        raise HTTPException(
            409,
            "This instance takes its admin password from the APP_PASSWORD environment "
            "variable, so a password changed here would never be read. Change the value of "
            "APP_PASSWORD and restart, or remove the variable to let this screen own the "
            "password.",
        )

    if not check_password(body.current_password):
        raise HTTPException(401, "Current password is incorrect")

    new_password = body.new_password.strip()
    ok, why = validate_password_strength(new_password)
    if not ok:
        raise HTTPException(400, why)

    password_hash = hash_password(new_password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (password_hash,),
        )
        mark_password_configured(conn)
        # Every other session dies with the old password. This is the whole point of
        # changing it after a suspected compromise, and until now it did not happen: the
        # thief's cookie kept working for the remaining days of its seven.
        bump_session_epoch(conn)
        add_log("info", "Admin password changed, and every other session was signed out", conn)
        conn.commit()
    finally:
        conn.close()

    # ... except this one. Logging the operator out of the browser they are holding, as a
    # side effect of following the advice, is how people learn not to follow it.
    session = get_session(request)
    if session.get("authenticated") is True:
        session["epoch"] = current_session_epoch()

    return {"ok": True}
