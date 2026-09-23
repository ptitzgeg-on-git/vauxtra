"""Abstract base classes for DNS and Reverse Proxy providers."""

from abc import ABC, abstractmethod

import requests

from app.config import PROVIDER_TIMEOUT
from app.security import redact_query_secrets


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


def reachability_check(session, url: str) -> dict:
    """One diagnostic check saying whether anything at all answered at `url`.

    `test_connection` cannot answer this, and that is the whole point of asking separately:
    it folds "nothing listened on that port" and "the host answered and refused the
    credentials" into one `False`, which the providers panel then labels `connection_failed`.
    An operator who reads that goes looking for a firewall, and the integrations with no
    richer diagnostic of their own sent them there every time a password was simply wrong.

    Any HTTP answer proves the host is reachable, a refusal included, so the status code is
    deliberately not read here: whether the credentials are accepted is the next check's
    business. Only a transport error -- refused connection, DNS failure, timeout -- says the
    host was never reached.
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
    """The provider did not finish saying what it holds.

    An empty list and a refused listing are different answers, and every caller that acts on
    a listing acts on the difference: `push` creates a record when it finds none, the drift
    check reports one missing, the record routes answer 404. `desec._get_all` puts it in its
    own words one layer down -- "one means the account has no records, the other means we do
    not know" -- and `powerdns._zone_rrsets` keeps the same three answers for the same
    reason. This is how that third answer leaves `list_rewrites`, which has only two to give.

    Providers whose own client raises, Cloudflare's for one, let that exception out instead.
    Every caller wraps the call, so what matters is that something arrives.

    The message is masked on the way in (`redact_query_secrets`): most refusals quote the
    `requests` exception they caught, and Technitium and Pi-hole v5 put their credential in
    the URL that exception quotes. What arrives is shown in the scan and the journal.
    """

    def __init__(self, message: object = "") -> None:
        super().__init__(redact_query_secrets(str(message)))


class DNSProvider(ABC):
    """Common interface for all DNS providers (AdGuard, Pi-hole, etc.)."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_rewrites(self) -> list[dict]:
        """Every rewrite the provider holds, as [{'domain': ..., 'answer': ...}].

        An empty list means the provider said it holds nothing. A provider that could not
        finish answering raises -- `ProviderListingRefused`, or whatever its own client
        threw -- rather than handing back the part it managed to collect.
        """

    def records_for(self, domain: str) -> list[dict]:
        """The records held for exactly `domain`, in the shape `list_rewrites` gives them.

        The drift check asks this of every DNS integration a service is not pushed to, to
        find a name that also resolves somewhere else. Filtering the whole listing is the
        answer any provider can give; one whose API can ask for a single name overrides this,
        as Cloudflare does. Raises when the listing does.
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

    # True when the `id` a host is handed back under is its hostname, so that renaming
    # the host renames the identifier. NPM numbers its hosts and the number survives a
    # rename; Zoraxy and Cloudflare Tunnel key their rules on the name, and a caller that
    # keeps the old name after a rename addresses a rule that no longer exists. The
    # service and sync routes read this to know what to store and what to trust.
    HOST_ID_IS_HOSTNAME = False

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_hosts(self) -> list[dict]:
        """Every proxy host the provider holds.

        Same contract as `DNSProvider.list_rewrites`, and for the same reason: an empty
        list means the provider said it holds nothing, while a provider that could not
        finish answering raises rather than handing back the part it collected. It was
        only ever written down on the DNS side, so the one proxy client that answered []
        to a failed request drifted for as long as nothing here said otherwise -- and the
        drift check reads a missing host as a route to republish.
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
        """Delete a proxy host by the identifier *this* provider uses for it.

        NPM numbers its hosts, Cloudflare Tunnel addresses its ingress rules by hostname,
        Traefik by router name. The caller passes through whatever it stored or listed; an
        implementation that needs a particular shape checks it and returns False.
        """

    @abstractmethod
    def get_certificates(self) -> list[dict]:
        """List available certificates."""

    @abstractmethod
    def find_best_certificate(self, domain_suffix: str) -> int | None:
        """Find the most suitable wildcard certificate for the domain."""


def supports_suspension(proxy) -> bool:
    """Whether this proxy can switch a host off instead of deleting it.

    `ProxyProvider.toggle_host` returns False, so a provider that never overrode it can only
    fail the call: there is no suspension to apply, none to lift, and none to plan. Two
    override it, NPM and Zoraxy. Comparing the class's method to the base's is what tells those
    apart from a provider that merely refused one particular host -- which is the same
    `False` on the wire and a completely different thing to tell the operator.

    Read through `getattr`, because a provider is whatever `create_provider` returns and not
    necessarily a subclass: one that does not carry the method at all has no suspension to
    speak of either, and that is the answer to give rather than an `AttributeError` from the
    middle of a push.
    """
    override = getattr(type(proxy), "toggle_host", None)
    return override is not None and override is not ProxyProvider.toggle_host
