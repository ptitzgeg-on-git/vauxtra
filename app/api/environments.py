import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.templates import templates_naming_label
from app.auth import require_auth
from app.models import add_log, get_db
from app.text import name_list, plural, verb

router = APIRouter()

_VALID_COLORS = {"blue","teal","green","red","orange","purple","cyan","yellow","pink","lime","indigo","azure"}

# Same 32-character limit as tag names: both are edited in the same panel field.
_MAX_NAME_LENGTH = 32


class EnvironmentIn(BaseModel):
    """Body of both environment write routes. Types only; messages come from
    `_read_name_and_color`, shared with the tag routes' wording.
    """

    name: str
    color: str = "blue"


def _read_name_and_color(body: dict) -> tuple[str, str]:
    """Validate a body the way `TagIn` does, the model above carrying only the types."""
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
def add_environment(request: Request, body: EnvironmentIn):
    """Create a new environment. Returns 409 if the name is already taken."""
    require_auth(request, scope="write")
    name, color = _read_name_and_color(body.model_dump())
    conn = get_db()
    try:
        # Looked up first so only a real duplicate gets a 409.
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
            # Race between lookup and INSERT. Other sqlite errors keep their 500.
            raise HTTPException(409, "An environment with this name already exists")
        return {"id": env_id, "name": name, "color": color}
    finally:
        conn.close()


@router.put("/api/environments/{eid}")
def update_environment(eid: int, request: Request, body: EnvironmentIn):
    """Update an environment by ID. 404 if it is gone, 409 if the name belongs to another one."""
    require_auth(request, scope="write")
    name, color = _read_name_and_color(body.model_dump())
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
        try:
            conn.execute("UPDATE environments SET name=?, color=? WHERE id=?", (name, color, eid))
            conn.commit()
        except sqlite3.IntegrityError:
            # Same race as in add_environment.
            raise HTTPException(409, "An environment with this name already exists")
        # The row is echoed back rather than tags' `{"ok": True}`: the MCP bridge returns this
        # reply to its own caller (vauxtra_mcp/tools/admin.py), while the panel only refetches.
        return {"id": eid, "name": name, "color": color}
    finally:
        conn.close()


def holders_of_environment(conn, eid: int) -> tuple[list[str], list[str]]:
    """Return (services, templates) using the environment, both sorted.

    Services are unlinked by cascade; template ids live in JSON text and are dropped on
    the next read. Read before deleting, since neither leaves a trace afterwards.
    """
    services = [
        # `.strip(".")` the way every other fqdn in the API is built: an apex route stores
        # an empty subdomain, and the naive join names it `.example.test`.
        f"{r['subdomain']}.{r['domain']}".strip(".")
        for r in conn.execute(
            "SELECT s.subdomain, s.domain FROM services s "
            "JOIN service_environments se ON se.service_id = s.id "
            "WHERE se.environment_id=? ORDER BY s.domain, s.subdomain",
            (eid,),
        )
    ]
    return services, templates_naming_label(conn, "environment_ids", eid)


def _log_environment_removal(conn, name: str, services: list[str], templates: list[str]) -> None:
    """Log an environment deletion with the services and templates that used it."""
    if not services and not templates:
        add_log("info", f"Environment deleted: {name}", conn)
        return
    # Separate sentences: services keep routing, templates change what they build.
    said = []
    if services:
        said.append(
            f"{plural(len(services), 'service')} "
            f"{verb(len(services), 'was', 'were')} set to it and "
            f"{verb(len(services), 'keeps', 'keep')} working without it "
            f"({name_list(services)})"
        )
    if templates:
        said.append(
            f"{plural(len(templates), 'service template')} named it and "
            f"{verb(len(templates), 'drops', 'drop')} it on the next read, so a service "
            f"built from one starts without the environment ({name_list(templates)})"
        )
    add_log("warn", f"Environment deleted: {name} -- {'. '.join(said)}", conn)


@router.delete("/api/environments/{eid}")
def delete_environment(eid: int, request: Request):
    """Delete an environment by ID. Associated services are unlinked, not deleted."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT name FROM environments WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Environment not found")
        # Read before the DELETE: afterwards nothing records who used it.
        services, templates = holders_of_environment(conn, eid)
        conn.execute("DELETE FROM environments WHERE id=?", (eid,))
        _log_environment_removal(conn, row["name"], services, templates)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
