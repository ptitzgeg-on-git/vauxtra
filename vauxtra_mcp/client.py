"""Shared HTTP client for all MCP tools — reads VAUXTRA_URL and VAUXTRA_API_KEY from env."""
import http.cookiejar
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


# The session cookie has to outlive the request that obtained it.
#
# Every helper below opens its own short-lived `httpx.Client`, which is the right shape for a
# bridge whose base URL and timeout are read from the environment on each call. But it meant
# `auth_login` received a `Set-Cookie`, closed the client, and dropped it: the login answered
# `{"ok": true}` and authenticated nothing, and `auth_logout` cleared a session it had never
# joined. Only an API key ever worked, and the two tools said otherwise.
#
# A plain `CookieJar` is what fixes it: `httpx.Cookies` copies any jar it is handed, so
# responses would update a copy, while a bare `http.cookiejar.CookieJar` is adopted by
# reference and the clients share it.
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
    """An error the API explained, with its explanation kept.

    `raise_for_status()` produces "Client error '400 Bad Request' for url ...", which throws
    away the `detail` the API went to the trouble of writing -- and since 1.1 those details
    are the useful part: which setting was refused and why, which provider still holds a
    service, that a hostname is already taken. The agent on the other end of the bridge got
    a status code and had to guess.
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
