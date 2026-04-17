import requests
from app.providers.base import DNSProvider
from app.config import PROVIDER_TIMEOUT


class PiholeProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url      = url.rstrip("/")
        self.api_key  = password
        self.password = password
        self.session  = requests.Session()
        self.session.timeout = PROVIDER_TIMEOUT
        self._v6_sid  = None
        self._version = None

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
                self._v6_sid = r.json().get("session", {}).get("sid", "")
                if self._v6_sid:
                    self.session.headers["X-FTL-SID"] = self._v6_sid
                    return True
        except requests.RequestException:
            pass
        return False

    def _ensure_auth(self) -> bool:
        if self._version is None:
            self._version = self._detect_version()
        if self._version == 6:
            if self._v6_sid:
                # Verify the session is still valid
                try:
                    r = self.session.get(f"{self.url}/api/dns/local/records", timeout=5)
                    if r.status_code == 200:
                        return True
                except requests.RequestException:
                    pass
                self._v6_sid = None
            return self._login_v6()
        return True

    def test_connection(self) -> bool:
        if not self._ensure_auth():
            return False
        try:
            if self._version == 6:
                r = self.session.get(f"{self.url}/api/dns/local/records", timeout=5)
                return r.status_code == 200
            else:
                r = self.session.get(
                    f"{self.url}/admin/api.php",
                    params={"list": "all", "auth": self.api_key},
                    timeout=5,
                )
                if r.status_code != 200:
                    return False
                data = r.json()
                return isinstance(data, (dict, list)) and bool(data)
        except requests.RequestException:
            return False

    def list_rewrites(self) -> list[dict]:
        if not self._ensure_auth():
            return []
        try:
            if self._version == 6:
                r = self.session.get(f"{self.url}/api/dns/local/records")
                r.raise_for_status()
                return [
                    {"domain": e["domain"], "answer": e["ip"]}
                    for e in r.json().get("records", [])
                    if e.get("type") == "A"
                ]
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
        if not self._ensure_auth():
            return False
        try:
            if self._version == 6:
                r = self.session.post(
                    f"{self.url}/api/dns/local/records",
                    json={"domain": domain, "ip": ip, "type": "A", "ttl": 300},
                )
                return r.status_code in (200, 201)
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
        if not self._ensure_auth():
            return False
        try:
            if self._version == 6:
                r = self.session.delete(
                    f"{self.url}/api/dns/local/records",
                    json={"domain": domain, "ip": ip},
                )
                return r.status_code in (200, 204)
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
        if not self.add_rewrite(new_domain, new_ip):
            return False
        if not self.delete_rewrite(old_domain, old_ip):
            return True  # new record created; old delete failed (logged by caller)
        return True
