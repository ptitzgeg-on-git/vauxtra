"""Helpers to suggest and resolve public DNS targets for exposed services."""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.validators import is_valid_hostname

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_IP_SOURCES = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
]

DEFAULT_PUBLIC_TARGET_TIMEOUT = 2.0
DEFAULT_PUBLIC_TARGET_PRIORITY = ["server_public_ip", "proxy_provider_host", "current"]
PUBLIC_TARGET_PRIORITY_CHOICES = {"server_public_ip", "proxy_provider_host", "current"}

# Why `resolve_public_target` came back empty, as the `source` it reports. `manual` means it
# was never asked to look anything up; `auto_unavailable` means it looked and found nothing.
PUBLIC_TARGET_UNRESOLVED_SOURCES = {"manual", "auto_unavailable"}


def _normalize_target(value: str) -> str:
    return (value or "").strip().lower()


def _extract_host_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        return _normalize_target(parsed.hostname or "")
    except Exception:
        return ""


def _provider_host_candidate(conn, provider_id: int | None) -> str:
    if not provider_id:
        return ""
    row = conn.execute("SELECT url FROM providers WHERE id=?", (int(provider_id),)).fetchone()
    if not row:
        return ""
    host = _extract_host_from_url(row["url"])
    return host if host and is_valid_hostname(host) else ""


def _parse_sources(raw: str) -> list[str]:
    items: list[str] = []
    text = (raw or "").replace(",", "\n")
    for line in text.splitlines():
        source = line.strip()
        if source and source.startswith(("http://", "https://")):
            items.append(source)
    return items


def _parse_priority(raw: str) -> list[str]:
    """The sources allowed to answer, in the operator's order. Omitting one excludes it.

    Every value the operator left out used to be appended back at the end, so the field
    could reorder the three sources but never drop one. What it names is the address
    written into public DNS for every service left in `auto` mode: an operator who takes
    `server_public_ip` out is saying this machine's WAN address must not be published, and
    saw it published anyway, under a form that had answered "Saved".

    An empty or unrecognisable setting is still the full default policy -- that is a field
    nobody has filled in, not a request for no sources at all.
    """
    parts = [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]
    ordered = [p for p in parts if p in PUBLIC_TARGET_PRIORITY_CHOICES]
    if not ordered:
        return list(DEFAULT_PUBLIC_TARGET_PRIORITY)
    unique: list[str] = []
    for item in ordered:
        if item not in unique:
            unique.append(item)
    return unique


def load_public_target_policy(conn) -> dict:
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('public_target_sources', 'public_target_timeout', 'public_target_priority')"
    ).fetchall()
    kv = {r["key"]: (r["value"] or "") for r in rows}

    sources = _parse_sources(kv.get("public_target_sources", ""))
    if not sources:
        sources = list(DEFAULT_PUBLIC_IP_SOURCES)

    timeout = DEFAULT_PUBLIC_TARGET_TIMEOUT
    raw_timeout = (kv.get("public_target_timeout", "") or "").strip()
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except Exception:
            timeout = DEFAULT_PUBLIC_TARGET_TIMEOUT
    timeout = max(0.5, min(timeout, 10.0))

    priority = _parse_priority(kv.get("public_target_priority", ""))

    return {
        "sources": sources,
        "timeout_seconds": timeout,
        "priority": priority,
    }


