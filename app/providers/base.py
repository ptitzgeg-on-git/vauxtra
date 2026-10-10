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

# No provider lives on a link-local address; cloud metadata services do, and every call a
# provider makes carries its stored credentials. Private LAN ranges stay allowed: a homelab
# DNS server or proxy is private by design.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),      # IPv4 link-local, AWS/GCP/Azure metadata
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("fd00:ec2::254/128"),   # AWS metadata over IPv6
    ipaddress.ip_network("fd20:ce::254/128"),    # GCP metadata over IPv6 (N13)
    ipaddress.ip_network("168.63.129.16/32"),    # Azure WireServer (host agent, not link-local)
)
# NAT64 (RFC 6052 well-known prefix): `64:ff9b::a9fe:a9fe` reaches 169.254.169.254 through
# the translator, so the IPv4 address it embeds is checked too.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
# H1: the local-use NAT64 prefix (RFC 8215) embeds the IPv4 address the /48 way of
# RFC 6052: two octets in bits 48-63, then the `u` octet (bits 64-71), then two more.
_NAT64_LOCAL_PREFIX = ipaddress.ip_network("64:ff9b:1::/48")
# Deprecated IPv4-compatible form `::a.b.c.d` (RFC 4291 2.5.5.1). `::` and `::1` are not.
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})


