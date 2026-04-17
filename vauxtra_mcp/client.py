"""Shared HTTP client for all MCP tools — reads VAUXTRA_URL and VAUXTRA_API_KEY from env."""
import os
import httpx

VAUXTRA_URL = os.environ.get("VAUXTRA_URL", "http://localhost:8888").rstrip("/")
_API_KEY = os.environ.get("VAUXTRA_API_KEY", "")


def auth_headers() -> dict[str, str]:
    if _API_KEY:
        return {"Authorization": f"Bearer {_API_KEY}"}
    return {}


def get(path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=30) as c:
        return c.get(f"/api{path}", headers=auth_headers(), **kwargs)


def post(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=30) as c:
        return c.post(f"/api{path}", json=json, headers=auth_headers(), **kwargs)


def delete(path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=30) as c:
        return c.delete(f"/api{path}", headers=auth_headers(), **kwargs)


def put(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=30) as c:
        return c.put(f"/api{path}", json=json, headers=auth_headers(), **kwargs)
