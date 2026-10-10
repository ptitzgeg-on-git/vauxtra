import logging
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError, field_validator

from app.auth import require_auth
from app.models import add_log, get_db, get_db_ctx
from app.services.docker_analyzer import analyze_container
from app.validators import is_valid_domain, normalize_domain

router = APIRouter()
_logger = logging.getLogger(__name__)


DOCKER_HOST_RE = re.compile(r"^(unix|tcp|ssh)://.+")


def _is_valid_docker_host(value: str) -> bool:
    if not DOCKER_HOST_RE.match(value):
        return False
    parsed = urlparse(value)
    scheme = (parsed.scheme or "").lower()

    if scheme == "unix":
        # unix:///var/run/docker.sock
        return bool(parsed.path and parsed.path.startswith("/"))

    if scheme == "tcp":
        # tcp://host:2375 or tcp://127.0.0.1:2376
        if not parsed.hostname:
            return False
        return parsed.port is not None

    if scheme == "ssh":
        # ssh://user@host or ssh://host
        return bool(parsed.hostname)

    return False


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
        if not _is_valid_docker_host(value):
            raise ValueError(
                "Invalid docker_host. Use unix:///path.sock, tcp://host:port, or ssh://user@host"
            )
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
    except Exception:
        # The exception text told "connection refused" from "timed out" from "name not
        # known": with a tcp:// endpoint pointed anywhere, a port scanner. The detail goes to
        # the server log only, which an API key cannot read.
        _logger.warning("Docker daemon unavailable at %s", docker_host or "(environment)", exc_info=True)
        raise HTTPException(503, "Docker daemon unavailable")


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
    # A remote endpoint makes the server dial any host and port on demand (S7), so it takes
    # the same scope as the settings that point the server at the network. A `unix://` path
    # is no smaller: the caller picks any socket the container can open, and the Docker one
    # it usually means is root on the host. Every endpoint is `admin`.
    require_auth(request, scope="admin")
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
    # N18: `admin`, like adding one. The endpoint decides which Docker daemon the
    # imports read, so choosing, probing or removing it is the same trust decision.
    require_auth(request, scope="admin")
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
    # N18: `admin`, like adding one. The endpoint decides which Docker daemon the
    # imports read, so choosing, probing or removing it is the same trust decision.
    require_auth(request, scope="admin")
    with get_db_ctx() as conn:
        endpoint = _resolve_endpoint(conn, endpoint_id)

    client = _docker_client(endpoint.get("docker_host") or None)
    try:
        containers_count = len(client.containers.list(ignore_removed=True))
    finally:
        client.close()
    return {
        "ok": True,
        "endpoint_id": endpoint.get("id"),
        "endpoint_name": endpoint.get("name"),
        "docker_host": endpoint.get("docker_host"),
        "containers": containers_count,
    }


@router.delete("/api/docker/endpoints/{endpoint_id}")
def delete_docker_endpoint(endpoint_id: int, request: Request):
    # N18: `admin`, like adding one. The endpoint decides which Docker daemon the
    # imports read, so choosing, probing or removing it is the same trust decision.
    require_auth(request, scope="admin")
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


# Labels a reader of the discovery list needs: the routing ones the analyzer reads and the
# two that say which compose stack a container belongs to. Everything else stays on the
# host: labels routinely carry basic-auth hashes, tokens and file paths, and the listing is
# open to read-only keys.
_LABEL_PREFIXES = ("vauxtra.", "traefik.")
_LABEL_KEYS = {"com.docker.compose.project", "com.docker.compose.service"}
_SENSITIVE_LABEL_PARTS = ("auth", "pass", "secret", "token", "key", "users", "credential")


def _public_labels(labels: dict) -> dict[str, str]:
    kept: dict[str, str] = {}
    for key, value in (labels or {}).items():
        name = str(key)
        lowered = name.lower()
        if lowered not in _LABEL_KEYS and not lowered.startswith(_LABEL_PREFIXES):
            continue
        if any(part in lowered for part in _SENSITIVE_LABEL_PARTS):
            continue
        kept[name] = value
    return kept


