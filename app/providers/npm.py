"""Nginx Proxy Manager provider — proxy host and certificate management."""

import requests
from app.providers.base import ProxyProvider
from app.config import PROVIDER_TIMEOUT


class NPMProvider(ProxyProvider):

    def __init__(self, url: str, email: str, password: str):
        self.api_url = url.rstrip("/")
        if not self.api_url.endswith("/api"):
            self.api_url += "/api"
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        self.session.timeout = PROVIDER_TIMEOUT
        self._token = None

    def _login(self) -> bool:
        """Authenticate and retrieve a Bearer token."""
        try:
            r = self.session.post(
                f"{self.api_url}/tokens",
                json={"identity": self.email, "secret": self.password},
                timeout=PROVIDER_TIMEOUT,
            )
            r.raise_for_status()
            self._token = r.json().get("token", "")
            self.session.headers["Authorization"] = f"Bearer {self._token}"
            return bool(self._token)
        except requests.RequestException:
            return False

    def _ensure_auth(self) -> bool:
        """Ensure the token is valid, reconnect if needed."""
        if self._token:
            try:
                r = self.session.get(
                    f"{self.api_url}/nginx/proxy-hosts?limit=1",
                    timeout=PROVIDER_TIMEOUT,
                )
                if r.status_code == 200:
                    return True
            except requests.RequestException:
                pass
        return self._login()

    def test_connection(self) -> bool:
        return self._ensure_auth()

    def list_hosts(self) -> list[dict]:
        if not self._ensure_auth():
            return []
        try:
            r = self.session.get(f"{self.api_url}/nginx/proxy-hosts")
            r.raise_for_status()
            hosts = r.json()
            return [
                {
                    "id": h["id"],
                    "domains": h.get("domain_names", []),
                    "target": f"{h['forward_scheme']}://{h['forward_host']}:{h['forward_port']}",
                    "scheme": h["forward_scheme"],
                    "host": h["forward_host"],
                    "port": h["forward_port"],
                    "ssl": h.get("ssl_forced", False),
                    "websocket": h.get("allow_websocket_upgrade", False),
                    "cert_id": h.get("certificate_id"),
                }
                for h in hosts
            ]
        except requests.RequestException:
            return []

    def create_host(self, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> dict | None:
        if not self._ensure_auth():
            return None
        payload = {
            "domain_names": [domain],
            "forward_scheme": scheme,
            "forward_host": ip,
            "forward_port": port,
            "certificate_id": cert_id,
            "ssl_forced": cert_id is not None,
            "http2_support": cert_id is not None,
            "block_exploits": True,
            "allow_websocket_upgrade": websocket,
            "hsts_enabled": False,
            "locations": [],
            "meta": {},
        }
        try:
            r = self.session.post(
                f"{self.api_url}/nginx/proxy-hosts", json=payload
            )
            r.raise_for_status()
            data = r.json()
            return {"id": data.get("id"), "domain": domain}
        except requests.RequestException:
            return None

    def delete_host(self, host_id: int) -> bool:
        if not self._ensure_auth():
            return False
        try:
            r = self.session.delete(f"{self.api_url}/nginx/proxy-hosts/{host_id}")
            return r.status_code in (200, 204) or r.text == "true"
        except requests.RequestException:
            return False

    def update_host(self, host_id: int, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> bool:
        """Update an existing proxy host via PUT."""
        if not self._ensure_auth():
            return False
        payload = {
            "domain_names": [domain],
            "forward_scheme": scheme,
            "forward_host": ip,
            "forward_port": port,
            "certificate_id": cert_id,
            "ssl_forced": cert_id is not None,
            "http2_support": cert_id is not None,
            "block_exploits": True,
            "allow_websocket_upgrade": websocket,
            "hsts_enabled": False,
            "locations": [],
            "meta": {},
        }
        try:
            r = self.session.put(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}", json=payload
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_certificates(self) -> list[dict]:
        if not self._ensure_auth():
            raise RuntimeError("NPM authentication failed (check email and password)")
        r = self.session.get(
            f"{self.api_url}/nginx/certificates",
            timeout=PROVIDER_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        # NPM v3 wraps results in {"data": [...]}
        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return []
        result = []
        for c in data:
            if not isinstance(c, dict):
                continue
            result.append({
                "id":         c.get("id"),
                "nice_name":  c.get("nice_name", ""),
                "domains":    c.get("domain_names", []),
                "expires_on": (
                    c.get("expires_on") or
                    c.get("meta", {}).get("letsencrypt_certificate", {}).get("expires_on") or
                    ""
                ),
            })
        return result

    def find_best_certificate(self, domain_suffix: str) -> int | None:
        """Select the best certificate: wildcard first, then subdomain match, then fallback."""
        certs = self.get_certificates()
        suffix = domain_suffix.lower()
        preferred = None
        fallback = None

        for cert in certs:
            cid = cert["id"]
            names = [n.lower() for n in cert["domains"]]
            names.append(cert["nice_name"].lower())
            # Exact wildcard or exact domain match
            if any(n in (f"*.{suffix}", suffix) for n in names):
                return cid
            # Subdomain of the target domain
            if any(n.endswith(f".{suffix}") for n in names):
                preferred = cid
            # Last resort
            if fallback is None:
                fallback = cid

        return preferred
