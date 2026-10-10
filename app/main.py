import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from app.config import DEBUG, HTTPS_ONLY, SECRET_KEY
from app.limiter import limiter
from app.models import init_db
from app.security import validate_cors_origins

_logger = logging.getLogger(__name__)

from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.backup import router as backup_router
from app.api.certificates import router as certificates_router
from app.api.docker import router as docker_router
from app.api.environments import router as environments_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.providers import router as providers_router
from app.api.services import router as services_router
from app.api.settings import router as settings_router
from app.api.sync import router as sync_router
from app.api.tags import router as tags_router
from app.api.templates import router as templates_router
from app.api.webhooks import router as webhooks_router

_DIR = os.path.dirname(os.path.abspath(__file__))

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_db()
    from app.auth import (
        auth_is_downgraded,
        has_password_configured,
        revoke_sessions_if_env_password_changed,
    )
    from app.models import get_db
    from app.scheduler import start

    if revoke_sessions_if_env_password_changed():
        _logger.info("APP_PASSWORD changed since the last start: every open session was ended.")

    # Both of these are states an operator reaches without ever being told. The wizard has a
    # *Skip* button on the password step, and a database restored from the wrong file can
    # lose the hash. One line in the logs at every boot is the cheapest possible warning.
    if auth_is_downgraded():
        _logger.error(
            "SECURITY: this instance was configured with an admin password and the hash is "
            "no longer in the database. Every request will be refused until the database is "
            "restored or APP_PASSWORD is set. Vauxtra will not fall back to anonymous access."
        )
    elif not has_password_configured():
        _logger.warning(
            "SECURITY: no admin password is configured. Every request reaching this instance "
            "is granted the admin scope, with no credential. Set one in Settings > API keys, "
            "or through APP_PASSWORD, before exposing port 8888 to anything but localhost."
        )

    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='check_interval'").fetchone()
    conn.close()
    # This used to be a bare `int()`, on a value any `write`-scoped caller could set: one bad
    # row and the application never came up again, on every restart. The write side validates
    # it now; this stays defensive because a database that already holds a bad value has to
    # boot before anyone can correct it.
    try:
        interval = max(0, int(str(row["value"]).strip())) if row else 0
    except (TypeError, ValueError):
        interval = 0
        _logger.warning(
            "check_interval holds %r, which is not a number. Automatic health checks stay "
            "off until it is saved again from Settings.",
            row["value"],
        )
    start(interval)
    yield


app = FastAPI(
    title="Vauxtra",
    description="Vauxtra RESTful API",
    docs_url="/api/docs" if DEBUG else None,
    # The schema follows the documentation. It was served unauthenticated on every install
    # while `/api/docs` was closed -- which hides the reading room and leaves the book on
    # the doorstep. Nothing in this repository reads it; the file is generated on demand.
    openapi_url="/openapi.json" if DEBUG else None,
    redoc_url=None,
    lifespan=_lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# The interface is served by this same application, so the working default is no
# cross-origin caller at all. It used to be three localhost origins -- the Vite dev server
# among them -- allowed *with credentials* on every deployment, while `.env.example`
# promised "leave empty for same-origin only". Those origins are what `npm run dev` needs,
# so they are what `DEBUG=true` grants, and nothing else does.
_default_cors = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8888" if DEBUG else ""
)
try:
    _cors_origins = validate_cors_origins(
        os.environ.get("CORS_ORIGINS", ""),
        _default_cors
    )
    if _cors_origins:
        _logger.info(f"CORS origins validated: {len(_cors_origins)} allowed")
    else:
        _logger.info("No CORS origin configured: same-origin callers only")
except ValueError as e:
    # A list that does not parse must not widen back to the defaults: refuse them all and
    # say which. Same-origin keeps working, which is every ordinary deployment.
    _logger.error(f"Invalid CORS configuration, no cross-origin caller is allowed: {e}")
    _cors_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="vauxtra_session",
    max_age=7 * 24 * 60 * 60,
    https_only=HTTPS_ONLY,
    same_site="strict",
)


# Everything the interface loads now comes from this origin: the Vite build under
# `/assets`, and since this change the Inter font too, which was fetched from rsms.me on
# every page load of a tool that holds provider credentials. Inline styles survive because
# React writes `style={{...}}` attributes and CSP counts those as inline; `img-src` allows
# remote https because a service carries an `icon_url` the operator chooses.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])

# Swagger UI loads its own bundle from a CDN, so the policy above would leave a blank page
# explained only in the browser console. Both paths exist only when DEBUG is on.
_CSP_EXEMPT_PATHS = frozenset({"/api/docs", "/openapi.json"})

_warned_about_forwarded_headers = False

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_warned_about_origin_mismatch = False


def _own_hosts(request: Request) -> set[str]:
    """Every `host[:port]` this request may legitimately have been addressed to.

    `Host` first. Behind a reverse proxy that rewrites it (`proxy_pass http://vauxtra:8888;`
    without `proxy_set_header Host $host;`), the browser's origin is the public name and
    `Host` the upstream one, so every write of the interface was refused, the login
    included. `X-Forwarded-Host` is accepted when `FORWARDED_ALLOW_IPS` says a proxy is in
    front: a browser cannot attach it to a cross-site request without a CORS preflight,
    which this application never grants for that header.
    """
    hosts = {request.headers.get("host", "").strip().lower()}
    if os.environ.get("FORWARDED_ALLOW_IPS", "").strip():
        forwarded = request.headers.get("x-forwarded-host", "")
        hosts.update(h.strip().lower() for h in forwarded.split(",") if h.strip())
    hosts.discard("")
    return hosts


