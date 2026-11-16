"""One probe for every route, whatever exposes it.

There used to be four: the scheduler, the per-row button, "check all" and the preflight each
opened their own socket with their own timeout and their own idea of which services to skip.
The same route could read "up" in one place and "down" in the next, and a tunnel route read
"down" everywhere it was probed at all.

That last one is the reason this module exists. A tunnel route's target is written from the
point of view of `cloudflared` -- `localhost:8080` means the host the connector runs on, not
the container Vauxtra runs in. Dialling it from here measures nothing about the route. What a
visitor sees is the Cloudflare edge, so that is what a tunnel route is probed through:

1. its public name has to resolve to Cloudflare, or no visitor reaches the tunnel at all;
2. one HTTPS request to that name, redirects not followed, classified by what the edge
   answers: 530 is the edge saying no connector holds the tunnel, 502/504 is the connector
   failing to reach the origin, a redirect to `*.cloudflareaccess.com` is Access standing in
   front (the route works, the origin is simply not visible to an anonymous probe).

Every result carries a short `detail` code the panel translates, so "error" always says which
of these it was rather than the one generic "check the tunnel connector" it used to print for
routes that were fine.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

TCP_TIMEOUT_SECONDS = 3.0
EDGE_TIMEOUT_SECONDS = 6.0
PROBE_WORKERS = 8
USER_AGENT = "Vauxtra-probe/1"

# Published at https://www.cloudflare.com/ips/ and stable for years. Kept inline rather than
# fetched: a probe that needs the network to decide what the network answered would fail in
# exactly the outage it is there to describe.
CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
        "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
    )
)

# Cloudflare's own resolver, asked only when the local one answers with something that is not
# Cloudflare. A split-horizon DNS (an AdGuard rewrite of `*.example.com` to a LAN address) is
# common in exactly the setups that run a tunnel, and it must not turn every tunnel route red.
# The name is a Cloudflare-proxied name already, so this tells Cloudflare nothing it lacks.
PUBLIC_RESOLVERS = ("1.1.1.1", "1.0.0.1")


@dataclass(frozen=True)
class ProbeResult:
    status: str               # "ok" | "error" | "unknown"
    detail: str = ""          # short code, translated by the panel (see DETAIL_CODES)
    latency_ms: float | None = None
    tested: bool = True       # False: nothing could be probed, history is left alone
    http_status: int | None = None

    @property
    def stored_detail(self) -> str:
        """What goes in `services.status_detail`: the code, and the HTTP status if any."""
        if self.http_status is None:
            return self.detail
        return f"{self.detail}:{self.http_status}"


# Every code a result can carry, so the panel's translations can be checked against one list.
DETAIL_CODES = frozenset({
    "tcp_open",             # the target accepted a TCP connection
    "tcp_failed",           # refused, unreachable or timed out
    "no_port",              # a name in DNS and nothing more: nothing to connect to
    "edge_ok",              # the edge answered and the origin behind it did too
    "edge_access",          # Access is in front: the route answers, the origin is not visible
    "edge_tunnel_down",     # 530: the edge has no connector for this tunnel
    "edge_origin_down",     # 502/504: the connector could not reach the origin
    "edge_origin_error",    # any other 5xx, from the origin itself
    "edge_unreachable",     # no TLS session with the edge at all
    "dns_unresolved",       # the public name does not resolve
    "dns_not_cloudflare",   # it resolves, but not to Cloudflare: visitors never reach the tunnel
    "no_hostname",          # a tunnel route with no public name to probe
})


def is_cloudflare_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return any(addr in net for net in CLOUDFLARE_NETWORKS if net.version == addr.version)


def tcp_probe(ip: str, port: int, timeout: float = TCP_TIMEOUT_SECONDS) -> ProbeResult:
    if not int(port or 0):
        return ProbeResult("unknown", "no_port", tested=False)
    start = time.monotonic()
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return ProbeResult("ok", "tcp_open", round((time.monotonic() - start) * 1000, 1))
    except (OSError, ValueError):
        return ProbeResult("error", "tcp_failed")


def _resolve_local(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return sorted({info[4][0] for info in infos})


def _resolve_public(hostname: str) -> list[str]:
    """Ask Cloudflare's resolver directly, for the split-horizon case only."""
    try:
        import dns.resolver  # dnspython, already a dependency
    except ImportError:
        return []
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = list(PUBLIC_RESOLVERS)
    resolver.lifetime = 3.0
    found: list[str] = []
    for rdtype in ("A", "AAAA"):
        try:
            found.extend(r.to_text() for r in resolver.resolve(hostname, rdtype))
        except Exception:  # noqa: BLE001 - NXDOMAIN, no answer and timeout all mean "none"
            continue
    return sorted(set(found))


def _edge_addresses(hostname: str) -> tuple[list[str], str]:
    """The Cloudflare addresses to dial, or the reason there are none."""
    local = _resolve_local(hostname)
    edge = [a for a in local if is_cloudflare_ip(a)]
    if edge:
        return edge, ""
    public = _resolve_public(hostname)
    edge = [a for a in public if is_cloudflare_ip(a)]
    if edge:
        return edge, ""
    if local or public:
        return [], "dns_not_cloudflare"
    return [], "dns_unresolved"


