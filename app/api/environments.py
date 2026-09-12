import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.auth import require_auth
from app.models import get_db

router = APIRouter()

_VALID_COLORS = {"blue","teal","green","red","orange","purple","cyan","yellow","pink","lime","indigo","azure"}

# `TagIn` stops a tag name at 32 characters (app/api/tags.py). Environments are typed into the
# same field of the same panel, so they answer to the same ceiling, and with the 422 FastAPI
# already answers for a tag body it refuses. One rule, one reply, whichever list is edited.
_MAX_NAME_LENGTH = 32


def _read_name_and_color(body: dict) -> tuple[str, str]:
    """Validate a body the way `TagIn` does, a plain dict having no model to do it."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if len(name) > _MAX_NAME_LENGTH:
        raise HTTPException(422, f"Name too long (max {_MAX_NAME_LENGTH} characters)")
    color = body.get("color", "blue")
    if color not in _VALID_COLORS:
        color = "blue"
    return name, color


@router.get("/api/environments")
def list_environments(request: Request):
    """Return all environments ordered by name."""
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM environments ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/environments", status_code=201)
def add_environment(request: Request, body: dict):
    """Create a new environment. Returns 409 if the name is already taken."""
    require_auth(request, scope="write")
    name, color = _read_name_and_color(body)
    conn = get_db()
    try:
        # Asked before writing, so that a duplicate is the only thing answered as a duplicate.
        # The INSERT used to report it through the UNIQUE index, under a bare `except Exception`
        # that told a locked base and a full disk they were duplicates too.
        existing = conn.execute("SELECT id FROM environments WHERE name=?", (name,)).fetchone()
        if existing:
            raise HTTPException(409, "An environment with this name already exists")
        try:
            cur = conn.execute(
                "INSERT INTO environments (name, color) VALUES (?,?)", (name, color)
            )
            env_id = cur.lastrowid
            conn.commit()
        except sqlite3.IntegrityError:
            # The lookup above and this INSERT are two statements: another writer can store
            # the name in between, and then the UNIQUE index is the only thing that still
            # knows. Same refusal, same sentence. Narrow on purpose -- an `OperationalError`
            # for a locked base or a full disk is ours, and keeps its 500.
            raise HTTPException(409, "An environment with this name already exists")
        return {"id": env_id, "name": name, "color": color}
    finally:
        conn.close()


@router.put("/api/environments/{eid}")
def update_environment(eid: int, request: Request, body: dict):
    """Update an environment by ID. 404 if it is gone, 409 if the name belongs to another one."""
    require_auth(request, scope="write")
    name, color = _read_name_and_color(body)
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM environments WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Environment not found")
        conflict = conn.execute(
            "SELECT id FROM environments WHERE name=? AND id!=?", (name, eid)
        ).fetchone()
        if conflict:
            raise HTTPException(409, "An environment with this name already exists")
        conn.execute("UPDATE environments SET name=?, color=? WHERE id=?", (name, color, eid))
        conn.commit()
        # The row is echoed back rather than tags' `{"ok": True}`: the MCP bridge returns this
        # reply to its own caller (vauxtra_mcp/tools/admin.py), while the panel only refetches.
        return {"id": eid, "name": name, "color": color}
    finally:
        conn.close()


@router.delete("/api/environments/{eid}")
def delete_environment(eid: int, request: Request):
    """Delete an environment by ID. Associated services are unlinked, not deleted."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM environments WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Environment not found")
        conn.execute("DELETE FROM environments WHERE id=?", (eid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
