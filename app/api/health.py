import shutil
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import is_authenticated, is_setup_incomplete
from app.config import APP_VERSION
from app.models import get_db_ctx

router = APIRouter()

@router.get("/api/health")
def health(request: Request):
    start = time.monotonic()
    try:
        with get_db_ctx() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    # B24: the route answers without authentication (container health checks, the login
    # page). The exact version tells a scanner which advisories apply, and the disk usage
    # is the host's: an anonymous caller gets the verdict, a signed-in one the details.
    # Both checks read the database: when it is down they raise, and the health check
    # must still answer 503 rather than 500. The caller then gets the anonymous verdict.
    try:
        detailed = is_setup_incomplete() or is_authenticated(request)
    except Exception:
        detailed = False
    if not detailed:
        return JSONResponse({"ok": db_ok, "db": db_ok}, status_code=status)

    try:
        total, used, free = shutil.disk_usage("/")
        disk_usage = round((used / total) * 100, 1)
    except Exception:
        disk_usage = 0

    latency = round((time.monotonic() - start) * 1000, 1)
    return JSONResponse({
        "ok": db_ok,
        "db": db_ok,
        "latency_ms": latency,
        "disk_usage": disk_usage,
        "version": APP_VERSION
    }, status_code=status)
