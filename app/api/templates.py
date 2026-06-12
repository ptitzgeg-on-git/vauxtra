"""Service templates — pre-configured defaults that accelerate service creation."""

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.auth import require_auth
from app.models import get_db

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


def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["tag_ids"] = json.loads(d.get("tag_ids_json") or "[]")
    except Exception:
        d["tag_ids"] = []
    d.pop("tag_ids_json", None)
    return d


@router.get("/api/templates")
def list_templates(request: Request):
    """Return all service templates ordered by name."""
    require_auth(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM service_templates ORDER BY name").fetchall()
        return [_row_to_dict(r) for r in rows]
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
        return _row_to_dict(row)
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
