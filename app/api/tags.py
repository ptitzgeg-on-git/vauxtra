from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from app.models import get_db
from app.auth import require_auth
from app.validators import is_valid_tag_color

router = APIRouter()


class TagIn(BaseModel):
    name:  str
    color: str = "blue"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Tag name is required")
        if len(v) > 32:
            raise ValueError("Name too long (max 32 characters)")
        return v

    @field_validator("color")
    @classmethod
    def color_valid(cls, v):
        if not is_valid_tag_color(v):
            return "blue"
        return v


@router.get("/api/tags")
def list_tags(request: Request):
    """Return all tags ordered by name."""
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/tags", status_code=201)
def create_tag(request: Request, body: TagIn):
    """Create a new tag. Returns 409 if name already exists."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM tags WHERE name=?", (body.name,)).fetchone()
        if existing:
            raise HTTPException(409, "A tag with this name already exists")
        cur = conn.execute("INSERT INTO tags (name, color) VALUES (?,?)", (body.name, body.color))
        conn.commit()
        tid = cur.lastrowid
        return {"id": tid, "name": body.name, "color": body.color}
    finally:
        conn.close()


@router.put("/api/tags/{tid}")
def update_tag(tid: int, request: Request, body: TagIn):
    """Update an existing tag by ID."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM tags WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Tag not found")
        conflict = conn.execute("SELECT id FROM tags WHERE name=? AND id!=?", (body.name, tid)).fetchone()
        if conflict:
            raise HTTPException(409, "A tag with this name already exists")
        conn.execute("UPDATE tags SET name=?, color=? WHERE id=?", (body.name, body.color, tid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/api/tags/{tid}")
def delete_tag(tid: int, request: Request):
    """Delete a tag by ID. Associated services are unlinked, not deleted."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM tags WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Tag not found")
        conn.execute("DELETE FROM tags WHERE id=?", (tid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
