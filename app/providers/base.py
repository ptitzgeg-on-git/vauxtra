"""Abstract base classes for DNS and Reverse Proxy providers."""

import ipaddress
import socket
from abc import ABC, abstractmethod
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import MaxRetryError, NewConnectionError

from app.config import PROVIDER_TIMEOUT
from app.security import redact_query_secrets

_DEFAULT_PORTS = {"http": 80, "https": 443}

# No provider lives on a link-local address; cloud metadata services do, and every call a
# provider makes carries its stored credentials. Private LAN ranges stay allowed: a homelab
# DNS server or proxy is private by design.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),      # IPv4 link-local, AWS/GCP/Azure/OCI metadata
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("fd00:ec2::254/128"),   # AWS metadata over IPv6
    ipaddress.ip_network("fd20:ce::254/128"),    # GCP metadata over IPv6
    ipaddress.ip_network("168.63.129.16/32"),    # Azure WireServer (host agent, not link-local)
)
# NAT64 (RFC 6052 well-known prefix): `64:ff9b::a9fe:a9fe` reaches 169.254.169.254 through
# the translator, so the IPv4 address it embeds is checked too.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
# The local-use NAT64 prefix (RFC 8215) embeds the IPv4 address the /48 way of RFC 6052:
# two octets in bits 48-63, then the `u` octet (bits 64-71), then two more.
_NAT64_LOCAL_PREFIX = ipaddress.ip_network("64:ff9b:1::/48")
# Deprecated IPv4-compatible form `::a.b.c.d` (RFC 4291 2.5.5.1). `::` and `::1` are not.
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})
_BLOCKED_MESSAGE = (
    "Link-local and cloud metadata addresses (169.254.0.0/16, fe80::/10) are not allowed"
)


