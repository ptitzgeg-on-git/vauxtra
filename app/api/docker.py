import re
import sqlite3
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from app.auth import require_auth
from app.importing import refuse_import, set_aside
from app.models import add_log, get_db, get_db_ctx
from app.services.docker_analyzer import analyze_container
from app.text import plural
from app.validators import is_valid_domain, normalize_domain

router = APIRouter()


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


# The Docker daemon is a far end like any other, and this file used to give that one
# situation two numbers of its own: 503 here, 500 in `list_docker_containers` below, next to
# the 502 `app/api/providers.py` and `app/api/webhooks.py` already answer for a provider or a
# notification target that refused. Three codes for "I asked someone else and what came back
# was not usable" is three different accusations. 502 is the one the other two files argue
# for: 503 says *this* server is unavailable, which blames Vauxtra for a socket that was
# never mounted or a `tcp://` host that is down, and 504 would claim a timeout that a bare
# daemon exception does not let us tell from a flat refusal. What stays 500 is the missing
# package below -- that one really is a broken installation of Vauxtra.
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
        raise HTTPException(
            502,
            f"Docker daemon unavailable: {e}. Check the endpoint's Docker host, "
            "and that this instance can reach it.",
        )


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
    """Store a new Docker endpoint. 409 only when that host is already stored.

    The duplicate is asked for before the write, the way `add_environment` and `create_tag`
    ask (`app/api/environments.py`, `app/api/tags.py`). It used to be read off the UNIQUE
    index through a bare `except Exception` around the INSERT, and that handler had no way
    to tell a duplicate from anything else the statement could raise: a locked database, a
    column a half-applied migration never added and a full disk all came back to the
    operator as `409 Docker endpoint host already exists`, which sends them hunting for a
    row that was never written.

    A write that fails for one of those reasons now leaves as what it is -- an unhandled
    `sqlite3.OperationalError`, answered 500 -- rather than as a sentence naming a cause
    nobody measured.

    The lookup alone was not enough. A SELECT followed by an INSERT is two statements, not
    one atomic step: two calls storing the same host both passed the lookup, the second
    INSERT hit the UNIQUE index, and the `sqlite3.IntegrityError` nobody caught reached the
    operator as 500 -- the route saying it had broken over a duplicate it used to name. The
    lookup stays, because it is what produces the readable refusal in the ordinary case; the
    handler below catches the one exception the index raises, and answers the same 409 with
    the same sentence.
    """
    require_auth(request, scope="write")
    conn = get_db()
    try:
        existing = conn.execute("SELECT COUNT(*) FROM docker_endpoints").fetchone()[0]
        is_default = 1 if existing == 0 else 0

        # `docker_host` is the UNIQUE column of `docker_endpoints` (app/models.py); `name`
        # is not, and two endpoints may share one.
        duplicate = conn.execute(
            "SELECT id FROM docker_endpoints WHERE docker_host=?", (body.docker_host,)
        ).fetchone()
        if duplicate:
            raise HTTPException(409, "Docker endpoint host already exists")

        try:
            cur = conn.execute(
                "INSERT INTO docker_endpoints (name, docker_host, enabled, is_default) VALUES (?,?,?,?)",
                (body.name, body.docker_host, int(body.enabled), is_default),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # The duplicate that arrived between the lookup and this statement. Only the
            # exception the UNIQUE index raises is caught, so the three faults listed above
            # -- all `sqlite3.OperationalError` -- keep leaving as our own 500.
            raise HTTPException(409, "Docker endpoint host already exists")
        endpoint_id = cur.lastrowid
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

    # This `try` holds a single statement, and so does the one inside the loop below: between
    # them they cover every line of this route that reaches the daemon, and no other. The
    # guard used to span the whole loop while its comment claimed to wrap "the remote call":
    # it wrapped `_extract_container_port`, `_extract_container_ip` and `analyze_container`
    # too, which are Vauxtra's own code reading a dictionary the daemon had already handed
    # over. A bug in one of those came back as `502 Failed to list Docker containers`, a
    # sentence that names the operator's daemon and sends them to inspect a host that had
    # just answered correctly.
    # The boundary sits here because this is where the request leaves the process: past it,
    # nothing is asked of anybody, so anything that breaks is ours and surfaces as a 500 with
    # our traceback.
    try:
        listed = client.containers.list()
    except Exception as e:
        # The same far end as `_docker_client` above, reached a few lines later: the client
        # answered `ping` and then broke, or the daemon refused the listing. Vauxtra did not
        # break, so it does not answer 500 for it.
        raise HTTPException(502, f"Failed to list Docker containers: {e}")

    containers = []
    for c in listed:
        attrs = c.attrs or {}
        labels = (attrs.get("Config", {}) or {}).get("Labels") or {}
        port = _extract_container_port(attrs)
        ip = _extract_container_ip(attrs, c.name)

        # The second and last statement that reaches the daemon, and the reason the boundary
        # is two lines rather than one: docker-py resolves `Container.image` lazily, through
        # `client.images.get()` on the same socket (docker/models/containers.py), so reading
        # it can be refused the way the listing can. Picking which of its names to display is
        # ours, and stays outside.
        try:
            image = c.image
        except Exception as e:
            raise HTTPException(502, f"Failed to read the image of container {c.name}: {e}")

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
                # `image` is `None` when the container has no image id -- an image removed
                # while its container kept running, which Docker allows. The old expression
                # fell through to `image.short_id` in exactly that case and raised
                # `AttributeError`, so one such container took the entire listing with it:
                # every other container on the host disappeared from the discovery panel
                # behind a single 500. It is now the one row with a blank image name.
                "image": (image.tags[0] if image.tags else image.short_id) if image else "",
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

    containers.sort(key=lambda row: row["name"])
    return containers


@router.post("/api/docker/import")
def import_docker_containers(request: Request, data: dict = Body(...)):
    """Turn selected containers into services, and say what became of each one.

    The answer used to be `{"imported": n, "skipped": n, "errors": [...]}` with `skipped` a
    single integer covering two outcomes that have nothing to do with each other: a
    container already tracked under that name, which is the nominal result of ticking a
    whole page and pressing import, and a container this route *refused* -- no address, no
    port -- which is the one thing the operator has to go and fix. Neither was named and
    neither was written to the journal, so a run that dropped five of six selected
    containers for want of a reachable address answered "5 skipped" and left no record
    anywhere of which five, or why.

    `/api/services/import` had already been through this. It answers with `skipped` and
    `errors` as two lists of sentences, so this one does too, through the same two helpers
    in `app/importing.py` -- the point of that module being that the next fix cannot land on
    one import and not the other.
    """
    require_auth(request, scope="write")

    domain = normalize_domain(data.get("domain") or "")
    if not is_valid_domain(domain):
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
        skipped: list[str] = []
        errors: list[str] = []

        try:
            for item in containers:
                # Named before anything can go wrong with it, so that a row refused below is
                # refused under something the operator can recognise in the container list.
                # `_sanitize_subdomain` can rewrite a name past recognition and falls back to
                # the literal "service" for a name made entirely of characters it strips, so
                # the sentence carries what Docker called the container, not what we call it.
                name = str(item.get("name") or "").strip() or "an unnamed container"
                try:
                    subdomain = _sanitize_subdomain(item.get("subdomain") or item.get("suggested_subdomain") or name)
                    target_ip = (item.get("target_ip") or "").strip()
                    target_port = int(item.get("target_port") or 0)
                    forward_scheme = item.get("forward_scheme") or item.get("suggested_scheme") or "http"
                    websocket = bool(item.get("websocket"))

                    if not target_ip or target_port <= 0:
                        # A refusal, not a row passed over. The operator ticked this container
                        # and nothing was created for it: a service with no address or no port
                        # would answer nothing, so it is not written, and saying which field
                        # was missing is the difference between a number and somewhere to go.
                        # A container on a network Vauxtra cannot read, or one publishing no
                        # port, arrives here -- both are visible in the discovery list, and
                        # neither is visible in a count.
                        missing = "no address" if not target_ip else f"no usable port (got {target_port})"
                        refuse_import(errors, conn, name, f"the container has {missing}")
                        continue

                    existing = conn.execute(
                        "SELECT id FROM services WHERE subdomain=? AND domain=?",
                        (subdomain, domain),
                    ).fetchone()
                    if existing:
                        set_aside(skipped, name, f"Vauxtra already tracks {subdomain}.{domain}")
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
                    add_log("info", f"Docker [{endpoint_name}] imported: {subdomain}.{domain} → {target_ip}:{target_port}", conn)
                except Exception as e:
                    # `str(e)` on its own named the fault and not the row, and two faults
                    # reach here. `int()` on a port the panel passed through as text says
                    # `invalid literal for int() with base 10`; and a name another writer
                    # stored between the lookup above and this INSERT says `UNIQUE constraint
                    # failed: services.subdomain, services.domain`, because `services` carries
                    # a UNIQUE index on that pair (`idx_services_hostname`). Neither sentence
                    # says which of the twelve ticked containers produced it.
                    #
                    # Two containers in the *same* payload that sanitise to one name do not
                    # arrive here: the first INSERT is visible to the lookup on the next
                    # turn of this loop through the same connection, so the second is passed
                    # over by name. Asserted, rather than assumed, in
                    # `tests/test_docker_import_outcomes.py`.
                    #
                    # Everything is caught, on purpose: this is a per-row handler inside a
                    # loop, and one unusable container must not cost the operator the eleven
                    # good ones. The rows that did import survive -- the commit is after the
                    # loop, and only a failure of the commit itself rolls the run back.
                    refuse_import(errors, conn, name, f"importing it raised {type(e).__name__}: {e}")

            if skipped:
                # One line for the run. See `set_aside` in `app/importing.py`.
                add_log(
                    "info",
                    f"Docker [{endpoint_name}] passed over {plural(len(skipped), 'container')} already tracked",
                    conn,
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return {"imported": imported, "skipped": skipped, "errors": errors}
