"""Traefik provider — read-only proxy monitoring via the Traefik REST API.

Traefik exposes a read-only API (no write endpoints).
This provider supports importing / monitoring existing routes.
Push and delete operations raise RuntimeError.

Storage convention:
  url      → Traefik API base (e.g. http://traefik:8080)
  username → Basic-auth username (optional)
  password → Basic-auth password (optional)
"""

import re
from urllib.parse import urlparse

import requests

from app.providers.base import ProxyProvider, TimeoutSession


class TraefikProvider(ProxyProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url     = url.rstrip("/")
        self.session = TimeoutSession()
        if username and password:
            self.session.auth = (username, password)

    # ── ProxyProvider interface ───────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.url}/api/overview")
            return r.status_code == 200
        except requests.RequestException:
            return False

    _MAX_PAGES = 100
    _HOST_CALL = re.compile(r"\bHost\(([^)]*)\)")
    _QUOTED = re.compile(r"[`\"]([^`\"]+)[`\"]")

    def _get_all(self, path: str) -> list:
        """Every page of a listing. Traefik pages by 100 and names the next page in
        `X-Next-Page`; reading the first answer alone dropped every route past the 100th.

        Raises `requests.RequestException` when the listing cannot be read whole, the page
        cap included: a truncated list reads as "these routes are gone" to the caller.
        """
        items: list = []
        page = 1
        for _ in range(self._MAX_PAGES):
            r = self.session.get(f"{self.url}{path}", params={"page": page, "per_page": 100})
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError as e:
                raise requests.RequestException(f"Traefik listing {path} is not JSON") from e
            # N14: anything but a list used to be skipped, so an error page or an object
            # read as "no routes" -- the same answer as a Traefik with nothing configured.
            if not isinstance(data, list):
                raise requests.RequestException(f"Traefik listing {path} is not a list")
            items.extend(data)
            try:
                nxt = int(r.headers.get("X-Next-Page") or 0)
            except (TypeError, ValueError):
                nxt = 0
            if nxt <= page:
                break
            page = nxt
        else:
            raise requests.RequestException(
                f"Traefik listing {path} has more than {self._MAX_PAGES} pages"
            )
        return items

    @classmethod
    def _rule_hosts(cls, rule: str) -> list[str]:
        """Every name of every `Host(...)` matcher. A matcher may list several names
        (`Host(`a`, `b`)`), and the old pattern read none of them in that form."""
        hosts: list[str] = []
        for args in cls._HOST_CALL.findall(rule or ""):
            for name in cls._QUOTED.findall(args):
                name = name.strip()
                if name and name not in hosts:
                    hosts.append(name)
        return hosts

    def list_hosts(self) -> list[dict] | None:
        """Return all enabled HTTP routers as normalised host dicts, or None on failure.

        None, not [], when the routers could not be read: callers take [] for "Traefik
        serves nothing" and report every route as missing.

        Each entry includes:
          - ``middlewares``: list of middleware names active on this router
          - ``tls_resolver``: ACME cert resolver name if TLS is configured
        """
        try:
            routers  = self._get_all("/api/http/routers")
            services = self._get_all("/api/http/services")

            # service name → first backend URL
            svc_map: dict[str, str] = {}
            for svc in services:
                name    = svc.get("name", "")
                servers = svc.get("loadBalancer", {}).get("servers", [])
                if servers:
                    svc_map[name] = servers[0].get("url", "")

            # `/api/http/middlewares` used to be fetched here to build a name -> type map
            # that nothing read: a third round-trip to Traefik on every listing, and its
            # answer discarded. The `middlewares` each host reports are the names the router
            # itself carries, which come from the routers call.

            hosts = []
            for idx, router in enumerate(routers):
                status = router.get("status", "enabled")
                if status not in ("enabled", ""):
                    continue
                rule    = router.get("rule", "")
                domains = self._rule_hosts(rule)
                if not domains:
                    continue

                backend_url = svc_map.get(router.get("service", ""), "")
                scheme, host, port = "http", "", 80
                if backend_url:
                    try:
                        parsed = urlparse(backend_url)
                        scheme = parsed.scheme or "http"
                        host   = parsed.hostname or ""
                        port   = parsed.port or (443 if scheme == "https" else 80)
                    except Exception:
                        # Malformed backend URL; use defaults (scheme=http, port=80)
                        pass

                # Middlewares attached to this router
                raw_middlewares = router.get("middlewares") or []
                middlewares = list(raw_middlewares) if isinstance(raw_middlewares, list) else []

                # TLS configuration
                tls_cfg = router.get("tls") or {}
                tls_resolver = tls_cfg.get("certResolver") if isinstance(tls_cfg, dict) else None

                hosts.append({
                    "id":           router.get("name", str(idx)),
                    "domains":      domains,
                    "target":       backend_url,
                    "scheme":       scheme,
                    "host":         host,
                    "port":         port,
                    "ssl":          bool(router.get("tls")),
                    "websocket":    False,
                    "cert_id":      None,
                    "middlewares":  middlewares,
                    "tls_resolver": tls_resolver,
                })
            return hosts
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            # A non-JSON body (ValueError) or an unexpected shape is as unreadable as a
            # network error.
            return None

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        return None  # Traefik is read-only — routes are managed via Docker labels or config files

    def delete_host(self, host_id):
        return False  # Traefik is read-only — routes are managed via Docker labels or config files

    def get_certificates(self) -> list[dict]:
        # Traefik manages ACME certs internally; not accessible via API
        return []

    def find_best_certificate(self, domain_suffix: str):
        return None
