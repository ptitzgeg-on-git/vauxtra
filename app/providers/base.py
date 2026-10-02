"""Abstract base classes for DNS and Reverse Proxy providers."""

from abc import ABC, abstractmethod

import requests

from app.config import PROVIDER_TIMEOUT
from app.security import redact_query_secrets


class TimeoutSession(requests.Session):
    """A `requests.Session` that applies a default timeout to every call.

    requests ignores a `timeout` attribute on the session, and one hung provider would
    block the single-threaded scheduler. An explicit `timeout=` still wins.
    """

    def __init__(self, timeout: float = PROVIDER_TIMEOUT):
        super().__init__()
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().request(method, url, **kwargs)

    # `requests` drops `Authorization` when a redirect leaves the host, and nothing else.
    # These carry the same credential for PowerDNS and Pi-hole v6.
    _CREDENTIAL_HEADERS = ("X-API-Key", "X-FTL-SID", "X-FTL-CSRF")

    def rebuild_auth(self, prepared_request, response):
        super().rebuild_auth(prepared_request, response)
        if self.should_strip_auth(response.request.url, prepared_request.url):
            for name in self._CREDENTIAL_HEADERS:
                prepared_request.headers.pop(name, None)


def reachability_check(session, url: str) -> dict:
    """Diagnostic check: did anything answer at `url`?

    Any HTTP status counts as reachable; only a transport error (refused, DNS, timeout)
    fails. Kept separate from the credentials check so a wrong password is not reported
    as a connection failure.
    """
    try:
        session.get(url, timeout=PROVIDER_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        return {
            "name": "Reachability",
            "ok": False,
            "detail": f"Could not reach {url}: {exc}",
            "detail_code": "connection_failed",
            "detail_params": {"error": str(exc)},
            "blocking": True,
        }
    return {
        "name": "Reachability",
        "ok": True,
        "detail": f"{url} answered",
        "detail_code": "connection_ok",
        "blocking": False,
    }


def login_check(ok: bool) -> dict:
    """One diagnostic check for credentials the host was reachable enough to refuse."""
    return {
        "name": "Login",
        "ok": ok,
        "detail": "Authenticated successfully" if ok else "Login failed -- check username/password and URL",
        "detail_code": "login_ok" if ok else "login_failed",
        "blocking": True,
    }


class ProviderListingRefused(RuntimeError):
    """Raised by list_rewrites when the provider could not return a complete listing.

    Distinct from an empty list, which callers treat as "nothing there". The message
    is passed through redact_query_secrets.
    """

    def __init__(self, message: object = "") -> None:
        super().__init__(redact_query_secrets(str(message)))


# Optional method, not declared here because the diagnostics route checks for it with
# hasattr and falls back to test_connection():
#
#   validate_permissions(hostname_hint="", write_probe=False) -> dict
#
# Returns {"ok": bool, "checks": [...], "warnings": [str, ...]}. Each check is a dict with
# "name", "ok", "blocking", "detail" (English) and usually "detail_code" (i18n key under
# providers.diag.detail), plus optional "detail_params" and "skipped" (a check not run:
# ok is False but it is not a warning). "ok" is False when a blocking check failed, and
# "warnings" holds English notes on non-blocking problems (often empty). write_probe asks
# for a real write test where the provider supports one.
class DNSProvider(ABC):
    """Common interface for all DNS providers (AdGuard, Pi-hole, etc.)."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_rewrites(self) -> list[dict]:
        """Return every rewrite as [{'domain': ..., 'answer': ...}].

        An empty list means the provider holds nothing. An incomplete listing raises
        (ProviderListingRefused or the client's own error), never returns a partial list.
        """

    def records_for(self, domain: str) -> list[dict]:
        """Return the records named exactly `domain`, shaped like list_rewrites.

        Filters the full listing; providers that can query one name override this.
        Raises when the listing does.
        """
        wanted = (domain or "").strip().strip(".").lower()
        return [
            r
            for r in self.list_rewrites() or []
            if str(r.get("domain") or "").strip().strip(".").lower() == wanted
        ]

    @abstractmethod
    def add_rewrite(self, domain: str, ip: str) -> bool:
        """Add a DNS rewrite."""

    @abstractmethod
    def delete_rewrite(self, domain: str, ip: str) -> bool:
        """Delete a DNS rewrite."""

    def update_rewrite(self, old_domain: str, old_ip: str, new_domain: str, new_ip: str) -> bool:
        """Update a rewrite (create new first, then delete old to avoid data loss)."""
        if old_domain == new_domain and old_ip == new_ip:
            return True  # nothing to change
        if not self.add_rewrite(new_domain, new_ip):
            return False
        if not self.delete_rewrite(old_domain, old_ip):
            return True  # new record created; old delete failed (logged by caller)
        return True


class ProxyProvider(ABC):
    """Common interface for all reverse proxy providers (NPM, Traefik, etc.)."""

    # True when a host's `id` is its hostname (Zoraxy, Cloudflare Tunnel), so a rename
    # changes the id. NPM ids are numbers that survive a rename.
    HOST_ID_IS_HOSTNAME = False

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_hosts(self) -> list[dict]:
        """Return every proxy host. Same contract as DNSProvider.list_rewrites: [] means
        none, an incomplete listing raises.
        """

    @abstractmethod
    def create_host(self, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> dict | None:
        """Create a proxy host. Returns the created host info or None."""

    def update_host(self, host_id, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> bool:
        """Best-effort default update strategy for providers without native update API."""
        del host_id
        created = self.create_host(domain, ip, port, scheme, websocket, cert_id)
        return bool(created)

    def toggle_host(self, host_id: int, enabled: bool) -> bool:
        """Enable or disable a proxy host (optional, some providers may not support)."""
        return False  # Default: not supported

    @abstractmethod
    def delete_host(self, host_id: int | str) -> bool:
        """Delete a proxy host by this provider's own identifier (number, hostname or
        router name). Return False for an identifier of the wrong shape.
        """

    @abstractmethod
    def get_certificates(self) -> list[dict]:
        """List available certificates."""

    @abstractmethod
    def find_best_certificate(self, host: str) -> int | None:
        """A certificate that covers `host`, the service's full name (not its zone), or None."""


def supports_suspension(proxy) -> bool:
    """Whether this proxy can suspend a host instead of deleting it.

    True when the class overrides ProxyProvider.toggle_host (NPM, Zoraxy). This tells an
    unsupported toggle from a refused one, which both return False.
    """
    override = getattr(type(proxy), "toggle_host", None)
    return override is not None and override is not ProxyProvider.toggle_host
