import re
from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from app.auth import require_auth
from app.models import add_log, get_db, get_db_ctx
from app.services.docker_analyzer import analyze_container

router = APIRouter()


DOCKER_HOST_RE = re.compile(r"^(unix|tcp|ssh)://.+")


class DockerEndpointIn(BaseModel):
    name: str
    docker_host: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def val_name(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("Endpoint name is required")
        return value

    @field_validator("docker_host")
    @classmethod
    def val_host(cls, v: str) -> str:
        value = (v or "").strip()
        if not DOCKER_HOST_RE.match(value):
            raise ValueError("docker_host must start with unix://, tcp:// or ssh://")
        return value


def _sanitize_subdomain(raw: str) -> str:
    value = (raw or "service").strip().lower()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:63] or "service"


def _docker_client(docker_host: str | None = None):
    try:
        import docker
    except ImportError:
        raise HTTPException(500, "Package 'docker' not installed — rebuild the Docker image")

    try:
        client = docker.DockerClient(base_url=docker_host) if docker_host else docker.from_env()
        client.ping()
        return client
    except Exception as e:
        raise HTTPException(503, f"Docker daemon unavailable: {e}")


def _resolve_endpoint(conn, endpoint_id: int | None):
    if endpoint_id is not None:
        row = conn.execute(
            "SELECT id, name, docker_host, enabled, is_default, created_at FROM docker_endpoints WHERE id=?",
            (endpoint_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Docker endpoint not found")
        if not row["enabled"]:
            raise HTTPException(400, "Selected Docker endpoint is disabled")
        return dict(row)

    row = conn.execute(
        """
        SELECT id, name, docker_host, enabled, is_default, created_at
        FROM docker_endpoints
        WHERE enabled=1
        ORDER BY is_default DESC, id ASC
        LIMIT 1
        """
    ).fetchone()

    if row:
        return dict(row)

    return {
        "id": None,
        "name": "Environment Docker",
        "docker_host": None,
        "enabled": 1,
        "is_default": 1,
        "created_at": None,
    }


@router.get("/api/docker/endpoints")
def list_docker_endpoints(request: Request):
    """Return all configured Docker endpoints for container discovery."""
    require_auth(request)
    with get_db_ctx() as conn:
        rows = conn.execute(
            """
            SELECT id, name, docker_host, enabled, is_default, created_at
            FROM docker_endpoints
            ORDER BY is_default DESC, id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/docker/endpoints", status_code=201)
def add_docker_endpoint(request: Request, body: DockerEndpointIn):
    require_auth(request, scope="write")
    conn = get_db()
    try:
        existing = conn.execute("SELECT COUNT(*) FROM docker_endpoints").fetchone()[0]
        is_default = 1 if existing == 0 else 0

        try:
            cur = conn.execute(
                "INSERT INTO docker_endpoints (name, docker_host, enabled, is_default) VALUES (?,?,?,?)",
                (body.name, body.docker_host, int(body.enabled), is_default),
            )
            conn.commit()
            endpoint_id = cur.lastrowid
        except Exception:
            raise HTTPException(409, "Docker endpoint host already exists")
    finally:
        conn.close()

    add_log("info", f"Docker endpoint added: {body.name} ({body.docker_host})")
    return {
        "id": endpoint_id,
        "name": body.name,
        "docker_host": body.docker_host,
        "enabled": body.enabled,
        "is_default": bool(is_default),
    }


@router.post("/api/docker/endpoints/{endpoint_id}/default")
def set_default_docker_endpoint(endpoint_id: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute("SELECT id, name FROM docker_endpoints WHERE id=?", (endpoint_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Docker endpoint not found")

        conn.execute("UPDATE docker_endpoints SET is_default=0")
        conn.execute("UPDATE docker_endpoints SET is_default=1 WHERE id=?", (endpoint_id,))
        conn.commit()
    finally:
        conn.close()
    add_log("info", f"Docker endpoint set as default: {row['name']}")
    return {"ok": True}


@router.post("/api/docker/endpoints/{endpoint_id}/test")
def test_docker_endpoint(endpoint_id: int, request: Request):
    require_auth(request, scope="write")
    with get_db_ctx() as conn:
        endpoint = _resolve_endpoint(conn, endpoint_id)

    client = _docker_client(endpoint.get("docker_host") or None)
    containers_count = len(client.containers.list())
    return {
        "ok": True,
        "endpoint_id": endpoint.get("id"),
        "endpoint_name": endpoint.get("name"),
        "docker_host": endpoint.get("docker_host"),
        "containers": containers_count,
    }


@router.delete("/api/docker/endpoints/{endpoint_id}")
def delete_docker_endpoint(endpoint_id: int, request: Request):
    require_auth(request, scope="write")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, is_default FROM docker_endpoints WHERE id=?",
            (endpoint_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Docker endpoint not found")

        total = conn.execute("SELECT COUNT(*) FROM docker_endpoints").fetchone()[0]
        if total <= 1:
            raise HTTPException(400, "At least one Docker endpoint must remain")

        conn.execute("DELETE FROM docker_endpoints WHERE id=?", (endpoint_id,))

        if row["is_default"]:
            next_row = conn.execute("SELECT id FROM docker_endpoints ORDER BY id LIMIT 1").fetchone()
            if next_row:
                conn.execute("UPDATE docker_endpoints SET is_default=1 WHERE id=?", (next_row["id"],))

        conn.commit()
    finally:
        conn.close()
    add_log("info", f"Docker endpoint deleted: {row['name']}")
    return {"ok": True}


def _extract_container_port(attrs: dict) -> int | None:
    network = attrs.get("NetworkSettings", {}) or {}
    ports = network.get("Ports") or {}
    for key in ports.keys():
        try:
            return int(str(key).split("/")[0])
        except Exception:
            continue

    exposed = (attrs.get("Config", {}) or {}).get("ExposedPorts") or {}
    for key in exposed.keys():
        try:
            return int(str(key).split("/")[0])
        except Exception:
            continue

    return None


def _extract_container_ip(attrs: dict, fallback_name: str) -> str:
    network = attrs.get("NetworkSettings", {}) or {}
    networks = network.get("Networks") or {}
    for net in networks.values():
        ip = net.get("IPAddress")
        if ip:
            return ip
    return fallback_name


@router.get("/api/docker/containers")
def list_docker_containers(request: Request, endpoint_id: int | None = Query(default=None)):
    require_auth(request)
    with get_db_ctx() as conn:
        endpoint = _resolve_endpoint(conn, endpoint_id)

        # Load existing services for matching
        existing_services = {}
        for row in conn.execute("SELECT id, subdomain, domain, target_ip, target_port FROM services").fetchall():
            key_subdomain = row["subdomain"].lower()
            key_target = f"{row['target_ip']}:{row['target_port']}"
            fqdn = f"{row['subdomain']}.{row['domain']}"
            existing_services[key_subdomain] = {"id": row["id"], "fqdn": fqdn}
            existing_services[key_target] = {"id": row["id"], "fqdn": fqdn}

    client = _docker_client(endpoint.get("docker_host") or None)

    containers = []
    try:
        for c in client.containers.list():
            attrs = c.attrs or {}
            labels = (attrs.get("Config", {}) or {}).get("Labels") or {}
            port = _extract_container_port(attrs)
            ip = _extract_container_ip(attrs, c.name)

            suggestion = analyze_container(labels, c.name, port)
            suggested_subdomain = suggestion["subdomain"].lower()
            target_key = f"{ip}:{suggestion['target_port'] or port or 0}"

            # Check if this container matches an existing service
            existing_match = None
            if suggested_subdomain in existing_services:
                existing_match = existing_services[suggested_subdomain]
            elif target_key in existing_services:
                existing_match = existing_services[target_key]

            containers.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "image": c.image.tags[0] if c.image and c.image.tags else c.image.short_id,
                    "status": c.status,
                    "target_ip": ip,
                    "target_port": suggestion["target_port"] if suggestion["target_port"] is not None else port,
                    "labels": labels,
                    # Legacy fields kept for backwards-compatibility with existing frontend
                    "suggested_subdomain": suggestion["subdomain"],
                    "suggested_scheme": suggestion["forward_scheme"],
                    "websocket": suggestion["websocket"],
                    # Enriched suggestion block
                    "suggestion": dict(suggestion),
                    "endpoint_id": endpoint.get("id"),
                    "endpoint_name": endpoint.get("name"),
                    # Matching info
                    "existing_service": existing_match,
                }
            )
    except Exception as e:
        raise HTTPException(500, f"Failed to list Docker containers: {e}")

    containers.sort(key=lambda row: row["name"])
    return containers


@router.post("/api/docker/import")
def import_docker_containers(request: Request, data: dict = Body(...)):
    require_auth(request, scope="write")

    domain = (data.get("domain") or "").strip().lower()
    if not domain or "." not in domain:
        raise HTTPException(400, "A valid domain is required")

    containers = data.get("containers") or []
    if not isinstance(containers, list) or not containers:
        raise HTTPException(400, "No containers selected")

    proxy_provider_id = int(data["proxy_provider_id"]) if data.get("proxy_provider_id") not in (None, "") else None
    dns_provider_id = int(data["dns_provider_id"]) if data.get("dns_provider_id") not in (None, "") else None
    dns_ip = (data.get("dns_ip") or "").strip()
    endpoint_id = data.get("endpoint_id")

    endpoint_name = "default"
    if endpoint_id not in (None, ""):
        with get_db_ctx() as conn_check:
            endpoint = _resolve_endpoint(conn_check, int(endpoint_id))
        endpoint_name = endpoint.get("name") or "default"

    conn = get_db()
    try:
        imported = 0
        skipped = 0
        errors: list[str] = []

        try:
            for item in containers:
                try:
                    name = item.get("name") or ""
                    subdomain = _sanitize_subdomain(item.get("subdomain") or item.get("suggested_subdomain") or name)
                    target_ip = (item.get("target_ip") or "").strip()
                    target_port = int(item.get("target_port") or 0)
                    forward_scheme = item.get("forward_scheme") or item.get("suggested_scheme") or "http"
                    websocket = bool(item.get("websocket"))

                    if not target_ip or target_port <= 0:
                        skipped += 1
                        continue

                    existing = conn.execute(
                        "SELECT id FROM services WHERE subdomain=? AND domain=?",
                        (subdomain, domain),
                    ).fetchone()
                    if existing:
                        skipped += 1
                        continue

                    conn.execute(
                        """INSERT INTO services
                           (subdomain, domain, target_ip, target_port, forward_scheme,
                            websocket, enabled, dns_provider_id, proxy_provider_id, dns_ip)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            subdomain,
                            domain,
                            target_ip,
                            target_port,
                            "https" if str(forward_scheme).lower() == "https" else "http",
                            int(websocket),
                            1,
                            dns_provider_id,
                            proxy_provider_id,
                            dns_ip,
                        ),
                    )
                    conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
                    imported += 1
                    add_log("ok", f"Docker [{endpoint_name}] imported: {subdomain}.{domain} → {target_ip}:{target_port}", conn)
                except Exception as e:
                    errors.append(str(e))

            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return {"imported": imported, "skipped": skipped, "errors": errors}