def _is_publicly_routable(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether this is an address the outside world could route back to us."""
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def detect_server_public_ip(
    sources: list[str] | None = None,
    timeout_seconds: float = 2.0,
) -> str:
    """Return the first detected WAN IP from configured resolvers, else empty string."""
    for source in (sources or DEFAULT_PUBLIC_IP_SOURCES):
        try:
            req = Request(source, headers={"User-Agent": "Vauxtra/1.0"})
            with urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read(96).decode("utf-8", "ignore").strip()
            candidate = raw.split()[0].strip()
            ip = ipaddress.ip_address(candidate)
        except Exception:
            continue
        if not _is_publicly_routable(ip):
            # Whatever comes back here is written into public DNS for every service left in
            # `public_target_mode='auto'`. `127.0.0.1` used to be accepted verbatim -- from
            # a captive portal answering for the resolver, from a source chosen badly, from
            # a source that was compromised -- and every name on this instance would then
            # resolve to the visitor's own machine. Named out loud rather than skipped in
            # silence: a resolver that answers with a LAN address is broken, not empty.
            logger.warning(
                "Ignoring %s from WAN resolver %s: not a publicly routable address",
                ip,
                source,
            )
            continue
        return str(ip)
    return ""


def suggest_public_targets(
    conn,
    proxy_provider_id: int | None = None,
    current_value: str = "",
    server_public_ip: str | None = None,
) -> dict:
    """Return candidate targets and the recommended value."""
    policy = load_public_target_policy(conn)

    candidates: list[dict] = []
    seen: set[str] = set()

    def add_candidate(value: str, source: str) -> None:
        candidate = _normalize_target(value)
        if not candidate or candidate in seen:
            return
        if not is_valid_hostname(candidate):
            return
        seen.add(candidate)
        candidates.append({"value": candidate, "source": source})

    if current_value:
        add_candidate(current_value, "current")

    proxy_host = _provider_host_candidate(conn, proxy_provider_id)
    if proxy_host:
        add_candidate(proxy_host, "proxy_provider_host")

    wan_ip = _normalize_target(server_public_ip) if server_public_ip is not None else ""
    if not wan_ip:
        wan_ip = _normalize_target(
            detect_server_public_ip(
                sources=policy["sources"],
                timeout_seconds=policy["timeout_seconds"],
            )
        )
    if wan_ip:
        add_candidate(wan_ip, "server_public_ip")

    recommended = ""
    for source in policy["priority"]:
        hit = next((c["value"] for c in candidates if c["source"] == source), "")
        if hit:
            recommended = hit
            break

    return {
        "candidates": candidates,
        "recommended": recommended,
        "policy": policy,
    }


def resolve_public_target(
    conn,
    mode: str,
    manual_value: str,
    proxy_provider_id: int | None = None,
    current_value: str = "",
    server_public_ip: str | None = None,
) -> tuple[str, str]:
    """Resolve the effective public target and return (value, source)."""
    normalized_mode = _normalize_target(mode) or "manual"
    manual = _normalize_target(manual_value)

    if manual and is_valid_hostname(manual):
        return manual, "manual"

    if normalized_mode != "auto":
        return "", "manual"

    result = suggest_public_targets(
        conn,
        proxy_provider_id=proxy_provider_id,
        current_value=current_value,
        server_public_ip=server_public_ip,
    )
    value = _normalize_target(result.get("recommended", ""))
    if not value:
        return "", "auto_unavailable"

    source = "auto"
    for candidate in result.get("candidates", []):
        if _normalize_target(candidate.get("value", "")) == value:
            source = candidate.get("source") or "auto"
            break

    return value, source


def describe_public_target_failure(source: str, provider_name: str = "") -> tuple[str, str]:
    """Why no public target is available, as `(detail_key, sentence)`.

    `resolve_public_target` already separates the two ways of coming back empty, and every
    caller used to throw that apart and print one sentence for both: "Unable to resolve DNS
    public target". Only one of the two resolves anything. In manual mode the function
    returns on its first branch without a single lookup, so an operator who simply left the
    field blank was told a resolution had failed, and went reading firewall logs over an
    empty text box. The two cases have different remedies, so they get different sentences.
    """
    who = f' for "{provider_name}"' if provider_name else ""
    if _normalize_target(source) == "auto_unavailable":
        return (
            "dns_target_detection_failed",
            f"Automatic detection of the public DNS target{who} found nothing usable. "
            "Check the WAN resolvers in Settings, or set the target by hand.",
        )
    return (
        "dns_target_required",
        f"A public DNS target is required{who}. "
        "Enter an address, or turn on automatic detection.",
    )
