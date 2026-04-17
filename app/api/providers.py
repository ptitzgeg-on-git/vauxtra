import json
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from app.models import get_db, get_db_ctx, add_log
from app.providers.factory import create_provider, PROVIDER_TYPES
from app.auth import require_auth
from app.validators import is_valid_url
from app.config import encrypt_secret

router = APIRouter()


_CLOUDFLARE_DEFAULT_URL = "https://api.cloudflare.com/client/v4"


def _normalize_provider_url(provider_type: str, url_value: str) -> str:
    val = (url_value or "").strip()
    if provider_type in {"cloudflare", "cloudflare_tunnel"} and not val:
        return _CLOUDFLARE_DEFAULT_URL
    if not is_valid_url(val):
        raise ValueError("Invalid URL (must start with http:// or https://)")
    return val.rstrip("/")


def _safe_json_load(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _provider_diagnostics(provider, provider_type: str, hostname_hint: str = "", write_probe: bool = False) -> dict:
    validation: dict[str, Any]
    if hasattr(provider, "validate_permissions"):
        try:
            validation = provider.validate_permissions(hostname_hint=hostname_hint, write_probe=write_probe)
        except TypeError:
            # Some providers may not support optional args in custom implementations.
            validation = provider.validate_permissions()
        except Exception as e:
            validation = {
                "ok": False,
                "checks": [{"name": "validation", "ok": False, "detail": str(e), "blocking": True}],
                "warnings": [],
            }
    else:
        ok = False
        try:
            ok = bool(provider.test_connection())
        except Exception:
            ok = False
        validation = {
            "ok": ok,
            "checks": [
                {
                    "name": "test_connection",
                    "ok": ok,
                    "detail": "Connection test passed" if ok else "Connection test failed",
                    "blocking": True,
                }
            ],
            "warnings": [],
        }

    health: dict[str, Any]
    if hasattr(provider, "health_status"):
        try:
            health = provider.health_status()
        except Exception as e:
            health = {
                "ok": False,
                "status": "unknown",
                "error": str(e),
            }
    else:
        health_ok = bool(validation.get("ok", False))
        health = {
            "ok": health_ok,
            "status": "healthy" if health_ok else "down",
        }

    return {
        "ok": bool(validation.get("ok", False)) and bool(health.get("ok", False)),
        "type": provider_type,
        "validation": validation,
        "health": health,
    }


class ProviderIn(BaseModel):
    name:     str
    type:     str
    url:      str = ""
    username: str = ""
    password: str = ""
    extra:    dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Name is required")
        return v.strip()

    @field_validator("url")
    @classmethod
    def url_valid(cls, v, info: ValidationInfo):
        ptype = str(info.data.get("type", "")).strip()
        return _normalize_provider_url(ptype, v)

    @field_validator("extra")
    @classmethod
    def extra_valid(cls, v):
        return v if isinstance(v, dict) else {}

    @field_validator("type")
    @classmethod
    def type_valid(cls, v):
        if v not in PROVIDER_TYPES:
            raise ValueError(f"Unknown type: {v}")
        return v


class ProviderUpdate(BaseModel):
    name:     str | None = None
    url:      str | None = None
    username: str | None = None
    password: str | None = None
    enabled:  int | None = None
    extra:    dict[str, Any] | None = None


class ProviderValidationOptions(BaseModel):
    hostname_hint: str = ""
    write_probe: bool = False


class ProviderDraftValidationIn(ProviderIn):
    hostname_hint: str = ""
    write_probe: bool = False


@router.get("/api/providers")
def list_providers(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, type, url, username, enabled, extra, created_at FROM providers ORDER BY id"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        item = dict(r)
        item["extra"] = _safe_json_load(item.get("extra"))
        out.append(item)
    return out


@router.get("/api/providers/health")
def all_providers_health(request: Request):
    """Batch health check for all enabled providers."""
    require_auth(request)
    with get_db_ctx() as conn:
        rows = conn.execute(
            "SELECT id, name, type, url, username, password, enabled, extra FROM providers WHERE enabled=1"
        ).fetchall()

    results = {}
    for r in rows:
        pid = r["id"]
        try:
            provider = create_provider(dict(r))
            provider.test_connection()
            results[str(pid)] = {"status": "healthy", "error": None}
        except Exception as e:
            results[str(pid)] = {"status": "unhealthy", "error": str(e)}
    return results


@router.get("/api/providers/types")
def list_types(request: Request):
    require_auth(request)
    return PROVIDER_TYPES


@router.post("/api/providers/validate-draft")
def validate_provider_draft(request: Request, body: ProviderDraftValidationIn):
    require_auth(request)

    if not PROVIDER_TYPES.get(body.type, {}).get("available"):
        raise HTTPException(400, f"Provider type '{body.type}' not yet available")

    provider_row = {
        "type": body.type,
        "url": _normalize_provider_url(body.type, body.url),
        "username": body.username,
        "password": encrypt_secret(body.password),
        "extra": json.dumps(body.extra or {}),
    }

    try:
        provider = create_provider(provider_row)
        diagnostics = _provider_diagnostics(
            provider,
            provider_type=body.type,
            hostname_hint=(body.hostname_hint or "").strip().lower(),
            write_probe=bool(body.write_probe),
        )
    except Exception as e:
        diagnostics = {
            "ok": False,
            "type": body.type,
            "validation": {
                "ok": False,
                "checks": [{"name": "provider_init", "ok": False, "detail": str(e), "blocking": True}],
                "warnings": [],
            },
            "health": {"ok": False, "status": "down", "error": str(e)},
        }

    return diagnostics


@router.get("/api/providers/tunnels/health")
def list_tunnel_health(request: Request):
    require_auth(request)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM providers WHERE type='cloudflare_tunnel' AND enabled=1 ORDER BY id"
    ).fetchall()
    conn.close()

    items = []
    healthy = 0
    for row in rows:
        item = dict(row)
        item_extra = _safe_json_load(item.get("extra"))
        item["extra"] = item_extra
        try:
            provider = create_provider(row)
            if hasattr(provider, "health_status"):
                health = provider.health_status()
            else:
                ok = bool(provider.test_connection())
                health = {"ok": ok, "status": "healthy" if ok else "down"}
        except Exception as e:
            health = {"ok": False, "status": "down", "error": str(e)}

        if health.get("ok"):
            healthy += 1

        items.append(
            {
                "id": item["id"],
                "name": item["name"],
                "type": item["type"],
                "enabled": bool(item["enabled"]),
                "tunnel_id": str(item_extra.get("tunnel_id", "")).strip(),
                "health": health,
            }
        )

    return {
        "total": len(items),
        "healthy": healthy,
        "down": len(items) - healthy,
        "items": items,
    }


@router.post("/api/providers", status_code=201)
def add_provider(request: Request, body: ProviderIn):
    require_auth(request, scope="write")
    if not PROVIDER_TYPES.get(body.type, {}).get("available"):
        raise HTTPException(400, f"Provider type '{body.type}' not yet available")
    conn = get_db()
    cur  = conn.execute(
        "INSERT INTO providers (name, type, url, username, password, extra) VALUES (?,?,?,?,?,?)",
        (
            body.name,
            body.type,
            _normalize_provider_url(body.type, body.url),
            body.username,
            encrypt_secret(body.password),
            json.dumps(body.extra or {}),
        ),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    add_log("info", f"Provider added: {body.name} ({body.type})")
    return {"id": pid, "name": body.name, "type": body.type}


@router.put("/api/providers/{pid}")
def update_provider(pid: int, request: Request, body: ProviderUpdate):
    require_auth(request, scope="write")
    conn = get_db()
    row  = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Provider not found")

    name     = (body.name or row["name"]).strip()
    url_src  = body.url if body.url is not None else row["url"]
    try:
        url_val = _normalize_provider_url(row["type"], url_src)
    except ValueError as e:
        conn.close()
        raise HTTPException(400, str(e)) from e
    username = body.username if body.username is not None else row["username"]
    enabled  = body.enabled if body.enabled is not None else row["enabled"]
    if body.extra is not None:
        extra = body.extra
    else:
        extra = _safe_json_load(row["extra"])

    if body.password:
        conn.execute(
            "UPDATE providers SET name=?,url=?,username=?,password=?,enabled=?,extra=? WHERE id=?",
            (name, url_val, username, encrypt_secret(body.password), enabled, json.dumps(extra), pid),
        )
    else:
        conn.execute(
            "UPDATE providers SET name=?,url=?,username=?,enabled=?,extra=? WHERE id=?",
            (name, url_val, username, enabled, json.dumps(extra), pid),
        )
    conn.commit()
    conn.close()
    add_log("info", f"Provider updated: {name}")
    return {"ok": True}


@router.delete("/api/providers/{pid}")
def delete_provider(pid: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    row  = conn.execute("SELECT name FROM providers WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Provider not found")
    conn.execute("DELETE FROM providers WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    add_log("info", f"Provider deleted: {row['name']}")
    return {"ok": True}


@router.post("/api/providers/{pid}/validate")
def validate_provider(pid: int, request: Request, body: ProviderValidationOptions | None = None):
    require_auth(request)
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Provider not found")

    opts = body or ProviderValidationOptions()
    try:
        provider = create_provider(row)
        diagnostics = _provider_diagnostics(
            provider,
            provider_type=row["type"],
            hostname_hint=(opts.hostname_hint or "").strip().lower(),
            write_probe=bool(opts.write_probe),
        )
    except Exception as e:
        diagnostics = {
            "ok": False,
            "type": row["type"],
            "validation": {
                "ok": False,
                "checks": [{"name": "provider_init", "ok": False, "detail": str(e), "blocking": True}],
                "warnings": [],
            },
            "health": {"ok": False, "status": "down", "error": str(e)},
        }

    diagnostics["provider"] = row["name"]
    return diagnostics


@router.get("/api/providers/{pid}/health")
def provider_health(pid: int, request: Request):
    require_auth(request)
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Provider not found")

    try:
        provider = create_provider(row)
        if hasattr(provider, "health_status"):
            health = provider.health_status()
        else:
            ok = bool(provider.test_connection())
            health = {"ok": ok, "status": "healthy" if ok else "down"}
    except Exception as e:
        health = {"ok": False, "status": "down", "error": str(e)}

    return {
        "provider": row["name"],
        "type": row["type"],
        "health": health,
        "ok": bool(health.get("ok")),
    }


@router.post("/api/providers/{pid}/test")
def test_provider(pid: int, request: Request):
    require_auth(request)
    conn = get_db()
    row  = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Provider not found")
    try:
        provider = create_provider(row)
        diagnostics = _provider_diagnostics(provider, provider_type=row["type"])
        ok = bool(diagnostics.get("ok"))
        return {
            "ok": ok,
            "provider": row["name"],
            "validation": diagnostics.get("validation", {}),
            "health": diagnostics.get("health", {}),
        }
    except Exception as e:
        return {
            "ok": False,
            "provider": row["name"],
            "validation": {
                "ok": False,
                "checks": [{"name": "provider_init", "ok": False, "detail": str(e), "blocking": True}],
                "warnings": [],
            },
            "health": {"ok": False, "status": "down", "error": str(e)},
        }
