"""Service templates — pre-configured defaults that accelerate service creation."""

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.auth import require_auth
from app.models import get_db
from app.validators import DOMAIN_REASONS, domain_problem, is_valid_hostname, normalize_domain

router = APIRouter()


class TemplateIn(BaseModel):
    name:               str
    description:        str = ""
    forward_scheme:     str = "http"
    target_port:        int | None = None
    websocket:          bool = False
    expose_mode:        str = "proxy_dns"
    proxy_provider_id:  int | None = None
    dns_provider_id:    int | None = None
    tunnel_provider_id: int | None = None
    public_target_mode: str = "manual"
    domain:             str = ""
    dns_ip:             str = ""
    tag_ids:            list[int] = Field(default_factory=list)
    icon_url:           str = ""

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Template name is required")
        if len(v) > 64:
            raise ValueError("Name too long (max 64 characters)")
        return v

    @field_validator("forward_scheme")
    @classmethod
    def valid_scheme(cls, v):
        if v not in {"http", "https"}:
            raise ValueError("forward_scheme must be 'http' or 'https'")
        return v

    @field_validator("expose_mode")
    @classmethod
    def valid_expose_mode(cls, v):
        if v not in {"proxy_dns", "tunnel"}:
            raise ValueError("expose_mode must be 'proxy_dns' or 'tunnel'")
        return v

    @field_validator("target_port")
    @classmethod
    def valid_port(cls, v):
        if v is not None and not (1 <= v <= 65535):
            raise ValueError("target_port must be between 1 and 65535")
        return v

    # The three below mirror `ServiceIn`, with one difference that is the whole point of a
    # template: a field may be left empty, because the operator fills it in at apply time.
    # A value that is *present* is a value `POST /api/services` will be handed verbatim, so
    # refusing it here refuses it once, at the moment it was typed, rather than every time
    # the template is used.

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, v):
        val = normalize_domain(v)
        if not val:
            return ""
        problem = domain_problem(val)
        if problem:
            raise ValueError(f"Invalid domain: {DOMAIN_REASONS[problem]}")
        return val

    @field_validator("dns_ip")
    @classmethod
    def valid_dns_ip(cls, v):
        val = (v or "").strip().lower()
        if val and not is_valid_hostname(val):
            raise ValueError("Invalid DNS public target")
        return val

    @field_validator("public_target_mode")
    @classmethod
    def valid_public_target_mode(cls, v):
        val = (v or "manual").strip().lower()
        if val not in ("manual", "auto"):
            raise ValueError("public_target_mode must be 'manual' or 'auto'")
        return val


def _row_to_dict(row) -> dict:
    """Read a template row, with `tag_ids` guaranteed to be a list of ints.

    `tag_ids_json` is a TEXT column, so what comes back is whatever is in it. Valid JSON is
    not the question -- `{"a": 1}` parses and used to reach the panel as `tag_ids`, which is
    the wrong shape for every caller. An unreadable or wrongly-shaped column reads as no
    tags rather than raising, because one bad row must not take `GET /api/templates` down
    with it.
    """
    d = dict(row)
    try:
        parsed = json.loads(d.get("tag_ids_json") or "[]")
    except (ValueError, TypeError):
        parsed = []
    d["tag_ids"] = (
        [int(i) for i in parsed if isinstance(i, int) and not isinstance(i, bool)]
        if isinstance(parsed, list)
        else []
    )
    d.pop("tag_ids_json", None)
    return d


def _drop_dead_tags(conn, templates: list[dict]) -> None:
    """Remove, in place, every `tag_ids` entry naming a tag that no longer exists.

    The two kinds of reference a template holds rot differently on their own. A deleted
    provider leaves the column NULL, because `service_templates` declares `ON DELETE SET
    NULL` on all three. A deleted tag leaves nothing behind at all: `tag_ids_json` is TEXT,
    so no cascade reaches it and the template goes on naming a tag nobody can see.

    That id is unusable in both directions. Sent onward it earns a refusal from
    `POST /api/services` for a tag the operator never chose; shown in the editor it is a
    chip the tag list cannot draw, so it cannot be clicked off -- and the save that carries
    it back is now refused by `_unknown_references`, leaving a template that can be opened
    and never stored. Dropping it here, on every read, makes a tag rot the way a provider
    already does: quietly, before anyone is asked to do something about it.

    One query for however many templates were read, because `GET /api/templates` returns
    them all and a per-row lookup would scale with the list.
    """
    wanted = {i for t in templates for i in t["tag_ids"]}
    if not wanted:
        return
    marks = ",".join("?" * len(wanted))
    live = {
        r["id"] for r in conn.execute(f"SELECT id FROM tags WHERE id IN ({marks})", tuple(wanted))
    }
    for tpl in templates:
        tpl["tag_ids"] = [i for i in tpl["tag_ids"] if i in live]


