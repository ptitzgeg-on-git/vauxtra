import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.api.templates import templates_naming_label
from app.auth import require_auth
from app.models import add_log, get_db
from app.text import name_list, plural, verb
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
        try:
            cur = conn.execute(
                "INSERT INTO tags (name, color) VALUES (?,?)", (body.name, body.color)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # The lookup above and this INSERT are two statements: another writer can store
            # the name in between, and then the UNIQUE index is the only thing that still
            # knows. Same refusal, same sentence. Narrow on purpose -- an `OperationalError`
            # for a locked base or a full disk is ours, and keeps its 500.
            raise HTTPException(409, "A tag with this name already exists")
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
        try:
            conn.execute("UPDATE tags SET name=?, color=? WHERE id=?", (body.name, body.color, tid))
            conn.commit()
        except sqlite3.IntegrityError:
            # The window `create_tag` documents, on the rename rather than the creation. The
            # conflict lookup above and this UPDATE are the same two statements, and the same
            # writer can take the name in between -- with the same UNIQUE index left as the
            # only thing that knows. It answered 500 here while the creation, three lines of
            # code away, answered 409 for the collision the operator had actually caused.
            raise HTTPException(409, "A tag with this name already exists")
        return {"ok": True}
    finally:
        conn.close()


def holders_of_tag(conn, tid: int) -> tuple[list[str], list[str]]:
    """The services carrying the tag and the templates naming it, both already sorted.

    The two hold it in tables that behave nothing alike. `service_tags` declares
    `ON DELETE CASCADE` (`app/models.py`), so a deleted tag unlinks its services and the
    rows themselves are untouched. `service_templates.tag_ids_json` is TEXT holding a JSON
    array, which no constraint reaches: the id survives the delete and is dropped on the
    next read by `_drop_dead_labels` (`app/api/templates.py`). Same disappearance, arrived
    at two different ways, and neither leaves anything to read afterwards.
    """
    services = [
        # `.strip(".")` the way every other fqdn in the API is built: an apex route stores
        # an empty subdomain, and the naive join names it `.example.test`.
        f"{r['subdomain']}.{r['domain']}".strip(".")
        for r in conn.execute(
            "SELECT s.subdomain, s.domain FROM services s "
            "JOIN service_tags st ON st.service_id = s.id "
            "WHERE st.tag_id=? ORDER BY s.domain, s.subdomain",
            (tid,),
        )
    ]
    templates = templates_naming_label(conn, "tag_ids", tid)
    return services, templates


def _log_tag_removal(conn, name: str, services: list[str], templates: list[str]) -> None:
    """The only trace a tag deletion leaves, so it carries what was holding the tag.

    There was none at all before this. Every other destructive route in the API writes to the
    journal -- services, providers, domains, Docker endpoints, API keys -- and the two label
    routes wrote nothing, which is the wrong way round: a tag is the one thing here whose
    deletion changes rows the operator was not looking at. The services keep working and lose
    a label they were filtered by; the templates come back one tag shorter and the next
    service built from one starts without it. Neither says anything at the time, and after
    the delete the tag id is not in the database to ask about.
    """
    if not services and not templates:
        add_log("info", f"Tag deleted: {name}", conn)
        return
    # A sentence each, rather than one count over both. They are not the same event: the
    # services lose a label and go on routing, the templates change what they will build
    # next. Joined into one list the rarer half is also the one "and 3 more" hides, and it
    # is the half nothing else in the product reports.
    said = []
    if services:
        said.append(
            f"{plural(len(services), 'service')} carried it and "
            f"{verb(len(services), 'keeps', 'keep')} working without it "
            f"({name_list(services)})"
        )
    if templates:
        said.append(
            f"{plural(len(templates), 'service template')} named it and "
            f"{verb(len(templates), 'drops', 'drop')} it on the next read, so a service "
            f"built from one starts without the tag ({name_list(templates)})"
        )
    add_log("warn", f"Tag deleted: {name} -- {'. '.join(said)}", conn)


@router.delete("/api/tags/{tid}")
def delete_tag(tid: int, request: Request):
    """Delete a tag by ID. Associated services are unlinked, not deleted."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT name FROM tags WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Tag not found")
        # Read before the DELETE: the cascade takes `service_tags` with it, so after the
        # commit there is nothing left that knows which services carried this tag.
        services, templates = holders_of_tag(conn, tid)
        conn.execute("DELETE FROM tags WHERE id=?", (tid,))
        _log_tag_removal(conn, row["name"], services, templates)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
