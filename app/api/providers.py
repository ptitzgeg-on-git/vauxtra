import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.api.sync import withdraw_service_routes
from app.auth import is_authorized, require_auth, require_auth_or_setup
from app.config import encrypt_secret
from app.models import add_log, get_db, get_db_ctx
from app.providers.factory import PROVIDER_TYPES, create_provider
from app.text import plural, verb
from app.validators import is_valid_url

router = APIRouter()


_CLOUDFLARE_DEFAULT_URL = "https://api.cloudflare.com/client/v4"

# Hosted providers: a blank URL means this well-known address, not a validation error.
_DEFAULT_PROVIDER_URLS = {
    "cloudflare": _CLOUDFLARE_DEFAULT_URL,
    "cloudflare_tunnel": _CLOUDFLARE_DEFAULT_URL,
    "desec": "https://desec.io/api/v1",
}


def _normalize_provider_url(provider_type: str, url_value: str) -> str:
    val = (url_value or "").strip()
    if not val and provider_type in _DEFAULT_PROVIDER_URLS:
        return _DEFAULT_PROVIDER_URLS[provider_type]
    if not is_valid_url(val):
        raise ValueError("Invalid URL (must start with http:// or https://)")
    return val.rstrip("/")


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _endpoint(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(url or "")
        scheme = parts.scheme.lower()
        return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None


def _same_endpoint(a: str, b: str) -> bool:
    """Whether two URLs reach the same scheme, host and port."""
    first = _endpoint(a)
    return first is not None and first == _endpoint(b)


def _safe_json_load(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
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
                "checks": [
                    {
                        "name": "validation",
                        "ok": False,
                        "detail": str(e),
                        "detail_code": "provider_error",
                        "detail_params": {"error": str(e)},
                        "blocking": True,
                    }
                ],
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
                    "detail_code": "connection_ok" if ok else "connection_failed",
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
    """Body of PUT /api/providers/{pid}; absent fields keep the stored value.

    A blank name is refused, as on create. `enabled` is a bool because every reader
    filters on enabled=1.
    """

    name:     str | None = None
    url:      str | None = None
    username: str | None = None
    password: str | None = None
    enabled:  bool | None = None
    extra:    dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        # Only when it was sent. `None` is the caller saying nothing about the name, which is
        # the whole point of this model; a blank string is the caller saying to erase it.
        if v is not None and not v.strip():
            raise ValueError("Name is required")
        return v.strip() if v is not None else v


class ProviderValidationOptions(BaseModel):
    hostname_hint: str = ""
    write_probe: bool = False


class ProviderDraftValidationIn(ProviderIn):
    # Nothing is written, so a name is optional.
    name: str = "(draft)"
    hostname_hint: str = ""
    write_probe: bool = False


@router.get("/api/providers")
def list_providers(request: Request):
    require_auth_or_setup(request)
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


def _health_of(row) -> dict[str, Any]:
    try:
        provider = create_provider(dict(row))
        # test_connection returns False rather than raising.
        ok = bool(provider.test_connection())
        return {"status": "healthy" if ok else "unhealthy", "error": None}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


# How many integrations `GET /providers/health` tests at once.
_HEALTH_WORKERS = 8


@router.get("/api/providers/health")
def all_providers_health(request: Request):
    """Batch health check for all enabled providers, tested side by side."""
    require_auth(request)
    with get_db_ctx() as conn:
        rows = conn.execute(
            "SELECT id, name, type, url, username, password, enabled, extra FROM providers WHERE enabled=1"
        ).fetchall()
    if not rows:
        return {}

    # Tested in parallel so one hanging integration does not delay the others.
    with ThreadPoolExecutor(max_workers=min(_HEALTH_WORKERS, len(rows))) as pool:
        verdicts = list(pool.map(_health_of, rows))
    return {str(r["id"]): verdict for r, verdict in zip(rows, verdicts, strict=True)}


@router.get("/api/providers/types")
def list_types(request: Request):
    require_auth_or_setup(request)
    return PROVIDER_TYPES


@router.post("/api/providers/validate-draft")
def validate_provider_draft(request: Request, body: ProviderDraftValidationIn):
    # `write` once setup is done: the body carries an arbitrary URL that the server then
    # connects to, credentials included.
    require_auth_or_setup(request, scope="write")

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
                "checks": [
                    {
                        "name": "provider_init",
                        "ok": False,
                        "detail": str(e),
                        "detail_code": "provider_error",
                        "detail_params": {"error": str(e)},
                        "blocking": True,
                    }
                ],
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
    require_auth_or_setup(request, scope="write")
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
    # A configured instance is no longer an empty install: close the setup bypass even if
    # the operator never reaches the last screen of the wizard.
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_completed', '1')")
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

    # Strip the stored name too: older rows may hold only spaces.
    name     = (body.name if body.name is not None else row["name"]).strip()
    url_src  = body.url if body.url is not None else row["url"]
    try:
        url_val = _normalize_provider_url(row["type"], url_src)
    except ValueError as e:
        conn.close()
        raise HTTPException(400, str(e)) from e
    # The stored secret goes wherever the URL points, so moving the URL needs admin scope.
    if (
        row["password"]
        and not body.password
        and not _same_endpoint(row["url"], url_val)
        and not is_authorized(request, "admin")
    ):
        conn.close()
        raise HTTPException(
            403,
            "Moving this integration to another host, port or scheme needs its password or "
            "token again, or an admin key. The stored one is only sent to the address it was "
            "entered for.",
        )
    username = body.username if body.username is not None else row["username"]
    # bool() also normalizes a legacy out-of-range value on the next save.
    enabled  = int(body.enabled) if body.enabled is not None else int(bool(row["enabled"]))
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


def _provider_dependents(conn, pid: int) -> list[dict[str, Any]]:
    """Services that lose a provider link when `pid` is deleted (SET NULL / CASCADE)."""
    rows = conn.execute(
        """
        SELECT s.id, s.subdomain, s.domain, s.enabled,
               s.proxy_provider_id  AS proxy_id,
               s.dns_provider_id    AS dns_id,
               s.tunnel_provider_id AS tunnel_id,
               (SELECT GROUP_CONCAT(t.role) FROM service_push_targets t
                 WHERE t.service_id = s.id AND t.provider_id = ?) AS extra_roles,
               (SELECT GROUP_CONCAT(t.provider_id) FROM service_push_targets t
                 WHERE t.service_id = s.id) AS all_extra_ids
          FROM services s
         WHERE s.proxy_provider_id = ? OR s.dns_provider_id = ? OR s.tunnel_provider_id = ?
            OR EXISTS (SELECT 1 FROM service_push_targets t
                        WHERE t.service_id = s.id AND t.provider_id = ?)
         ORDER BY s.domain, s.subdomain
        """,
        (pid, pid, pid, pid, pid),
    ).fetchall()

    dependents: list[dict[str, Any]] = []
    for row in rows:
        roles = []
        if row["proxy_id"] == pid:
            roles.append("proxy")
        if row["dns_id"] == pid:
            roles.append("dns")
        if row["tunnel_id"] == pid:
            roles.append("tunnel")
        for extra in str(row["extra_roles"] or "").split(","):
            extra = extra.strip()
            if extra and extra not in roles:
                roles.append(f"extra {extra}")

        # A multi-sync service stays published by its other targets.
        attached = {row["proxy_id"], row["dns_id"], row["tunnel_id"]}
        attached.update(
            int(x) for x in str(row["all_extra_ids"] or "").split(",") if x.strip().isdigit()
        )
        attached.discard(None)
        attached.discard(pid)

        dependents.append(
            {
                "id": row["id"],
                "fqdn": f"{row['subdomain']}.{row['domain']}".strip(".").lower(),
                "roles": roles,
                "still_published": bool(attached),
            }
        )
    return dependents


def _provider_template_dependents(conn, pid: int) -> list[dict[str, Any]]:
    """Service templates whose proxy/DNS/tunnel provider is `pid` (blanked on delete)."""
    rows = conn.execute(
        """
        SELECT id, name, proxy_provider_id, dns_provider_id, tunnel_provider_id
          FROM service_templates
         WHERE proxy_provider_id = ? OR dns_provider_id = ? OR tunnel_provider_id = ?
         ORDER BY name
        """,
        (pid, pid, pid),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        roles = [
            role
            for role, col in (("proxy", "proxy_provider_id"), ("dns", "dns_provider_id"),
                              ("tunnel", "tunnel_provider_id"))
            if row[col] == pid
        ]
        out.append({"id": row["id"], "name": row["name"], "roles": roles})
    return out


def _provider_webhook_dependents(conn, pid: int) -> list[dict[str, Any]]:
    """Webhooks scoped to provider `pid`. scope_ref_id has no FK, so nothing cascades."""
    rows = conn.execute(
        """
        SELECT id, name, enabled
          FROM webhooks
         WHERE scope_type = 'provider' AND scope_ref_id = ?
         ORDER BY name
        """,
        (pid,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"])} for r in rows]


def _describe_provider_removal(
    name: str,
    dependents: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    webhooks: list[dict[str, Any]],
) -> str:
    """Build the 409 message describing, per dependent, what deleting the provider does."""
    kept = [d for d in dependents if d.get("still_published")]
    orphaned = [d for d in dependents if not d.get("still_published")]

    parts: list[str] = []
    if dependents:
        parts.append(
            f'{plural(len(dependents), "service")} still '
            f'{verb(len(dependents), "uses", "use")} "{name}".'
        )
    if orphaned:
        parts.append(
            f"{len(orphaned)} of them {verb(len(orphaned), 'has', 'have')} no other target: "
            f"{verb(len(orphaned), 'it keeps', 'they keep')} the public hostname and "
            f"{verb(len(orphaned), 'stops', 'stop')} being published anywhere until another "
            "provider is chosen."
        )
    if kept:
        parts.append(
            f"{len(kept)} {verb(len(kept), 'goes', 'go')} on being published by "
            f"{verb(len(kept), 'its', 'their')} other targets."
        )
    if dependents:
        parts.append(
            f'Whatever "{name}" already serves for them stays live on it after the deletion, '
            "and Vauxtra stops being able to see it. Re-send with ?force=true&withdraw=true to "
            "take those records off it first, or ?force=true alone to leave them in place."
        )
    if templates:
        # Separate sentence: a template publishes nothing.
        names = ", ".join(f'"{d["name"]}"' for d in templates[:3])
        if len(templates) > 3:
            names += f", and {len(templates) - 3} more"
        parts.append(
            f'{plural(len(templates), "service template")} '
            f'{verb(len(templates), "names", "name")} "{name}" and '
            f'{verb(len(templates), "loses", "lose")} that choice when it goes ({names}). '
            f"Nothing is published from a template, so there is nothing to take off "
            f'"{name}"; the next service built from '
            f"{verb(len(templates), 'it', 'them')} simply starts with no provider."
        )
    if webhooks:
        # Separate sentence: a webhook keeps pointing at the deleted id.
        hook_names = ", ".join(f'"{d["name"]}"' for d in webhooks[:3])
        if len(webhooks) > 3:
            hook_names += f", and {len(webhooks) - 3} more"
        parts.append(
            f'{plural(len(webhooks), "notification webhook")} '
            f'{verb(len(webhooks), "is", "are")} scoped to "{name}" and '
            f'{verb(len(webhooks), "loses", "lose")} that target when it goes ({hook_names}). '
            "Nothing is published from a webhook, so there is nothing to take off "
            f'"{name}"; the rule simply stops matching anything.'
        )
        armed = [d for d in webhooks if d.get("enabled")]
        if armed:
            # The one sentence an operator needs and would not guess: a dead webhook is not
            # switched off by any of this. It keeps its green badge in Settings.
            parts.append(
                f'{len(armed)} {verb(len(armed), "is", "are")} still switched on and '
                f'{verb(len(armed), "goes", "go")} on looking armed in Settings until the '
                "scope is changed."
            )
    if not dependents:
        parts.append("Re-send with ?force=true to delete it anyway.")
    return " ".join(parts)


@router.delete("/api/providers/{pid}")
def delete_provider(pid: int, request: Request, force: bool = False, withdraw: bool = False):
    require_auth_or_setup(request, scope="write")
    conn = get_db()
    row  = conn.execute("SELECT name FROM providers WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Provider not found")

    dependents = _provider_dependents(conn, pid)
    templates = _provider_template_dependents(conn, pid)
    hooks = _provider_webhook_dependents(conn, pid)
    if (dependents or templates or hooks) and not force:
        # Refuse with 409 listing dependent services, templates and scoped webhooks;
        # the UI resends with ?force=true after confirmation.
        conn.close()
        raise HTTPException(
            409,
            {
                "message": _describe_provider_removal(row["name"], dependents, templates, hooks),
                "services": dependents,
                "templates": templates,
                "webhooks": hooks,
            },
        )

    # withdraw=true: remove only this provider's routes; other targets are untouched.
    withdrawal_errors: list[str] = []
    if dependents and withdraw:
        for dep in dependents:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (dep["id"],)).fetchone()
            if not svc:
                continue
            for message in withdraw_service_routes(conn, svc, dep["id"], only_provider_ids={pid}):
                withdrawal_errors.append(f"{dep['fqdn']}: {message}")
        if withdrawal_errors:
            add_log(
                "error",
                f"Provider {row['name']}: {plural(len(withdrawal_errors), 'record')} could not be "
                "withdrawn before deletion and are still live on it",
                conn,
            )

    conn.execute("DELETE FROM providers WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    if dependents:
        names = ", ".join(d["fqdn"] for d in dependents[:5])
        if len(dependents) > 5:
            names += f", and {len(dependents) - 5} more"
        # Logged either way: without withdraw these hostnames stay published there.
        what = "withdrawn from it and unlinked" if withdraw else "unlinked, still served by it"
        add_log(
            "warn",
            f"Provider deleted: {row['name']} -- {plural(len(dependents), 'service')} {what} ({names})",
        )
    elif not templates and not hooks:
        add_log("info", f"Provider deleted: {row['name']}")
    if templates:
        # Own log line, so a template emptied by the deletion can be traced.
        tpl_names = ", ".join(d["name"] for d in templates[:5])
        if len(templates) > 5:
            tpl_names += f", and {len(templates) - 5} more"
        add_log(
            "warn",
            f"Provider deleted: {row['name']} -- {plural(len(templates), 'service template')} "
            f"lost the provider {verb(len(templates), 'it named', 'they named')} ({tpl_names})",
        )
    if hooks:
        # The webhook row is unchanged, so the log is the only trace.
        hook_names = ", ".join(d["name"] for d in hooks[:5])
        if len(hooks) > 5:
            hook_names += f", and {len(hooks) - 5} more"
        add_log(
            "warn",
            f"Provider deleted: {row['name']} -- {plural(len(hooks), 'notification webhook')} "
            f"{verb(len(hooks), 'was', 'were')} scoped to it and now "
            f"{verb(len(hooks), 'matches', 'match')} nothing ({hook_names})",
        )
    return {
        "ok": not withdrawal_errors,
        "unlinked_services": [d["id"] for d in dependents],
        "unlinked_templates": [d["id"] for d in templates],
        "orphaned_webhooks": [d["id"] for d in hooks],
        "withdrawn": bool(dependents and withdraw),
        "errors": withdrawal_errors,
    }


@router.post("/api/providers/{pid}/validate")
def validate_provider(pid: int, request: Request, body: ProviderValidationOptions | None = None):
    # `write`: the options carry `write_probe`, which really does write to the provider.
    require_auth(request, scope="write")
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
                "checks": [
                    {
                        "name": "provider_init",
                        "ok": False,
                        "detail": str(e),
                        "detail_code": "provider_error",
                        "detail_params": {"error": str(e)},
                        "blocking": True,
                    }
                ],
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
    # `write`, to match /api/docker/endpoints/{id}/test: it makes the server open an
    # outbound connection on demand, which a read-only key should not be able to drive.
    require_auth(request, scope="write")
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
                "checks": [
                    {
                        "name": "provider_init",
                        "ok": False,
                        "detail": str(e),
                        "detail_code": "provider_error",
                        "detail_params": {"error": str(e)},
                        "blocking": True,
                    }
                ],
                "warnings": [],
            },
            "health": {"ok": False, "status": "down", "error": str(e)},
        }


def _check_provider_capability(provider_type: str, capability: str) -> tuple[bool, str]:
    """Return (has_capability, error_message) for `capability` ('dns', 'proxy') on a provider type."""
    provider_meta = PROVIDER_TYPES.get(provider_type, {})
    capabilities = provider_meta.get("capabilities", {})

    if capability not in capabilities or not capabilities[capability]:
        provider_label = provider_meta.get("label", provider_type)
        return False, f"Provider '{provider_label}' does not support {capability} management"

    return True, ""


def _provider_client(row):
    """Build the client for a stored integration.

    Done outside the provider-call try block so a local fault (unknown type, unreadable
    secret) is a 500, not a 502 blaming the provider.
    """
    try:
        return create_provider(row)
    except Exception as e:
        raise HTTPException(500, f"Could not build a client for '{row['name']}': {str(e)}")


# DNS record and proxy host routes: provider errors answer 502 (upstream failure), not 500.

class DNSRecordIn(BaseModel):
    domain: str
    answer: str  # IP address or target


@router.get("/api/providers/{pid}/dns-records")
def list_dns_records(pid: int, request: Request):
    """List all DNS records managed by a provider."""
    require_auth(request)
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Provider not found")

    # Check capability
    has_dns, error_msg = _check_provider_capability(row["type"], "dns")
    if not has_dns:
        raise HTTPException(400, error_msg)

    provider = _provider_client(row)
    try:
        records = provider.list_rewrites()
        return {"provider": row["name"], "records": records or []}
    except Exception as e:
        raise HTTPException(502, f"Failed to list DNS records: {str(e)}")


@router.post("/api/providers/{pid}/dns-records", status_code=201)
def create_dns_record(pid: int, request: Request, body: DNSRecordIn):
    """Create a new DNS record in a provider."""
    require_auth(request, scope="write")
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Provider not found")

    # Check capability
    has_dns, error_msg = _check_provider_capability(row["type"], "dns")
    if not has_dns:
        raise HTTPException(400, error_msg)

    provider = _provider_client(row)
    try:
        success = provider.add_rewrite(body.domain, body.answer)
        if not success:
            raise HTTPException(400, "Failed to create DNS record (provider rejected)")
        add_log("info", f"DNS record created: {body.domain} → {body.answer} ({row['name']})")
        return {"ok": True, "domain": body.domain, "answer": body.answer}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to create DNS record: {str(e)}")


@router.delete("/api/providers/{pid}/dns-records/{domain}")
def delete_dns_record(pid: int, domain: str, request: Request, answer: str | None = None):
    """Delete a DNS record from a provider."""
    require_auth(request, scope="write")
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Provider not found")

    # Check capability
    has_dns, error_msg = _check_provider_capability(row["type"], "dns")
    if not has_dns:
        raise HTTPException(400, error_msg)

    provider = _provider_client(row)
    try:
        # If answer not provided, find it from list
        if not answer:
            # Case-insensitive, like the sync layer.
            wanted = domain.strip().lower()
            records = provider.list_rewrites() or []
            for r in records:
                if str(r.get("domain") or "").strip().lower() == wanted:
                    answer = r.get("answer")
                    break

        if not answer:
            raise HTTPException(404, "DNS record not found")

        success = provider.delete_rewrite(domain, answer)
        if not success:
            raise HTTPException(400, "Failed to delete DNS record (provider rejected)")
        add_log("info", f"DNS record deleted: {domain} ({row['name']})")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to delete DNS record: {str(e)}")


class ProxyHostIn(BaseModel):
    domain_names: list[str] = Field(min_length=1)
    forward_host: str
    forward_port: int = 80
    scheme: str = "http"


@router.get("/api/providers/{pid}/proxy-hosts")
def list_proxy_hosts(pid: int, request: Request):
    """List all proxy hosts managed by a provider."""
    require_auth(request)
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Provider not found")

    # Check capability
    has_proxy, error_msg = _check_provider_capability(row["type"], "proxy")
    if not has_proxy:
        raise HTTPException(400, error_msg)

    provider = _provider_client(row)
    try:
        hosts = provider.list_hosts()
        return {"provider": row["name"], "hosts": hosts or []}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to list proxy hosts: {str(e)}")


@router.post("/api/providers/{pid}/proxy-hosts", status_code=201)
def create_proxy_host(pid: int, request: Request, body: ProxyHostIn):
    """Create a new proxy host in a provider."""
    require_auth(request, scope="write")
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Provider not found")

    # Check capability
    has_proxy, error_msg = _check_provider_capability(row["type"], "proxy")
    if not has_proxy:
        raise HTTPException(400, error_msg)

    provider = _provider_client(row)
    try:
        primary_domain = body.domain_names[0].strip()
        if not primary_domain:
            raise HTTPException(400, "domain_names must contain at least one non-empty domain")

        result = provider.create_host(
            domain=primary_domain,
            ip=body.forward_host,
            port=body.forward_port,
            scheme=body.scheme,
        )
        if not result:
            raise HTTPException(400, "Failed to create proxy host (provider rejected)")
        add_log("info", f"Proxy host created: {', '.join(body.domain_names)} → {body.forward_host}:{body.forward_port}")
        return {"ok": True, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to create proxy host: {str(e)}")


@router.delete("/api/providers/{pid}/proxy-hosts/{host_id}")
def delete_proxy_host(pid: int, host_id: str, request: Request):
    """Delete a proxy host from a provider."""
    require_auth(request, scope="write")
    conn = get_db()
    row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Provider not found")

    # Check capability
    has_proxy, error_msg = _check_provider_capability(row["type"], "proxy")
    if not has_proxy:
        raise HTTPException(400, error_msg)

    provider = _provider_client(row)
    try:
        # Passed through as is: NPM uses numbers, Cloudflare Tunnel and Traefik use names.
        success = provider.delete_host(host_id)
        if not success:
            raise HTTPException(400, "Failed to delete proxy host (provider rejected)")
        add_log("info", f"Proxy host deleted: ID {host_id}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to delete proxy host: {str(e)}")
