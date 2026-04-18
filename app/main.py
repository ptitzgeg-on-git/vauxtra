import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.limiter import limiter

from app.config import SECRET_KEY, HTTPS_ONLY, DEBUG
from app.models import init_db

from app.api.health        import router as health_router
from app.api.providers     import router as providers_router
from app.api.services      import router as services_router
from app.api.tags          import router as tags_router
from app.api.settings      import router as settings_router
from app.api.backup        import router as backup_router
from app.api.certificates  import router as certificates_router
from app.api.environments  import router as environments_router
from app.api.webhooks      import router as webhooks_router
from app.api.sync          import router as sync_router
from app.api.docker        import router as docker_router
from app.api.api_keys      import router as api_keys_router
from app.api.auth          import router as auth_router

_DIR = os.path.dirname(os.path.abspath(__file__))

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_db()
    from app.scheduler import start
    from app.models import get_db
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='check_interval'").fetchone()
    conn.close()
    start(int(row["value"]) if row else 0)
    yield


app = FastAPI(
    title="Vauxtra",
    description="Vauxtra RESTful API",
    docs_url="/api/docs" if DEBUG else None,
    redoc_url=None,
    lifespan=_lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_default_cors = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8888"
_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", _default_cors).split(",")
    if o.strip()
]

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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if HTTPS_ONLY:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

app.include_router(health_router)
app.include_router(providers_router)
app.include_router(services_router)
app.include_router(tags_router)
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
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"message": "API route not found"})

    file_path = os.path.join(frontend_dist, full_path)
    if full_path and os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return JSONResponse(
        status_code=404,
        content={"error": "Frontend not built. Run 'npm run build' inside /frontend."},
    )
