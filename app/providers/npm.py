"""Nginx Proxy Manager provider — proxy host and certificate management."""

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import (
    ProviderListingRefused,
    ProxyProvider,
    TimeoutSession,
    login_check,
    reachability_check,
)
from app.text import plural


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

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        """Reachability first, credentials second.

        `test_connection` here is `_ensure_auth`, which is `False` for an NPM that is down
        and `False` for one that refused the email and password. Reported through the
        fallback in `_provider_diagnostics` both came out as `connection_failed`, which
        points the operator at the network when the account is what needs looking at.

        The third check is the one NPM was missing while every other provider had it. A
        token that authenticates is not a token that can read: NPM gives a user per-object
        permissions, and one whose `proxy_hosts` visibility is off signs in perfectly and
        is then refused the list Vauxtra manages. Stopping at "Login OK" made that account
        look ready, and the refusal surfaced later as a push that saved nothing.
        """
        checks = [reachability_check(self.session, f"{self.api_url}/tokens")]
        if not checks[0]["ok"]:
            return {"ok": False, "checks": checks, "warnings": []}
        authenticated = self._ensure_auth()
        checks.append(login_check(authenticated))
        if not authenticated:
            return {"ok": False, "checks": checks, "warnings": []}

        try:
            count = len(self.list_hosts())
            read_ok, detail = True, f"{plural(count, 'host')} readable"
        except Exception as exc:
            read_ok, detail = False, str(exc)
        checks.append({
            "name": "List hosts",
            "ok": read_ok,
            "detail": detail,
            "detail_code": "proxy_read_ok" if read_ok else "proxy_read_failed",
            "blocking": not read_ok,
        })
        return {"ok": read_ok, "checks": checks, "warnings": []}

    def list_hosts(self) -> list[dict]:
        """Every proxy host NPM holds.

        Raises rather than answering []. A request that failed says nothing about what NPM
        holds, and every caller acts on the difference: the drift check reads no matching
        host as `route_missing` and offers a Reconcile button, `_service_proxy_state` reads
        it as a service that was never published. NPM refusing the list for a moment is not
        the same answer as NPM holding nothing, and each caller already wraps this call,
        so the honest answer arrives as `proxy_check_failed` instead of a route to republish.
        """
        if not self._ensure_auth():
            raise ProviderListingRefused("NPM refused the credentials")
        try:
            r = self.session.get(
                f"{self.api_url}/nginx/proxy-hosts",
                timeout=PROVIDER_TIMEOUT,
            )
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
                    "enabled": bool(h.get("enabled", True)),
                }
                for h in hosts
            ]
        except requests.RequestException as exc:
            raise ProviderListingRefused(f"NPM would not list its proxy hosts: {exc}") from exc

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
            return r.status_code in (200, 204) or r.text == "true"
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
        try:
            r = self.session.put(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}",
                json=payload,
                timeout=PROVIDER_TIMEOUT,
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _host_enabled(self, host_id: int) -> bool | None:
        """Whether NPM serves this host right now, or None when the state could not be read."""
        try:
            r = self.session.get(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}",
                timeout=PROVIDER_TIMEOUT,
            )
            if r.status_code != 200:
                return None
            return bool(r.json().get("enabled"))
        except (requests.RequestException, ValueError):
            return None

    def toggle_host(self, host_id: int | str, enabled: bool) -> bool:
        """Enable or disable a proxy host via NPM's dedicated enable/disable endpoints.

        NPM answers 400 "Host is already enabled" when the host is in the state being asked
        for, so the status code alone cannot tell a refusal apart from a no-op. Every push
        resumes the host it just updated, and almost every host it updates is already
        running, so reading the code alone reported the ordinary case as a refused push.
        Read the host back instead and answer on the state it is actually in, which is what
        the caller asked about; only a host still in the wrong state is a real refusal.
        """
        host_id = _numeric_host_id(host_id)
        if host_id is None or not self._ensure_auth():
            return False
        action = "enable" if enabled else "disable"
        try:
            r = self.session.post(
                f"{self.api_url}/nginx/proxy-hosts/{host_id}/{action}",
                timeout=PROVIDER_TIMEOUT,
            )
            if r.status_code == 200:
                return True
            return self._host_enabled(host_id) == enabled
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