def embedded_ipv4_addresses(ip) -> list:
    """The IPv4 addresses an IPv6 address carries and may be routed to, in every form.

    IPv4-mapped, NAT64 (well-known /96 and local-use /48), IPv4-compatible, 6to4 and the
    Teredo client. Each of them can deliver a packet to the IPv4 host it embeds, so a check
    on the IPv6 address alone let `::7f00:1` or `64:ff9b::a00:1` through.
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
    `169.254.43518` and never touches DNS.
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
    """True for a link-local or cloud metadata address, IPv4-mapped forms included."""
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


def validate_provider_url(url: str, *, resolve: bool = False) -> str:
    """Check a provider URL before it is stored; return it stripped, or raise ValueError.

    Refuses anything that is not http(s), any host that is (or is spelled as) a link-local
    or cloud metadata address, and the metadata service names. With `resolve=True` the
    name is also looked up and every address it resolves to is checked; a name that does
    not resolve is accepted, since it may only resolve from the server's own network.
    An empty URL is returned as is: it stands for the provider type's default endpoint.

    `TimeoutSession` enforces the same rule again on every connection, so a name that
    passes here and later resolves to a refused address still never receives a request.
    """
    val = (url or "").strip()
    if not val:
        return val
    if not val.lower().startswith(("http://", "https://")) or len(val) >= 512:
        raise ValueError("Invalid URL (must start with http:// or https://)")
    try:
        parts = urlsplit(val)
        host = parts.hostname or ""
    except ValueError as e:
        raise ValueError("Invalid URL") from e
    # N12: the providers append fixed API paths to this URL. A `?` or `#` turned that path
    # into a query string or a fragment, so the request reached whatever path the URL named
    # on the host; credentials in the URL rode along on every call.
    if parts.query or parts.fragment or "?" in val or "#" in val or "@" in parts.netloc:
        raise ValueError("Invalid URL (no query string, fragment or credentials allowed)")
    if is_blocked_host(host):
        raise ValueError(
            "Link-local and cloud metadata addresses (169.254.0.0/16, fe80::/10) are not allowed"
        )
    if resolve and host:
        for address in _resolve(host):
            if is_blocked_address(address):
                raise ValueError(
                    "This host resolves to a link-local or cloud metadata address, "
                    "which is not allowed"
                )
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


def record_value_key(value) -> str:
    """A DNS record value in the form two spellings of the same answer share.

    M14: `2001:DB8::1` and `2001:db8:0::1` are one address, `Target.Example.com.` and
    `target.example.com` one name. Compared literally, deleting one spelling of a record
    stored under another reported success while the record stayed published.
    """
    text = str(value or "").strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text.rstrip(".").lower()


def _origin(url: str) -> tuple[str, str, int | None]:
    """Scheme, host and effective port of a URL (the default port when none is written)."""
    parts = urlsplit(url or "")
    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
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
        """Refuse link-local and metadata destinations; keep every failure a RequestException.

        The host is checked as written here, and the address really connected to is checked
        again on the socket (`_CheckedAdapter`). A CA bundle path that no longer exists made
        `requests` raise a bare OSError, which no provider catches: it is turned into a
        `requests.ConnectionError`, like any other unreachable upstream.
        """
        host = urlsplit(request.url or "").hostname or ""
        if is_blocked_host(host):
            raise BlockedDestinationError(
                f"Refused request to {host}: link-local or cloud metadata address",
                request=request,
            )
        # B3: through HTTP(S)_PROXY the socket reaches the proxy, so the peer check of
        # `_CheckedAdapter` sees the proxy's address and the name is resolved by the
        # proxy. The name is resolved here instead; a refused answer stops the request.
        # (The proxy resolves it again on its own: this narrows the gap, a proxy that
        # refuses link-local destinations itself closes it.)
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
        except requests.RequestException:
            raise
        except OSError as e:
            raise requests.ConnectionError(
                f"Could not connect to {host}: {e}", request=request
            ) from e

    def get_redirect_target(self, resp):
        """Follow a redirect only when it stays on the provider's own origin.

        `requests` strips `Authorization` on a cross-host redirect and nothing else: a 307 or
        308 replays the body, so the login passwords Pi-hole and Technitium send in it went
        to whatever host the redirect named. A same-host redirect to plain http or to another
        port leaked the `X-API-Key` and session headers just the same. Only the identical
        origin is followed, plus the http -> https upgrade of the same host on default ports;
        any other 3xx is returned as is.
        """
        target = super().get_redirect_target(resp)
        if not target:
            return target
        source = _origin(resp.request.url)
        destination = _origin(urljoin(resp.request.url, target))
        if destination == source:
            return target
        if (
            source[1] == destination[1]
            and source[0] == "http" and source[2] == 80
            and destination[0] == "https" and destination[2] == 443
        ):
            return target
        return None


class DNSProvider(ABC):
    """Common interface for all DNS providers (AdGuard, Pi-hole, etc.)."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Test whether the provider is reachable and credentials are valid."""

    @abstractmethod
    def list_rewrites(self) -> list[dict] | None:
        """List all DNS rewrites. Returns [{'domain': ..., 'ip': ...}].

        None means the list could not be read, which callers must not take for "empty".
        """

    def lookup_rewrites(self, name: str) -> list[dict] | None:
        """The records of one name, or None when they could not be read.

        The push only needs the records of the name it writes. Providers that keep records
        in zones override this to read that zone alone, so that one zone the account cannot
        read no longer blocks every push. The default filters the full list.
        """
        rewrites = self.list_rewrites()
        if rewrites is None:
            return None
        wanted = (name or "").strip().rstrip(".").lower()
        return [
            r for r in rewrites
            if str(r.get("domain") or "").strip().rstrip(".").lower() == wanted
        ]

    @abstractmethod
    def add_rewrite(self, domain: str, ip: str) -> bool:
        """Add a DNS rewrite."""

    @abstractmethod
    def delete_rewrite(self, domain: str, ip: str) -> bool:
        """Delete a DNS rewrite."""

    def update_rewrite(self, old_domain: str, old_ip: str, new_domain: str, new_ip: str) -> bool:
        """Update a rewrite (create new first, then delete old to avoid data loss)."""
        if record_value_key(old_domain) == record_value_key(new_domain) \
                and record_value_key(old_ip) == record_value_key(new_ip):
            # Nothing to change. Compared literally, two spellings of one address went on to
            # add (a no-op) then delete -- the delete matching the only record (M14).
            return True
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
    def list_hosts(self) -> list[dict] | None:
        """List all proxy hosts.

        None means the list could not be read, which callers must not take for "empty".
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
