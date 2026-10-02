"""Shared HTTP client for all MCP tools: reads VAUXTRA_URL and VAUXTRA_API_KEY from env."""
import http.cookiejar
import os

import httpx

VAUXTRA_URL = os.environ.get("VAUXTRA_URL", "http://localhost:8888").rstrip("/")
_API_KEY = os.environ.get("VAUXTRA_API_KEY", "")


def _timeout() -> float:
    """Read VAUXTRA_TIMEOUT on each call (tests change it).

    The default is generous: a push or reconcile walks every provider in series.
    """
    raw = os.environ.get("VAUXTRA_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else 120.0
    except ValueError:
        return 120.0
    return value if value > 0 else 120.0


# Shared cookie jar so a session from auth_login survives across the short-lived clients.
# A plain CookieJar is shared by reference; httpx.Cookies would copy it.
_SESSION_JAR = http.cookiejar.CookieJar()


def session_jar() -> http.cookiejar.CookieJar:
    """The shared jar, for the one caller that builds its own client (the SSE snapshot)."""
    return _SESSION_JAR


def session_cookie_count() -> int:
    """How many session cookies the bridge is currently holding. Used by the tests."""
    return len(_SESSION_JAR)


def clear_session() -> None:
    """Forget the session cookie. `auth_logout` calls this after the server clears its side."""
    _SESSION_JAR.clear()


class ApiError(RuntimeError):
    """An HTTP error carrying the API's `detail` message, which is what tells the agent
    what was refused and why.
    """

    def __init__(self, status_code: int, detail: str, method: str, url: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{method} {url} -> {status_code}: {detail}")


def check(response: httpx.Response) -> httpx.Response:
    """Return the response, or raise `ApiError` carrying what the API actually said."""
    if not response.is_error:
        return response

    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        raw = payload.get("detail", payload.get("message", ""))
        if isinstance(raw, list):
            # FastAPI's 422 shape: one entry per field that failed validation.
            detail = "; ".join(
                f"{'.'.join(str(x) for x in item.get('loc', [])[1:])}: {item.get('msg', '')}".strip(": ")
                for item in raw
                if isinstance(item, dict)
            )
        else:
            detail = str(raw)

    if not detail:
        detail = (response.text or response.reason_phrase or "").strip()[:500]

    raise ApiError(response.status_code, detail or "no detail", response.request.method, str(response.request.url))


def has_api_key() -> bool:
    return bool(_API_KEY)


def auth_headers() -> dict[str, str]:
    if _API_KEY:
        return {"Authorization": f"Bearer {_API_KEY}"}
    return {}


def get(path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout(), cookies=_SESSION_JAR) as c:
        return c.get(f"/api{path}", headers=auth_headers(), **kwargs)


def post(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout(), cookies=_SESSION_JAR) as c:
        return c.post(f"/api{path}", json=json, headers=auth_headers(), **kwargs)


def delete(path: str, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout(), cookies=_SESSION_JAR) as c:
        return c.delete(f"/api{path}", headers=auth_headers(), **kwargs)


def put(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout(), cookies=_SESSION_JAR) as c:
        return c.put(f"/api{path}", json=json, headers=auth_headers(), **kwargs)


def patch(path: str, json=None, **kwargs) -> httpx.Response:
    with httpx.Client(base_url=VAUXTRA_URL, timeout=_timeout(), cookies=_SESSION_JAR) as c:
        return c.patch(f"/api{path}", json=json, headers=auth_headers(), **kwargs)
