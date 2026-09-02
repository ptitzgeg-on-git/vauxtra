"""Shared HTTP client for all MCP tools — reads VAUXTRA_URL and VAUXTRA_API_KEY from env."""
import os

import httpx

VAUXTRA_URL = os.environ.get("VAUXTRA_URL", "http://localhost:8888").rstrip("/")
_API_KEY = os.environ.get("VAUXTRA_API_KEY", "")


def _timeout() -> float:
    """Read VAUXTRA_TIMEOUT once per call so a test can change it.

    The default is generous on purpose: a single push or reconcile walks every configured
    provider in series, each with its own timeout, so 30 s cut off perfectly healthy calls
    and the tool reported a failure for work the server went on to finish.
    """
    raw = os.environ.get("VAUXTRA_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else 120.0
    except ValueError:
        return 120.0
    return value if value > 0 else 120.0


def auth_headers() -> dict[str, str]:
    if _API_KEY:
        return {"Authorization": f"Bearer {_API_KEY}"}
    return {}


def get(path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout()) as c:
        return c.get(f"/api{path}", headers=auth_headers(), **kwargs)


def post(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout()) as c:
        return c.post(f"/api{path}", json=json, headers=auth_headers(), **kwargs)


def delete(path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout()) as c:
        return c.delete(f"/api{path}", headers=auth_headers(), **kwargs)


def put(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout()) as c:
        return c.put(f"/api{path}", json=json, headers=auth_headers(), **kwargs)
