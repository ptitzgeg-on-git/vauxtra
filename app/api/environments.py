from fastapi import APIRouter, Request, HTTPException
from app.models import get_db, get_db_ctx
from app.auth import require_auth

router = APIRouter()

_VALID_COLORS = {"blue","teal","green","red","orange","purple","cyan","yellow","pink","lime","indigo","azure"}


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
    name  = body.get("name", "").strip()
    color = body.get("color", "blue")
    if not name:
        raise HTTPException(400, "Name is required")
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
    except Exception:
        raise HTTPException(409, "Environment already exists")
    finally:
        conn.close()


@router.put("/api/environments/{eid}")
def update_environment(eid: int, request: Request, body: dict):
    require_auth(request, scope="write")
    name  = body.get("name", "").strip()
    color = body.get("color", "blue")
    if not name:
        raise HTTPException(400, "Name is required")
    if color not in _VALID_COLORS:
        color = "blue"
    conn = get_db()
    try:
        conn.execute("UPDATE environments SET name=?, color=? WHERE id=?", (name, color, eid))
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
