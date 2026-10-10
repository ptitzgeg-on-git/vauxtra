"""Where an apprise URL will actually connect, and whether that is inside the network.

Shared by the webhook routes (decide whether `admin` is required to store or test a URL)
and the senders in the scheduler (refuse a send that a DNS answer moved onto the local
network after the URL was stored).

The host is read from the instances apprise builds, not from `urlsplit()`: the two parsers
disagree. `ntfy://a@127.0.0.1@example.com/t` is `example.com` for `urlsplit()` (last `@`)
and `127.0.0.1` for apprise (first `@`), and `mailto://...?smtp=10.0.0.1` connects to an
SMTP host that lives in the query string. Only the instance knows the truth.
"""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

from app.providers.base import (
    _parse_address,
    _resolve,
    embedded_ipv4_addresses,
    is_blocked_address,
    is_blocked_host,
)

_LOCAL_NAME_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa")

# Instance attributes apprise 1.10 connects to. `host` is the common one; e-mail keeps its
# relay in `smtp_host` (`?smtp=`), XMPP in `xmpp_host`, Zulip in `hostname`, and Matrix in
# `home_server` once the well-known lookup has run.
_HOST_ATTRIBUTES = ("host", "smtp_host", "xmpp_host", "hostname", "home_server")


def _is_local_address(addr) -> bool:
    if addr is None:
        return False
    # H1: an IPv6 address that carries an IPv4 one (mapped, NAT64 /96 and /48,
    # IPv4-compatible, 6to4, Teredo) is local when the address it carries is.
    return (
        not addr.is_global
        or is_blocked_address(addr)
        or any(not v4.is_global for v4 in embedded_ipv4_addresses(addr))
    )


def host_is_local(host: str) -> bool:
    """True when `host` is, spells or resolves to a loopback, private or link-local address.

    Names without a dot are resolved too (M10): `gotify://gotify/TOKEN` reaches a Docker
    service by its container name. The host part of `discord://<id>/<token>` and the like
    is resolved as well; it does not resolve, so it stays non-local. A name that does not
    resolve at all is not local: nothing can be sent to it either.
    """
    name = (host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    if name == "localhost" or name.endswith(_LOCAL_NAME_SUFFIXES) or is_blocked_host(name):
        return True
    addr = _parse_address(name)
    if addr is not None:
        return _is_local_address(addr)
    return any(_is_local_address(_parse_address(a)) for a in _resolve(name))


def _authority_is_ambiguous(url: str) -> bool:
    """True when the authority holds more than one `@`, where parsers disagree on the host."""
    rest = url.split("://", 1)[1] if "://" in url else url
    authority = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return authority.count("@") > 1


def instance_hosts(instance) -> list[str]:
    hosts = []
    for attr in _HOST_ATTRIBUTES:
        value = getattr(instance, attr, None)
        if isinstance(value, str) and value.strip():
            hosts.append(value.strip())
    return hosts


def targets_the_local_network(url: str, apprise_obj=None) -> bool:
    """True when the URL makes apprise connect inside the local network.

    Fails closed (B1): a URL that cannot be parsed, or whose authority is ambiguous, counts
    as local, so storing it needs `admin`. `apprise_obj` is an `Apprise()` the URL was
    already added to; without one it is built here.
    """
    value = (url or "").strip()
    if not value:
        return False
    if _authority_is_ambiguous(value):
        return True
    try:
        parts = urlsplit(value)
        raw_host = parts.hostname or ""
    except ValueError:
        return True
    hosts = [raw_host] if raw_host else []
    if apprise_obj is None:
        try:
            import apprise

            apprise_obj = apprise.Apprise()
            if not apprise_obj.add(value):
                apprise_obj = None
        except Exception:
            return True
    if apprise_obj is not None:
        for instance in apprise_obj:
            hosts.extend(instance_hosts(instance))
    seen = set()
    for host in hosts:
        key = host.lower()
        if key in seen:
            continue
        seen.add(key)
        if host_is_local(host):
            return True
    return False


# ── Connection-time guard (H1) ──────────────────────────────────────────────
#
# The checks above run on the URL as stored. They cannot see what happens once apprise
# sends: an HTTP redirect, the Matrix `.well-known` or Bluesky PDS discovery, or a DNS
# answer that changed since (rebinding) all pick a new address after the check. The guard
# below sits on `socket.getaddrinfo`, the call every connection apprise makes goes through
# (requests/urllib3, smtplib, paho-mqtt, slixmpp...), and refuses the answer when any
# address in it is local. The address checked is the address connected to: nothing runs
# between the two. It is per thread, so the provider calls the other threads make to LAN
# DNS servers and proxies are not affected.
#
# A proxy configured through the environment is resolved like any host: a proxy on the LAN
# refuses the send of an unapproved webhook. That is fail closed, by design.

class LocalConnectionRefused(OSError):
    """A connection of an unapproved webhook resolved to a local address."""


_guard_state = threading.local()
_original_getaddrinfo = socket.getaddrinfo


class _Guard:
    __slots__ = ("refused",)

    def __init__(self) -> None:
        self.refused = False


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    infos = _original_getaddrinfo(host, port, *args, **kwargs)
    guards = getattr(_guard_state, "stack", None)
    if not guards:
        return infos
    for info in infos:
        try:
            address = _parse_address(str(info[4][0]))
        except (IndexError, TypeError):
            address = None
        # An answer that is not an IP address cannot be checked, so it is refused too.
        if address is None or _is_local_address(address):
            guards[-1].refused = True
            raise LocalConnectionRefused(
                "Refused: this notification target resolves inside the local network"
            )
    return infos


def _install() -> None:
    if socket.getaddrinfo is not _guarded_getaddrinfo:
        socket.getaddrinfo = _guarded_getaddrinfo


@contextmanager
def refuse_local_connections(active: bool = True, apprise_obj=None):
    """Within the block, a connection this thread opens to a local address fails.

    Yields an object whose `refused` tells whether a connection was refused, so a caller
    can tell "the target moved onto the LAN" from an ordinary delivery failure. With
    `active=False` it does nothing (an admin-approved webhook). `apprise_obj`, when
    given, is switched to sequential sending: apprise sends to several targets from a
    thread pool, and a thread-local guard would not follow it there.
    """
    guard = _Guard()
    if not active:
        yield guard
        return
    if apprise_obj is not None:
        try:
            apprise_obj.asset.async_mode = False
            for instance in apprise_obj:
                instance.asset.async_mode = False
        except Exception:
            pass
    _install()
    stack = getattr(_guard_state, "stack", None)
    if stack is None:
        stack = _guard_state.stack = []
    stack.append(guard)
    try:
        yield guard
    finally:
        stack.pop()
