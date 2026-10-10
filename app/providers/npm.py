"""Nginx Proxy Manager provider — proxy host and certificate management."""

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import ProxyProvider, TimeoutSession


def _numeric_host_id(host_id) -> int | None:
    """The identifier as NPM numbers it, or None if this is not one.

    Two reasons to check it here rather than at the call site. The first is that the caller
    does not know: `DELETE /api/providers/{pid}/proxy-hosts/{host_id}` serves all three
    providers and only this one insists on an integer. The second is that the value is
    interpolated into a URL -- a `host_id` of `1/../../users` would compose a path NPM
    would happily serve.

    Returns None rather than raising: the `ProxyProvider` contract is that a failure gives
    back a falsy value, and every caller already handles that.
    """
    try:
        return int(str(host_id).strip())
    except (TypeError, ValueError):
        return None


class NPMProvider(ProxyProvider):

    def __init__(self, url: str, email: str, password: str):
        self.api_url = url.rstrip("/")
        if not self.api_url.endswith("/api"):
            self.api_url += "/api"
        self.email = email
        self.password = password
        self.session = TimeoutSession()
        self.session.headers["Content-Type"] = "application/json"
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
            body = r.json()
            token = body.get("token", "") if isinstance(body, dict) else ""
            self._token = token if isinstance(token, str) else ""
            self.session.headers["Authorization"] = f"Bearer {self._token}"
            return bool(self._token)
        except (requests.RequestException, ValueError):
            # B4: a non-JSON or non-object answer is a failed login, not an exception
            # escaping through `list_hosts`.
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

    def list_hosts(self) -> list[dict] | None:
        # None, not [], when the list could not be read: the push took [] for "no host yet"
        # and created a second one for a name NPM already served.
        if not self._ensure_auth():
            return None
        try:
            r = self.session.get(
                f"{self.api_url}/nginx/proxy-hosts",
                timeout=PROVIDER_TIMEOUT,
            )
            r.raise_for_status()
            hosts = r.json()
            if not isinstance(hosts, list):
                # B4: `{}` or `""` iterate as nothing, which read as "no host yet" and made
                # the push create a duplicate.
                return None
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
                    "enabled": bool(h.get("enabled", True)),
                }
                for h in hosts
            ]
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            # B4: a body that is not JSON, not a list, or a host without the expected keys
            # is an unreadable list too, not an exception for every caller to meet.
            return None

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
                f"{self.api_url}/nginx/proxy-hosts",
                json=payload,
                timeout=PROVIDER_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            return {"id": data.get("id"), "domain": domain}
        except requests.RequestException:
            return None

    def delete_host(self, host_id: int | str) -> bool:
        host_id = _numeric_host_id(host_id)
        if host_id is None or not self._ensure_auth():
            return False
        try:
            r = self.session.delete(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}",
                timeout=PROVIDER_TIMEOUT,
            )
            # 404: the host is already gone, which is what the caller asked for. Answering
            # False kept the service attached to it forever, and the auto-reconcile then
            # recreated the host on the proxy the operator was moving away from.
            return r.status_code in (200, 204, 404) or r.text == "true"
        except requests.RequestException:
            return False

    def update_host(self, host_id: int, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> bool:
        """Update an existing proxy host via PUT."""
        host_id = _numeric_host_id(host_id)
        if host_id is None or not self._ensure_auth():
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
        payload = self._merge_with_current(host_id, payload, domain, cert_id)
        if payload is None:
            # The PUT replaces the whole host: sent without the current one, it wiped the
            # locations, HSTS, advanced config and metadata the operator had set.
            return False
        try:
            r = self.session.put(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}",
                json=payload,
                timeout=PROVIDER_TIMEOUT,
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    # Settings an operator sets in the NPM interface and Vauxtra never manages: a PUT built
    # from scratch reset them all (custom locations, HSTS, advanced config, access list).
    _OPERATOR_FIELDS = (
        "locations", "hsts_enabled", "hsts_subdomains", "advanced_config", "access_list_id",
        "caching_enabled", "block_exploits", "meta",
    )

    def _merge_with_current(self, host_id: int, payload: dict, domain: str,
                            cert_id: int | None) -> dict | None:
        """Carry over what the operator configured on the host; Vauxtra only owns the route.

        None when the host cannot be read: the update is then refused rather than sent as a
        bare payload that resets everything Vauxtra does not manage (N11).
        """
        try:
            r = self.session.get(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}",
                timeout=PROVIDER_TIMEOUT,
            )
            if r.status_code != 200:
                return None
            current = r.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(current, dict):
            return None

        merged = dict(payload)
        for field in self._OPERATOR_FIELDS:
            if field in current:
                merged[field] = current[field]
        # The first name is the one Vauxtra created the host with; aliases the operator
        # added after it stay.
        names = [n for n in (current.get("domain_names") or []) if isinstance(n, str)]
        merged["domain_names"] = [domain] + [n for n in names[1:] if n != domain]
        # No certificate found by Vauxtra does not mean the attached one must go.
        if cert_id is None and current.get("certificate_id"):
            merged["certificate_id"] = current["certificate_id"]
            merged["ssl_forced"] = merged["http2_support"] = True
        # `ssl_forced` and `http2_support` are the operator's once the host has a
        # certificate: an update forced both on, turning back on what had been switched
        # off in NPM. They are only Vauxtra's to set when this update attaches the
        # host's first certificate, which is what `create_host` does too.
        attaches_first_cert = cert_id is not None and not current.get("certificate_id")
        if not attaches_first_cert:
            for field in ("ssl_forced", "http2_support"):
                if field in current:
                    merged[field] = bool(current[field])
        return merged

    def toggle_host(self, host_id: int | str, enabled: bool) -> bool:
        """Enable or disable a proxy host via NPM's dedicated enable/disable endpoints."""
        host_id = _numeric_host_id(host_id)
        if host_id is None or not self._ensure_auth():
            return False
        action = "enable" if enabled else "disable"
        try:
            r = self.session.post(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}/{action}",
                timeout=PROVIDER_TIMEOUT,
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
        """Return a certificate that actually covers the host, or None.

        Callers pass the full service hostname. A certificate qualifies only when one
        of its names covers that host: the exact name, or the wildcard of its parent
        zone -- TLS wildcards cover exactly one label, so `*.example.com` covers
        `vault.example.com` but not `a.b.example.com`. `*.host` is also accepted for
        the case where the caller passes a bare zone.

        There is deliberately no last-resort fallback. `create_host` sets
        `"ssl_forced": cert_id is not None`: handing back an unrelated certificate
        forces HTTPS on a host it does not cover, and every visitor is met with
        ERR_CERT_COMMON_NAME_INVALID. No certificate is the honest answer.
        """
        host = (domain_suffix or "").strip().lower().rstrip(".")
        if not host:
            return None
        parent = host.split(".", 1)[1] if "." in host else ""

        exact: int | None = None
        wildcard: int | None = None

        for cert in self.get_certificates():
            cid = cert["id"]
            names = {str(n).strip().lower().rstrip(".") for n in cert["domains"] if n}
            nice = str(cert.get("nice_name") or "").strip().lower().rstrip(".")
            if nice:
                names.add(nice)

            if exact is None and host in names:
                exact = cid
            if wildcard is None and (
                (parent and f"*.{parent}" in names) or f"*.{host}" in names
            ):
                wildcard = cid

        return exact if exact is not None else wildcard
