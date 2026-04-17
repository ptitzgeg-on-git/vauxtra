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

from app.providers.base import ProxyProvider
from app.config import PROVIDER_TIMEOUT


class TraefikProvider(ProxyProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url     = url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = PROVIDER_TIMEOUT
        if username and password:
            self.session.auth = (username, password)

    # ── ProxyProvider interface ───────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.url}/api/overview")
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_hosts(self) -> list[dict]:
        """Return all enabled HTTP routers as normalised host dicts.

        Each entry includes:
          - ``middlewares``: list of middleware names active on this router
          - ``tls_resolver``: ACME cert resolver name if TLS is configured
        """
        try:
            r_routers    = self.session.get(f"{self.url}/api/http/routers")
            r_services   = self.session.get(f"{self.url}/api/http/services")
            r_middlewares = self.session.get(f"{self.url}/api/http/middlewares")
            r_routers.raise_for_status()
            r_services.raise_for_status()

            # service name → first backend URL
            svc_map: dict[str, str] = {}
            for svc in r_services.json():
                name    = svc.get("name", "")
                servers = svc.get("loadBalancer", {}).get("servers", [])
                if servers:
                    svc_map[name] = servers[0].get("url", "")

            # middleware name → type (for display)
            mw_types: dict[str, str] = {}
            if r_middlewares.ok:
                for mw in r_middlewares.json():
                    mw_name = mw.get("name", "")
                    # Determine middleware type by inspecting keys
                    mw_type = next(
                        (k for k in mw.keys() if k not in ("name", "type", "status", "provider", "usedBy")),
                        mw.get("type", "unknown"),
                    )
                    mw_types[mw_name] = mw_type

            hosts = []
            for idx, router in enumerate(r_routers.json()):
                status = router.get("status", "enabled")
                if status not in ("enabled", ""):
                    continue
                rule    = router.get("rule", "")
                domains = re.findall(r"Host\(`([^`]+)`\)", rule)
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
        except requests.RequestException:
            return []

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        return None  # Traefik is read-only — routes are managed via Docker labels or config files

    def delete_host(self, host_id):
        return False  # Traefik is read-only — routes are managed via Docker labels or config files

    def get_certificates(self) -> list[dict]:
        # Traefik manages ACME certs internally; not accessible via API
        return []

    def find_best_certificate(self, domain_suffix: str):
        return None
