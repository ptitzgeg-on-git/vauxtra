"""Authentication endpoints — login, logout, session check, password setup."""

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import (
    bump_session_epoch,
    check_password,
    current_session_epoch,
    get_session,
    has_password_configured,
    hash_password,
    is_authenticated,
    is_setup_incomplete,
    mark_password_configured,
    password_is_env_managed,
    password_was_configured_once,
    require_auth,
    require_auth_or_setup,
)
from app.limiter import limiter
from app.models import get_db
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
    return {
        "authenticated": is_authenticated(request),
        "auth_required": has_password_configured(),
        # The same rule the backend applies to `require_auth_or_setup`: the interface showed
        # the wizard on an install whose password had been set and later removed, and the
        # backend then refused the wizard's calls.
        "setup_required": is_setup_incomplete(),
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


def _password_matches(candidate: str) -> bool:
    """Check a typed password the way it was stored.

    Setup, change-password and `APP_PASSWORD` all strip the surrounding spaces; the checks
    did not, so a password typed with a trailing space at setup was refused at the next
    login. The stripped form is tried first, the raw one only for a hash written before
    stripping existed, so nobody is locked out either way.
    """
    candidate = candidate or ""
    stripped = candidate.strip()
    if stripped and check_password(stripped):
        return True
    return candidate != stripped and check_password(candidate)


@router.post("/api/auth/login")
@limiter.limit("5/minute;20/hour")
async def auth_login(request: Request, body: LoginBody):
    """Verify password and create an authenticated session.

    Rate limited to 5 attempts per minute, 20 per hour.
    Failed attempts include a small delay to slow down brute-force attacks.
    """
    if not await run_in_threadpool(has_password_configured):
        raise HTTPException(400, "Authentication is disabled — no password configured")

    # The hash check stays off the event loop; the delay that follows a miss does not
    # occupy a thread. As a sync route, `time.sleep(0.5)` held one of the threadpool's
    # forty workers per failed attempt, and a burst of wrong passwords from many addresses
    # left every other route of the panel waiting for a free thread.
    if not await run_in_threadpool(_password_matches, body.password):
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Invalid password")

    session = get_session(request)
    session["authenticated"] = True
    session["epoch"] = current_session_epoch()
    session["issued_at"] = int(time.time())
    return {"ok": True}


@router.post("/api/auth/logout")
def auth_logout(request: Request):
    """End the session, and every copy of its cookie with it.

    Clearing the cookie in this browser left a copy taken elsewhere fully valid: the cookie
    is self-contained and signed. Bumping the epoch revokes it, at the cost of also ending
    the operator's sessions on other devices.
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
    """Set the admin password during initial setup (only when no password exists)."""
    # `password_was_configured_once` as well: an instance whose hash has disappeared (or a
    # database that cannot be read, which that check reports as "yes") must not let an
    # anonymous caller choose the new admin password. A deliberately open install still
    # reaches this route, from the wizard or from Settings > Security.
    if has_password_configured() or password_was_configured_once():
        raise HTTPException(400, "Password is already configured")

    password = body.password.strip()
    ok, why = validate_password_strength(password)
    if not ok:
        raise HTTPException(400, why)

    password_hash = hash_password(password)
    conn = get_db()
    try:
        # Re-checked under a write lock: two concurrent calls must not both win.
        conn.execute("BEGIN IMMEDIATE")
        taken = conn.execute(
            "SELECT 1 FROM settings WHERE key IN ('app_password_hash', 'auth_mode') AND value != ''"
        ).fetchone()
        if taken:
            conn.rollback()
            raise HTTPException(400, "Password is already configured")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (password_hash,),
        )
        # Close the setup bypass here, not at the final click of the wizard: an abandoned
        # wizard must never leave the API anonymous.
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_completed', '1')"
        )
        mark_password_configured(conn)
        # Any session minted while the instance was open stops counting.
        bump_session_epoch(conn)
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

    if not _password_matches(body.current_password):
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
        conn.commit()
    finally:
        conn.close()

    # ... except this one. Logging the operator out of the browser they are holding, as a
    # side effect of following the advice, is how people learn not to follow it.
    session = get_session(request)
    if session.get("authenticated") is True:
        session["epoch"] = current_session_epoch()

    return {"ok": True}