def _is_cross_site_write(request: Request) -> bool:
    """A state-changing request a browser sent on behalf of another site.

    `SameSite=Strict` keeps the session cookie off those, but an open instance grants
    `admin` with no cookie at all, so a plain HTML form on any page could reach
    `/api/reset`. Callers without `Origin` (curl, the MCP bridge, scripts holding a Bearer
    key) are not browsers acting for a page, and pass.
    """
    global _warned_about_origin_mismatch
    if request.method.upper() in _SAFE_METHODS:
        return False
    origin = request.headers.get("origin", "").strip()
    if origin:
        if origin in _cors_origins:
            return False
        parsed = urlsplit(origin)
        if parsed.netloc and parsed.netloc.lower() in _own_hosts(request):
            return False
        if not _warned_about_origin_mismatch:
            # Said once: behind a misconfigured proxy every write fails the same way, and a
            # bare 403 in the browser does not point at the proxy.
            _warned_about_origin_mismatch = True
            _logger.warning(
                "Refused a write whose Origin (%s) does not match the Host header (%s). Behind "
                "a reverse proxy, forward the original host (nginx: `proxy_set_header Host "
                "$host;`), or list the public origin in CORS_ORIGINS.",
                origin, request.headers.get("host", ""),
            )
        return True
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    return fetch_site in ("cross-site", "same-site")


_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})


def _hostname_of(host_header: str) -> str:
    """The name in a `Host` value, without its port: `[::1]:8888` gives `::1`."""
    try:
        return (urlsplit("//" + host_header.strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _host_cannot_be_rebound(hostname: str) -> bool:
    """A `Host` value no attacker-controlled DNS name can produce.

    DNS rebinding needs the browser to address the instance under a name the attacker's
    DNS answers for, so the page and the instance share an origin. An IP literal or a
    loopback name cannot be that name; an origin the operator listed in CORS_ORIGINS is
    trusted already.
    """
    if not hostname:
        return False
    if hostname in _LOOPBACK_NAMES or hostname.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    return any(_hostname_of(urlsplit(o).netloc) == hostname for o in _cors_origins)


def _is_rebinding_target(request: Request) -> bool:
    """A request to an open instance under a name that DNS rebinding could have produced.

    The cross-site check above passes a rebound page: its `Origin` and `Host` agree. On an
    instance with a password that page gains nothing -- it has no cookie for the name and
    no credential. On an open instance, or one still in its setup wizard, every request is
    granted `admin` with no credential, so a page anywhere on the internet could call
    `/api/reset` or read the configuration through the visitor's browser. Refused only in
    that state, and only for names: reaching the instance by IP or `localhost` keeps working.
    """
    hostname = _hostname_of(request.headers.get("host", ""))
    if _host_cannot_be_rebound(hostname):
        return False
    from app.auth import has_password_configured

    try:
        return not has_password_configured()
    except Exception:
        return True


@app.middleware("http")
async def security_headers(request: Request, call_next):
    global _warned_about_forwarded_headers
    if (
        not _warned_about_forwarded_headers
        and "x-forwarded-for" in request.headers
        and not os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    ):
        # Said at the first forwarded request rather than at startup, because at startup
        # there is nothing to look at. The consequence is not cosmetic: every visitor shares
        # one rate-limit counter, so five failed logins from anywhere lock the operator out.
        _warned_about_forwarded_headers = True
        _logger.warning(
            "A request arrived with X-Forwarded-For but FORWARDED_ALLOW_IPS is not set: "
            "this instance reads the proxy's address as the client address, so every rate "
            "limit is shared by every visitor and one attacker can lock you out. Set "
            "FORWARDED_ALLOW_IPS to the address of your reverse proxy."
        )

    # The check reads SQLite when the name could be rebound: off the event loop, so a slow
    # disk does not stall every other request behind it.
    if await run_in_threadpool(_is_rebinding_target, request):
        return JSONResponse(
            {
                "detail": "This instance has no admin password, so it only answers under an "
                "IP address, localhost, or a host listed in CORS_ORIGINS. Set an admin "
                "password to reach it by name."
            },
            status_code=421,
        )

    if _is_cross_site_write(request):
        return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path not in _CSP_EXEMPT_PATHS:
        response.headers["Content-Security-Policy"] = _CSP
    if HTTPS_ONLY:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(providers_router)
app.include_router(services_router)
app.include_router(tags_router)
app.include_router(templates_router)
app.include_router(settings_router)
app.include_router(backup_router)
app.include_router(certificates_router)
app.include_router(environments_router)
app.include_router(webhooks_router)
app.include_router(sync_router)
app.include_router(docker_router)
app.include_router(api_keys_router)
app.include_router(auth_router)

frontend_dist = os.path.join(_DIR, "..", "frontend", "dist")
# Racine canonique du build. Tout chemin servi doit y etre confine : le motif
# `/{full_path:path}` ne retire pas les segments `..`, et uvicorn ne normalise
# pas le chemin, il se contente de le decoder.
_FRONTEND_ROOT = os.path.realpath(frontend_dist)
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")


def _resolve_frontend_file(full_path: str) -> str | None:
    """Resolve a request path inside the frontend build, or None if it escapes it."""
    if not full_path:
        return None
    candidate = os.path.realpath(os.path.join(_FRONTEND_ROOT, full_path))
    if candidate != _FRONTEND_ROOT and not candidate.startswith(_FRONTEND_ROOT + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"message": "API route not found"})

    file_path = _resolve_frontend_file(full_path)
    if file_path is not None:
        return FileResponse(file_path)

    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return JSONResponse(
        status_code=404,
        content={"error": "Frontend not built. Run 'npm run build' inside /frontend."},
    )