def _https_head_of(address: str, hostname: str, timeout: float) -> tuple[int, str]:
    """One GET to `https://hostname/` dialled at `address`. Returns (status, Location).

    The address is pinned so the split-horizon answer above is the one used, and the body is
    never read: the status line and one header are the whole measurement.
    """
    raw = socket.create_connection((address, 443), timeout=timeout)
    try:
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname=hostname)
    except Exception:
        raw.close()
        raise
    conn = http.client.HTTPConnection(hostname, 443, timeout=timeout)
    conn.sock = tls
    try:
        conn.request("GET", "/", headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location") or ""
    finally:
        conn.close()


def _is_access_redirect(location: str, host: str) -> bool:
    """Whether `location`, answered for `host`, sends the visitor to a Cloudflare Access login.

    Read the way a browser reads it: resolved against the page that answered, then judged on
    its host and its path alone. A substring test passed an application's own
    `/login?next=/cdn-cgi/access/`, and a foreign host carrying that text in its query, and the
    Access panel then showed an open route as already protected.

    The path is taken as written, so it is refused when a browser would not land where it
    reads: `urljoin` resolves the dot segments of a relative reference only, and a
    percent-encoded one (`%2e%2e`) is decoded after the prefix was checked. Any team domain
    is accepted, since the one of the account is not known here.
    """
    host = (host or "").strip().strip(".").lower()
    try:
        # A browser takes a backslash for a slash in an https URL; urlparse does not.
        target = urlparse(urljoin(f"https://{host}/", (location or "").strip().replace("\\", "/")))
        found = (target.hostname or "").rstrip(".")
    except ValueError:
        # `https://[broken/` raises here, and the probe has no reason to crash on it.
        return False
    if found.endswith(".cloudflareaccess.com"):
        return True
    path = target.path
    if "%" in path or any(segment in (".", "..") for segment in path.split("/")):
        return False
    return bool(host) and found == host and path.startswith("/cdn-cgi/access/")


def classify_edge_answer(status: int, location: str = "", host: str = "") -> tuple[str, str]:
    """(status, detail) for what the edge answered for `host`. Pure, so it is tested alone."""
    if status in (301, 302, 303, 307, 308) and _is_access_redirect(location, host):
        return "ok", "edge_access"
    if status == 530:
        return "error", "edge_tunnel_down"
    if status in (502, 504):
        return "error", "edge_origin_down"
    if status >= 500:
        return "error", "edge_origin_error"
    return "ok", "edge_ok"


def edge_probe(hostname: str, timeout: float = EDGE_TIMEOUT_SECONDS) -> ProbeResult:
    host = (hostname or "").strip().strip(".").lower()
    if not host:
        return ProbeResult("unknown", "no_hostname", tested=False)
    addresses, reason = _edge_addresses(host)
    if not addresses:
        return ProbeResult("error", reason)
    # IPv4 first: a container without IPv6 egress would otherwise burn the whole timeout.
    addresses.sort(key=lambda a: ":" in a)
    start = time.monotonic()
    for address in addresses[:2]:
        try:
            code, location = _https_head_of(address, host, timeout)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            continue
        status, detail = classify_edge_answer(code, location, host)
        latency = round((time.monotonic() - start) * 1000, 1)
        return ProbeResult(status, detail, latency, http_status=code)
    return ProbeResult("error", "edge_unreachable")


def is_tunnel(row: Any) -> bool:
    return (_get(row, "expose_mode") or "").strip().lower() == "tunnel"


def public_hostname(row: Any) -> str:
    if is_tunnel(row):
        host = (_get(row, "tunnel_hostname") or "").strip().lower()
        if host:
            return host
    return f"{_get(row, 'subdomain') or ''}.{_get(row, 'domain') or ''}".strip(".").lower()


def probe_service(
    row: Any,
    *,
    tcp: Callable[[str, int], ProbeResult] | None = None,
    edge: Callable[[str], ProbeResult] | None = None,
) -> ProbeResult:
    """The one probe. A tunnel route goes through the edge, anything else through TCP.

    The two probes are looked up when called, not bound as defaults, so a test patching
    `app.probe.edge_probe` reaches every caller, the API routes included.
    """
    if is_tunnel(row):
        return (edge or edge_probe)(public_hostname(row))
    return (tcp or tcp_probe)(_get(row, "target_ip") or "", int(_get(row, "target_port") or 0))


def probe_many(
    rows: Sequence[Any],
    probe: Callable[[Any], ProbeResult] = probe_service,
    workers: int = PROBE_WORKERS,
) -> list[tuple[Any, ProbeResult]]:
    """Probe in parallel, keep the input order. Forty routes no longer cost forty timeouts."""
    rows = list(rows)
    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(rows)))) as pool:
        results = list(pool.map(probe, rows))
    return list(zip(rows, results, strict=True))


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
