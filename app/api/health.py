import time
import shutil
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.models import get_db_ctx
from app.config import APP_VERSION

router = APIRouter()

@router.get("/api/health")
def health():
    start = time.monotonic()
    try:
        with get_db_ctx() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
        
    try:
        total, used, free = shutil.disk_usage("/")
        disk_usage = round((used / total) * 100, 1)
    except Exception:
        disk_usage = 0
        
    latency = round((time.monotonic() - start) * 1000, 1)
    status  = 200 if db_ok else 503
    return JSONResponse({
        "ok": db_ok, 
        "db": db_ok, 
        "latency_ms": latency,
        "disk_usage": disk_usage,
        "version": APP_VERSION
    }, status_code=status)