def _extract_container_port(attrs: dict) -> int | None:
    network = attrs.get("NetworkSettings", {}) or {}
    ports = network.get("Ports") or {}
    for key in ports:
        try:
            return int(str(key).split("/")[0])
        except Exception:
            continue

    exposed = (attrs.get("Config", {}) or {}).get("ExposedPorts") or {}
    for key in exposed:
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
        # `ignore_removed`: a container removed between the listing and its inspection
        # failed the whole route with a 500.
        for c in client.containers.list(ignore_removed=True):
            attrs = c.attrs or {}
            # `c.image` is a fresh API call on every access, and it was read three times per
            # container; an image deleted since the container started raised here too.
            try:
                image = c.image
                image_name = (image.tags[0] if image.tags else image.short_id) if image else ""
            except Exception:
                image_name = str((attrs.get("Config", {}) or {}).get("Image") or "")
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
                    "image": image_name,
                    "status": c.status,
                    "target_ip": ip,
                    "target_port": suggestion["target_port"] if suggestion["target_port"] is not None else port,
                    "labels": _public_labels(labels),
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
    except Exception:
        _logger.warning("Failed to list Docker containers", exc_info=True)
        raise HTTPException(500, "Failed to list Docker containers")
    finally:
        client.close()

    containers.sort(key=lambda row: row["name"])
    return containers


@router.post("/api/docker/import")
def import_docker_containers(request: Request, data: dict = Body(...)):
    require_auth(request, scope="write")

    domain = normalize_domain(data.get("domain") or "")
    if not is_valid_domain(domain):
        raise HTTPException(400, "A valid domain is required")

    containers = data.get("containers") or []
    if not isinstance(containers, list) or not containers:
        raise HTTPException(400, "No containers selected")

    try:
        proxy_provider_id = int(data["proxy_provider_id"]) if data.get("proxy_provider_id") not in (None, "") else None
        dns_provider_id = int(data["dns_provider_id"]) if data.get("dns_provider_id") not in (None, "") else None
        endpoint_id = data.get("endpoint_id")
        if endpoint_id not in (None, ""):
            endpoint_id = int(endpoint_id)
    except (TypeError, ValueError):
        # A non-numeric id came back as a 500.
        raise HTTPException(400, "Provider and endpoint ids must be numbers") from None
    dns_ip = str(data.get("dns_ip") or "").strip()

    endpoint_name = "default"
    if endpoint_id not in (None, ""):
        with get_db_ctx() as conn_check:
            endpoint = _resolve_endpoint(conn_check, int(endpoint_id))
        endpoint_name = endpoint.get("name") or "default"

    # Late import, as in `sync.import_services`.
    from app.api.services import ServiceIn, _conflicting_service

    conn = get_db()
    try:
        imported = 0
        skipped = 0
        errors: list[str] = []

        try:
            for item in containers:
                name = ""
                try:
                    if not isinstance(item, dict):
                        raise ValueError("Each container must be an object")
                    name = str(item.get("name") or "")
                    subdomain = _sanitize_subdomain(item.get("subdomain") or item.get("suggested_subdomain") or name)
                    target_ip = str(item.get("target_ip") or "").strip()
                    target_port = int(item.get("target_port") or 0)
                    forward_scheme = item.get("forward_scheme") or item.get("suggested_scheme") or "http"
                    websocket = bool(item.get("websocket"))

                    if not target_ip or target_port <= 0:
                        skipped += 1
                        continue

                    # The same checks as a service created by hand: the target address, the
                    # port and the DNS target used to be stored unvalidated.
                    checked = ServiceIn(
                        subdomain=subdomain,
                        domain=domain,
                        target_ip=target_ip,
                        target_port=target_port,
                        forward_scheme="https" if str(forward_scheme).lower() == "https" else "http",
                        websocket=websocket,
                        dns_provider_id=dns_provider_id,
                        proxy_provider_id=proxy_provider_id,
                        dns_ip=dns_ip,
                    )
                    subdomain, target_ip, target_port = (
                        checked.subdomain, checked.target_ip, checked.target_port
                    )

                    # Same rule as a service created by hand: names compare without case,
                    # and a tunnel already publishing this name counts as taken.
                    if _conflicting_service(
                        conn, f"{subdomain}.{domain}".lower(), subdomain=subdomain, domain=domain
                    ):
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
                            checked.forward_scheme,
                            int(checked.websocket),
                            1,
                            checked.dns_provider_id,
                            checked.proxy_provider_id,
                            checked.dns_ip,
                        ),
                    )
                    conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
                    imported += 1
                    add_log("info", f"Docker [{endpoint_name}] imported: {subdomain}.{domain} → {target_ip}:{target_port}", conn)
                except ValidationError as e:
                    # One line per container, named: `str(e)` spread each failure over
                    # several lines and never said which container it was about.
                    first = e.errors()[0] if e.errors() else {}
                    reason = str(first.get("msg") or "invalid value")
                    errors.append(f"{name}: {reason}" if name else reason)
                except Exception as e:
                    errors.append(f"{name}: {e}" if name else str(e))

            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return {"imported": imported, "skipped": skipped, "errors": errors}
