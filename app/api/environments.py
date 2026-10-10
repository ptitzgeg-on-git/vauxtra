import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.auth import require_auth
from app.models import get_db

router = APIRouter()

_VALID_COLORS = {"blue","teal","green","red","orange","purple","cyan","yellow","pink","lime","indigo","azure"}


def _clean_name(body: dict) -> str:
    """Validate an environment name the way tag names are validated."""
    raw = body.get("name", "")
    name = raw.strip() if isinstance(raw, str) else ""
    if not name:
        raise HTTPException(400, "Name is required")
    if len(name) > 32:
        raise HTTPException(400, "Name too long (max 32 characters)")
    # Service rows carry their environments as "name:color:id" joined by commas; a name
    # holding either separator was split apart there and vanished from the service.
    if ":" in name or "," in name:
        raise HTTPException(400, "Environment name cannot contain ':' or ','")
    return name


@router.get("/api/environments")
def list_environments(request: Request):
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM environments ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/environments", status_code=201)
def add_environment(request: Request, body: dict):
    require_auth(request, scope="write")
    name  = _clean_name(body)
    color = body.get("color", "blue")
    if color not in _VALID_COLORS:
        color = "blue"
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO environments (name, color) VALUES (?,?)", (name, color)
        )
        env_id = cur.lastrowid
        conn.commit()
        return {"id": env_id, "name": name, "color": color}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Environment already exists")
    finally:
        conn.close()


@router.put("/api/environments/{eid}")
def update_environment(eid: int, request: Request, body: dict):
    require_auth(request, scope="write")
    name  = _clean_name(body)
    color = body.get("color", "blue")
    if color not in _VALID_COLORS:
        color = "blue"
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM environments WHERE id=?", (eid,)).fetchone():
            raise HTTPException(404, "Environment not found")
        try:
            conn.execute("UPDATE environments SET name=?, color=? WHERE id=?", (name, color, eid))
        except sqlite3.IntegrityError:
            # A name already taken raised straight through as a 500.
            raise HTTPException(409, "Environment already exists") from None
        conn.commit()
        return {"id": eid, "name": name, "color": color}
    finally:
        conn.close()


@router.delete("/api/environments/{eid}")
def delete_environment(eid: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    try:
        conn.execute("DELETE FROM environments WHERE id=?", (eid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
