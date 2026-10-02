import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
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

# The generated key is 64 hex characters; only a hand-set SECRET_KEY can fall short of this.
_MIN_KEY_LENGTH = 32


def _warn_if_secret_key_is_short() -> None:
    if len(SECRET_KEY) < _MIN_KEY_LENGTH:
        _logger.warning(
            "SECURITY: SECRET_KEY is shorter than %d characters. It signs the session cookie "
            "and encrypts the stored credentials, so use a longer random value. Changing it "
            "makes the stored credentials unreadable: re-enter them afterwards.",
            _MIN_KEY_LENGTH,
        )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_db()
    from app.auth import auth_is_downgraded, has_password_configured
    from app.models import get_db
    from app.scheduler import start

    # Warn at every boot about an open or downgraded instance.
    if auth_is_downgraded():
        _logger.error(
            "SECURITY: this instance was configured with an admin password and the hash is "
            "no longer in the database. Every request will be refused until the database is "
            "restored or APP_PASSWORD is set. Vauxtra will not fall back to anonymous access."
        )
    elif not has_password_configured():
        _logger.warning(
            "SECURITY: no admin password is configured. Every request reaching this instance "
            "is granted the admin scope, with no credential. Set one in Settings > Security, "
            "or through APP_PASSWORD, before exposing port 8888 to anything but localhost."
        )
    _warn_if_secret_key_is_short()

    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='check_interval'").fetchone()
    conn.close()
    # Defensive int(): a bad stored value must not stop the app from booting.
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
    # The schema is exposed only when the docs are.
    openapi_url="/openapi.json" if DEBUG else None,
    redoc_url=None,
    lifespan=_lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Same-origin by default. DEBUG adds the Vite dev server origins.
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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


# All assets are self-hosted. Inline styles are allowed because React writes style
# attributes; img-src allows https for operator-chosen service icons.
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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers (nosniff, frame deny, referrer policy, CSP, HSTS when HTTPS_ONLY).

    Also warns once when X-Forwarded-For arrives without FORWARDED_ALLOW_IPS.
    """
    global _warned_about_forwarded_headers
    if (
        not _warned_about_forwarded_headers
        and "x-forwarded-for" in request.headers
        and not os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    ):
        # Without it every visitor shares one rate-limit counter.
        _warned_about_forwarded_headers = True
        _logger.warning(
            "A request arrived with X-Forwarded-For but FORWARDED_ALLOW_IPS is not set: "
            "this instance reads the proxy's address as the client address, so every rate "
            "limit is shared by every visitor and one attacker can lock you out. Set "
            "FORWARDED_ALLOW_IPS to the address of your reverse proxy."
        )

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
# Every served path must stay inside the build root: the catch-all route does not
# strip `..` segments and uvicorn does not normalize the path.
_FRONTEND_ROOT = os.path.realpath(frontend_dist)
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")


# Served files keep their names across builds, so ask the browser to revalidate. The
# hashed bundles under /assets are not served here.
_REVALIDATE = {"Cache-Control": "no-cache"}


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
        return FileResponse(file_path, headers=dict(_REVALIDATE))

    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, headers=dict(_REVALIDATE))

    return JSONResponse(
        status_code=404,
        content={"error": "Frontend not built. Run 'npm run build' inside /frontend."},
    )
