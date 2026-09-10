from contextlib import contextmanager
from urllib.parse import quote

import requests

from app.providers.base import DNSProvider, TimeoutSession


class PiholeProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url      = url.rstrip("/")
        self.api_key  = password
        self.password = password
        self.session  = TimeoutSession()
        self._v6_sid  = None
        self._v6_csrf = None
        self._version = None
        self._depth   = 0

    def _detect_version(self) -> int:
        try:
            r = self.session.get(f"{self.url}/api/auth", timeout=5)
            if r.status_code in (200, 401):
                return 6
        except requests.RequestException:
            pass
        return 5

    def _login_v6(self) -> bool:
        try:
            r = self.session.post(
                f"{self.url}/api/auth",
                json={"password": self.password},
                timeout=10,
            )
            if r.status_code == 200:
                payload = r.json() if r.content else {}
                session = payload.get("session", {}) if isinstance(payload, dict) else {}
                self._v6_sid = session.get("sid") or payload.get("sid") or ""
                self._v6_csrf = session.get("csrf") or payload.get("csrf") or ""
                if self._v6_sid and self._v6_csrf:
                    self.session.headers["X-FTL-SID"] = self._v6_sid
                    self.session.headers["X-FTL-CSRF"] = self._v6_csrf
                    return True
        except requests.RequestException:
            pass
        return False

    def _logout_v6(self) -> None:
        if not self._v6_sid:
            return
        try:
            self.session.delete(f"{self.url}/api/auth", timeout=5)
        except requests.RequestException:
            pass
        finally:
            self._v6_sid = None
            self._v6_csrf = None
            self.session.headers.pop("X-FTL-SID", None)
            self.session.headers.pop("X-FTL-CSRF", None)

    def _ensure_auth(self) -> bool:
        if self._version is None:
            self._version = self._detect_version()
        if self._version == 6:
            if self._v6_sid:
                # Verify the session is still valid
                try:
                    r = self.session.get(f"{self.url}/api/config/dns/hosts", timeout=5)
                    if r.status_code == 200:
                        return True
                except requests.RequestException:
                    pass
                self._v6_sid = None
                self._v6_csrf = None
                self.session.headers.pop("X-FTL-SID", None)
                self.session.headers.pop("X-FTL-CSRF", None)
            return self._login_v6()
        return True

    @contextmanager
    def _api_session(self):
        """Authenticate for one operation and always hand the API seat back.

        Pi-hole v6 caps concurrent sessions at `webserver.api.max_sessions` -- 16 by
        default -- and holds each one for `webserver.session.timeout`, 1800 seconds.
        Vauxtra builds a fresh provider for every request (`create_provider`), so every
        operation logs in again and gets its own seat.

        Only `test_connection` used to release one. `list_rewrites` did not, and that is
        the call the drift check and the scheduler make on every pass: sixteen of them and
        Pi-hole answers `api_seats_exceeded` to every login for the next half hour -- the
        operator's own browser included, because it draws on the same pool. Restarting
        Pi-hole does not clear it; the sessions are persisted, so only the timeout ends it.
        The failure surfaces as "provider rejected" inside Vauxtra, which points at the
        wrong thing entirely.

        The counter makes the helper reentrant, so `update_rewrite` can wrap its add and
        its delete in a single seat instead of spending two.

        Yields False when authentication failed; the caller returns its empty value.
        """
        self._depth += 1
        try:
            yield self._ensure_auth()
        finally:
            self._depth -= 1
            if self._depth == 0:
                # A no-op on v5 and whenever no session was opened.
                self._logout_v6()

    def test_connection(self) -> bool:
        with self._api_session() as authed:
            if not authed:
                return False
            return self._test_connection_inner()

    def _test_connection_inner(self) -> bool:
        try:
            if self._version == 6:
                r = self.session.get(f"{self.url}/api/config/dns/hosts", timeout=5)
                return r.status_code == 200
            else:
                r = self.session.get(
                    f"{self.url}/admin/api.php",
                    params={"customdns": "", "action": "get", "auth": self.api_key},
                    timeout=5,
                )
                if r.status_code != 200:
                    return False
                # On Pi-hole v5 this endpoint may legitimately return []
                # when no local rewrites exist; treat valid JSON response as
                # a successful authenticated connection.
                _ = r.json()
                return True
        except requests.RequestException:
            return False
        except ValueError:
            return False

    def list_rewrites(self) -> list[dict]:
        with self._api_session() as authed:
            if not authed:
                return []
            return self._list_rewrites_inner()

    def _list_rewrites_inner(self) -> list[dict]:
        try:
            if self._version == 6:
                r = self.session.get(f"{self.url}/api/config/dns/hosts")
                r.raise_for_status()
                hosts = r.json().get("config", {}).get("dns", {}).get("hosts", [])
                rewrites = []
                for entry in hosts:
                    parts = entry.split()
                    if len(parts) >= 2:
                        rewrites.append({"domain": parts[1], "answer": parts[0]})
                return rewrites
            else:
                r = self.session.get(
                    f"{self.url}/admin/api.php",
                    params={"customdns": "", "action": "get", "auth": self.api_key},
                )
                r.raise_for_status()
                return [
                    {"domain": row[0], "answer": row[1]}
                    for row in r.json().get("data", [])
                ]
        except requests.RequestException:
            return []

    def add_rewrite(self, domain: str, ip: str) -> bool:
        with self._api_session() as authed:
            if not authed:
                return False
            return self._add_rewrite_inner(domain, ip)

    def _add_rewrite_inner(self, domain: str, ip: str) -> bool:
        try:
            if self._version == 6:
                entry = quote(f"{ip} {domain}", safe="")
                r = self.session.put(f"{self.url}/api/config/dns/hosts/{entry}")
                return r.status_code == 201
            else:
                r = self.session.get(
                    f"{self.url}/admin/api.php",
                    params={
                        "customdns": "", "action": "add",
                        "domain": domain, "ip": ip,
                        "auth": self.api_key,
                    },
                )
                data = r.json()
                return r.status_code == 200 and data.get("success", False)
        except requests.RequestException:
            return False

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        with self._api_session() as authed:
            if not authed:
                return False
            return self._delete_rewrite_inner(domain, ip)

    def _delete_rewrite_inner(self, domain: str, ip: str) -> bool:
        try:
            if self._version == 6:
                entry = quote(f"{ip} {domain}", safe="")
                r = self.session.delete(f"{self.url}/api/config/dns/hosts/{entry}")
                return r.status_code == 204
            else:
                r = self.session.get(
                    f"{self.url}/admin/api.php",
                    params={
                        "customdns": "", "action": "delete",
                        "domain": domain, "ip": ip,
                        "auth": self.api_key,
                    },
                )
                data = r.json()
                return r.status_code == 200 and data.get("success", False)
        except requests.RequestException:
            return False

    def update_rewrite(self, old_domain: str, old_ip: str, new_domain: str, new_ip: str) -> bool:
        if old_domain == new_domain and old_ip == new_ip:
            return True  # nothing to change
        # One seat for both halves: the helper only logs out when the outermost caller
        # leaves, so the add and the delete share the session this opens.
        with self._api_session() as authed:
            if not authed:
                return False
            if not self.add_rewrite(new_domain, new_ip):
                return False
            if not self.delete_rewrite(old_domain, old_ip):
                return True  # new record created; old delete failed (logged by caller)
            return True
