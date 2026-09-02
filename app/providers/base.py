"""Abstract base classes for DNS and Reverse Proxy providers."""

from abc import ABC, abstractmethod

import requests

from app.config import PROVIDER_TIMEOUT


class TimeoutSession(requests.Session):
    """A `requests.Session` that carries a default timeout on every call.

    `requests` reads the timeout from the call arguments, never from the session:
    assigning `session.timeout` sets an attribute nobody looks at, and the request
    goes out with no read timeout at all. A provider that accepts the connection
    then stops answering therefore blocks its caller forever -- and the scheduler
    runs checks in a single thread, so one frozen provider silently stops all
    monitoring. Passing `timeout=` explicitly still wins over this default.
    """

    def __init__(self, timeout: float = PROVIDER_TIMEOUT):
        super().__init__()
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().request(method, url, **kwargs)


class DNSProvider(ABC):
    """Common interface for all DNS providers (AdGuard, Pi-hole, etc.)."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_rewrites(self) -> list[dict]:
        """List all DNS rewrites. Returns [{'domain': ..., 'ip': ...}]."""

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

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_hosts(self) -> list[dict]:
        """List all proxy hosts."""

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
    def delete_host(self, host_id: int) -> bool:
        """Delete a proxy host by its ID."""

    @abstractmethod
    def get_certificates(self) -> list[dict]:
        """List available certificates."""

    @abstractmethod
    def find_best_certificate(self, domain_suffix: str) -> int | None:
        """Find the most suitable wildcard certificate for the domain."""