def _unknown_references(conn, body: TemplateIn) -> list[str]:
    """Name every id in the template that points at nothing.

    The same check `app/api/services.py` runs before it writes a service, for the same
    reason and against the same tables. Without it the two kinds of id in a template failed
    in two different ways, neither of them useful:

      - a provider id reached SQLite, where `ON DELETE SET NULL` on `service_templates`
        means the column is a real foreign key and an unknown value raises
        `IntegrityError`. The operator got a 500 on a form they had just filled in.
      - a tag id is stored in `tag_ids_json`, which no constraint reaches, so it was saved
        happily and `GET /api/templates/{id}/apply` handed it back weeks later. The refusal
        arrived from `POST /api/services`, naming a tag id nobody had typed.
    """
    unknown: list[str] = []

    def _check(table: str, ids, label: str) -> None:
        wanted = sorted({int(i) for i in ids if i})
        if not wanted:
            return
        marks = ",".join("?" * len(wanted))
        found = {
            r["id"]
            for r in conn.execute(f"SELECT id FROM {table} WHERE id IN ({marks})", tuple(wanted))
        }
        unknown.extend(f"{label} {i}" for i in wanted if i not in found)

    _check("tags", body.tag_ids, "tag")
    _check(
        "providers",
        [body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id],
        "provider",
    )
    return unknown


@router.get("/api/templates")
def list_templates(request: Request):
    """Return all service templates ordered by name."""
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM service_templates ORDER BY name").fetchall()
        out = [_row_to_dict(r) for r in rows]
        _drop_dead_tags(conn, out)
        return out
    finally:
        conn.close()


@router.get("/api/templates/{tid}")
def get_template(tid: int, request: Request):
    """Return a single template by ID."""
    require_auth(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM service_templates WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        tpl = _row_to_dict(row)
        _drop_dead_tags(conn, [tpl])
        return tpl
    finally:
        conn.close()


@router.post("/api/templates", status_code=201)
def create_template(request: Request, body: TemplateIn):
    """Create a new service template."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM service_templates WHERE name=?", (body.name,)
        ).fetchone()
        if existing:
            raise HTTPException(409, "A template with this name already exists")

        unknown = _unknown_references(conn, body)
        if unknown:
            raise HTTPException(400, f"Nothing was created -- unknown {', '.join(unknown)}")

        cur = conn.execute(
            """INSERT INTO service_templates
               (name, description, forward_scheme, target_port, websocket, expose_mode,
                proxy_provider_id, dns_provider_id, tunnel_provider_id,
                public_target_mode, domain, dns_ip, tag_ids_json, icon_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                body.name, body.description, body.forward_scheme,
                body.target_port, int(body.websocket), body.expose_mode,
                body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id,
                body.public_target_mode, body.domain, body.dns_ip,
                json.dumps(body.tag_ids), body.icon_url,
            ),
        )
        conn.commit()
        tid = cur.lastrowid
        row = conn.execute("SELECT * FROM service_templates WHERE id=?", (tid,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.put("/api/templates/{tid}")
def update_template(tid: int, request: Request, body: TemplateIn):
    """Update an existing template."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM service_templates WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        conflict = conn.execute(
            "SELECT id FROM service_templates WHERE name=? AND id!=?", (body.name, tid)
        ).fetchone()
        if conflict:
            raise HTTPException(409, "A template with this name already exists")

        unknown = _unknown_references(conn, body)
        if unknown:
            raise HTTPException(400, f"Nothing was changed -- unknown {', '.join(unknown)}")

        conn.execute(
            """UPDATE service_templates SET
               name=?, description=?, forward_scheme=?, target_port=?, websocket=?,
               expose_mode=?, proxy_provider_id=?, dns_provider_id=?,
               tunnel_provider_id=?, public_target_mode=?, domain=?, dns_ip=?,
               tag_ids_json=?, icon_url=?
               WHERE id=?""",
            (
                body.name, body.description, body.forward_scheme,
                body.target_port, int(body.websocket), body.expose_mode,
                body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id,
                body.public_target_mode, body.domain, body.dns_ip,
                json.dumps(body.tag_ids), body.icon_url, tid,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM service_templates WHERE id=?", (tid,)).fetchone()
        return _row_to_dict(updated)
    finally:
        conn.close()


@router.delete("/api/templates/{tid}")
def delete_template(tid: int, request: Request):
    """Delete a template by ID."""
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM service_templates WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        conn.execute("DELETE FROM service_templates WHERE id=?", (tid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/api/templates/{tid}/apply")
def apply_template(tid: int, request: Request):
    """
    Return a pre-filled service payload based on the template.
    The client merges this with user-supplied subdomain / target_ip / target_port.
    """
    require_auth(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM service_templates WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        tpl = _row_to_dict(row)
        _drop_dead_tags(conn, [tpl])

        return {
            "forward_scheme":     tpl["forward_scheme"],
            "target_port":        tpl["target_port"],
            "websocket":          bool(tpl["websocket"]),
            "expose_mode":        tpl["expose_mode"],
            "proxy_provider_id":  tpl["proxy_provider_id"],
            "dns_provider_id":    tpl["dns_provider_id"],
            "tunnel_provider_id": tpl["tunnel_provider_id"],
            "public_target_mode": tpl["public_target_mode"],
            "domain":             tpl["domain"],
            "dns_ip":             tpl["dns_ip"],
            "tag_ids":            tpl["tag_ids"],
            "icon_url":           tpl["icon_url"],
            "_template_id":       tpl["id"],
            "_template_name":     tpl["name"],
        }
    finally:
        conn.close()