def embedded_ipv4_addresses(ip) -> list:
    """The IPv4 addresses an IPv6 address carries and may be routed to, in every form.

    IPv4-mapped, NAT64 (well-known /96 and local-use /48), IPv4-compatible, 6to4 and the
    Teredo client. Each of them can deliver a packet to the IPv4 host it embeds, so a check
    on the IPv6 address alone would let `::ffff:169.254.169.254` through.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return []
    found = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    value = int(ip)
    if ip in _NAT64_PREFIX:
        found.append(ipaddress.IPv4Address(value & 0xFFFFFFFF))
    if ip in _NAT64_LOCAL_PREFIX:
        high = (value >> 64) & 0xFFFF          # bits 48-63
        low = (value >> 40) & 0xFFFF           # bits 72-87, after the `u` octet
        found.append(ipaddress.IPv4Address((high << 16) | low))
    if ip in _IPV4_COMPATIBLE and value > 1:
        found.append(ipaddress.IPv4Address(value & 0xFFFFFFFF))
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.append(ip.teredo[1])
    return found


def _parse_address(host: str):
    """The IP address `host` spells, in any form the HTTP stack would connect to, or None.

    `inet_aton` is used for IPv4 because it accepts `2852039166`, `0xa9.0xfe.0xa9.0xfe` or
    `169.254.43518`, which the OS resolver connects to just the same, and never touches DNS.
    """
    host = (host or "").strip().strip("[]")
    if "%" in host:
        host = host.split("%", 1)[0]  # IPv6 zone index (`fe80::1%eth0`)
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def is_blocked_address(address) -> bool:
    """True for a link-local or cloud metadata address, embedded IPv4 forms included."""
    if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        ip = address
    else:
        ip = _parse_address(str(address))
    if ip is None:
        return False
    candidates = [ip, *embedded_ipv4_addresses(ip)]
    return any(
        c.version == net.version and c in net for c in candidates for net in _BLOCKED_NETWORKS
    )


def is_blocked_host(host: str) -> bool:
    """True when the host, as written, names a refused destination (no DNS lookup)."""
    name = (host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    if name in _BLOCKED_HOSTNAMES:
        return True
    ip = _parse_address(name)
    return ip is not None and is_blocked_address(ip)


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return []
    return [str(info[4][0]) for info in infos]


def validate_provider_url(url: str) -> str:
    """Refuse a provider URL whose host is, or is spelt as, a link-local or metadata address.

    Returns the URL stripped, or raises ValueError. No DNS lookup here: a name that only
    resolves from the server's own network is fine to store. `TimeoutSession` checks the
    address really connected to, so a name that later resolves to a refused address still
    never receives a request.
    """
    val = (url or "").strip()
    try:
        host = urlsplit(val).hostname or ""
    except ValueError as e:
        raise ValueError("Invalid URL") from e
    if is_blocked_host(host):
        raise ValueError(_BLOCKED_MESSAGE)
    return val


class BlockedDestinationError(requests.ConnectionError):
    """A provider request aimed at a link-local or cloud metadata address was refused.

    A `requests.ConnectionError`, so every provider's `except requests.RequestException`
    reports it as an unreachable provider instead of letting it escape.
    """


class _RefusedPeerError(NewConnectionError):
    """Raised inside urllib3 when the socket turned out to reach a refused address."""


def _check_peer(conn, sock) -> None:
    try:
        peer = sock.getpeername()[0]
    except (OSError, IndexError, TypeError):
        return
    if is_blocked_address(peer):
        try:
            sock.close()
        finally:
            raise _RefusedPeerError(
                conn, f"Refused connection to {conn.host}: link-local or metadata address"
            )


class _CheckedHTTPConnection(HTTPConnection):
    # Checked on the connected socket rather than on a lookup made beforehand: a name that
    # resolves to a LAN address once and to 169.254.169.254 the next time is still caught.
    def _new_conn(self):
        sock = super()._new_conn()
        _check_peer(self, sock)
        return sock


class _CheckedHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        sock = super()._new_conn()
        _check_peer(self, sock)
        return sock


class _CheckedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _CheckedHTTPConnection


class _CheckedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _CheckedHTTPSConnection


class _CheckedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _CheckedHTTPConnectionPool,
            "https": _CheckedHTTPSConnectionPool,
        }


def url_origin(url: str) -> tuple[str, str, int | None]:
    """Scheme, host and effective port of a URL, the default port when none is written.

    This triple is where a provider's credential goes: every call it makes carries it. The
    path is left out on purpose, it is the same server either way, and `http://npm` and
    `http://npm:80` compare equal. A port that does not parse reads as -1, which matches
    no other URL.
    """
    parts = urlsplit(url or "")
    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    return scheme, (parts.hostname or "").lower(), port


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
        self.mount("http://", _CheckedAdapter())
        self.mount("https://", _CheckedAdapter())

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().request(method, url, **kwargs)

    def send(self, request, **kwargs):
        """Refuse link-local and cloud metadata destinations, as written and as connected.

        The host is checked as written here, and the address really connected to is checked
        again on the socket (`_CheckedAdapter`). Through an HTTP(S) proxy the socket reaches
        the proxy instead, so the name is resolved here and a refused answer stops the
        request; the proxy resolves it again on its own, so this narrows the gap, and a
        proxy that refuses link-local destinations itself closes it.
        """
        host = urlsplit(request.url or "").hostname or ""
        if is_blocked_host(host):
            raise BlockedDestinationError(
                f"Refused request to {host}: link-local or cloud metadata address",
                request=request,
            )
        through_proxy = (
            host
            and _parse_address(host) is None
            and select_proxy(request.url, kwargs.get("proxies") or {})
        )
        if through_proxy and any(is_blocked_address(address) for address in _resolve(host)):
            raise BlockedDestinationError(
                f"Refused request to {host}: it resolves to a link-local or cloud "
                "metadata address",
                request=request,
            )
        try:
            return super().send(request, **kwargs)
        except requests.ConnectionError as e:
            cause = e.args[0] if e.args else None
            if isinstance(cause, MaxRetryError) and isinstance(cause.reason, _RefusedPeerError):
                raise BlockedDestinationError(
                    f"Refused request to {host}: it resolves to a link-local or cloud "
                    "metadata address",
                    request=request,
                ) from e
            raise

    def get_redirect_target(self, resp):
        """Follow a redirect only when it stays on the provider's own origin.

        `requests` strips `Authorization` on a cross-host redirect and nothing else: a 307
        or 308 replays the body, so the login passwords Pi-hole and Technitium send in it
        went to whatever host the redirect named. A same-host redirect to plain http or to
        another port leaked the `X-API-Key` and session headers just the same. Only the
        identical origin is followed, plus the http to https upgrade of the same host on
        default ports; any other 3xx is handed back as is, for the caller to read as the
        failure it is.
        """
        target = super().get_redirect_target(resp)
        if not target:
            return target
        source = url_origin(resp.request.url)
        destination = url_origin(urljoin(resp.request.url, target))
        if destination == source:
            return target
        if (
            source[1] == destination[1]
            and source[0] == "http" and source[2] == 80
            and destination[0] == "https" and destination[2] == 443
        ):
            return target
        return None

    # `requests` drops `Authorization` when a redirect leaves the host, and nothing else.
    # These carry the same credential for PowerDNS and Pi-hole v6. `get_redirect_target`
    # already stops such a redirect; this keeps the headers off it if that ever changes.
    _CREDENTIAL_HEADERS = ("X-API-Key", "X-FTL-SID", "X-FTL-CSRF")

    def rebuild_auth(self, prepared_request, response):
        super().rebuild_auth(prepared_request, response)
        if self.should_strip_auth(response.request.url, prepared_request.url):
            for name in self._CREDENTIAL_HEADERS:
                prepared_request.headers.pop(name, None)


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
    def find_best_certificate(self, host: str) -> int | None:
        """A certificate that covers `host`, the service's full name (not its zone), or None."""


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
