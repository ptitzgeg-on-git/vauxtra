"""Authentication endpoints — login, logout, session check, password setup."""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.auth import get_session, is_authenticated, has_password_configured, check_password, hash_password, require_auth
from app.limiter import limiter
from app.models import get_db

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
        
        # Check if any providers exist (alternative indicator of completed setup)
        provider_count = conn.execute("SELECT COUNT(*) as c FROM providers").fetchone()["c"]
    finally:
        conn.close()
    
    return {
        "authenticated": is_authenticated(request),
        "auth_required": has_password_configured(),
        "setup_required": not setup_completed and provider_count == 0,
    }


@router.post("/api/auth/setup-complete")
def mark_setup_complete(request: Request):
    """Mark the setup wizard as completed (stored server-side)."""
    require_auth(request)
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
    import time
    
    if not has_password_configured():
        raise HTTPException(400, "Authentication is disabled — no password configured")

    if not check_password(body.password):
        # Add delay on failed attempt to slow brute-force
        time.sleep(0.5)
        raise HTTPException(401, "Invalid password")

    session = get_session(request)
    session["authenticated"] = True
    return {"ok": True}


@router.post("/api/auth/logout")
def auth_logout(request: Request):
    """Clear the authenticated session."""
    session = get_session(request)
    session.clear()
    return {"ok": True}


@router.post("/api/auth/setup-password")
@limiter.limit("3/minute")
def setup_password(request: Request, body: SetPasswordBody):
    """Set the admin password during initial setup (only when no password exists)."""
    if has_password_configured():
        raise HTTPException(400, "Password is already configured")

    password = body.password.strip()
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    password_hash = hash_password(password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (password_hash,),
        )
        conn.commit()
    finally:
        conn.close()

    # Auto-login after setting password
    session = get_session(request)
    session["authenticated"] = True
    return {"ok": True}


@router.post("/api/auth/change-password")
@limiter.limit("3/minute")
def change_password(request: Request, body: ChangePasswordBody):
    """Change the admin password (requires current password verification)."""
    require_auth(request)
    
    if not check_password(body.current_password):
        raise HTTPException(401, "Current password is incorrect")

    new_password = body.new_password.strip()
    if len(new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")

    password_hash = hash_password(new_password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
            (password_hash,),
        )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True}
