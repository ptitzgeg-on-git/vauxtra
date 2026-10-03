"""Service templates: pre-configured defaults for new services."""

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import require_auth
from app.models import get_db
from app.validators import DOMAIN_REASONS, domain_problem, is_valid_hostname, normalize_domain

router = APIRouter()


class TemplateIn(BaseModel):
    """Body of both template write routes. Unknown keys are a 422, as on ServiceIn."""

    model_config = ConfigDict(extra="forbid")

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
    environment_ids:    list[int] = Field(default_factory=list)
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

    # Mirrors ServiceIn, except a field may be empty (filled in at apply time). A value
    # that is present is validated now rather than on every apply.

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


#: The two halves of the label control, each a TEXT column holding a JSON array, paired with
#: the table its ids have to be found in. One name per half, used by everything below.
_LABEL_COLUMNS = (
    ("tag_ids", "tag_ids_json", "tags", "tag"),
    ("environment_ids", "environment_ids_json", "environments", "environment"),
)


def _row_to_dict(row) -> dict:
    """Read a template row; both label columns always come back as lists of ints.

    A malformed or wrongly shaped JSON column reads as no labels instead of raising.
    """
    d = dict(row)
    for key, column, _table, _label in _LABEL_COLUMNS:
        try:
            parsed = json.loads(d.get(column) or "[]")
        except (ValueError, TypeError):
            parsed = []
        d[key] = (
            [int(i) for i in parsed if isinstance(i, int) and not isinstance(i, bool)]
            if isinstance(parsed, list)
            else []
        )
        d.pop(column, None)
    return d


def _drop_dead_labels(conn, templates: list[dict]) -> None:
    """Remove, in place, tag/environment ids that no longer exist.

    Label columns are JSON text with no foreign key, so deletions do not cascade.
    One query per label kind for the whole list.
    """
    for key, _column, table, _label in _LABEL_COLUMNS:
        wanted = {i for t in templates for i in t[key]}
        if not wanted:
            continue
        marks = ",".join("?" * len(wanted))
        live = {
            r["id"]
            for r in conn.execute(f"SELECT id FROM {table} WHERE id IN ({marks})", tuple(wanted))
        }
        for tpl in templates:
            tpl[key] = [i for i in tpl[key] if i in live]


def templates_naming_label(conn, key: str, label_id: int) -> list[str]:
    """Names of templates whose `key` ("tag_ids" or "environment_ids") contains `label_id`.

    A scan, since the column is JSON text. Called before a label is deleted, because the
    dead id is dropped on the next read and leaves no trace.
    """
    column = next(c for k, c, _t, _l in _LABEL_COLUMNS if k == key)
    names = []
    for r in conn.execute(f"SELECT name, {column} FROM service_templates ORDER BY name"):
        try:
            ids = json.loads(r[column] or "[]")
        except (TypeError, ValueError):
            # Unparseable column: treat as not containing the label.
            continue
        if isinstance(ids, list) and label_id in ids:
            names.append(r["name"])
    return names


def _unknown_references(conn, body: TemplateIn) -> list[str]:
    """Name every id in the template that points at nothing.

    Provider ids are foreign keys (would be a 500); label ids are JSON text (would be
    stored and refused later at apply time). Same check as for services.
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

    for key, _column, table, label in _LABEL_COLUMNS:
        _check(table, getattr(body, key), label)
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
        _drop_dead_labels(conn, out)
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
        _drop_dead_labels(conn, [tpl])
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
                public_target_mode, domain, dns_ip, tag_ids_json,
                environment_ids_json, icon_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                body.name, body.description, body.forward_scheme,
                body.target_port, int(body.websocket), body.expose_mode,
                body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id,
                body.public_target_mode, body.domain, body.dns_ip,
                json.dumps(body.tag_ids), json.dumps(body.environment_ids), body.icon_url,
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
               tag_ids_json=?, environment_ids_json=?, icon_url=?
               WHERE id=?""",
            (
                body.name, body.description, body.forward_scheme,
                body.target_port, int(body.websocket), body.expose_mode,
                body.proxy_provider_id, body.dns_provider_id, body.tunnel_provider_id,
                body.public_target_mode, body.domain, body.dns_ip,
                json.dumps(body.tag_ids), json.dumps(body.environment_ids),
                body.icon_url, tid,
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
    """Return a pre-filled service payload from the template.

    The client merges it with the subdomain, target_ip and target_port it supplies.
    """
    require_auth(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM service_templates WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        tpl = _row_to_dict(row)
        _drop_dead_labels(conn, [tpl])

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
            "environment_ids":    tpl["environment_ids"],
            "icon_url":           tpl["icon_url"],
            "_template_id":       tpl["id"],
            "_template_name":     tpl["name"],
        }
    finally:
        conn.close()
