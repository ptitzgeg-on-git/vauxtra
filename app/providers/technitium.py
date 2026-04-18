"""Technitium DNS Server provider — DNS rewrite management via HTTP API."""

import requests
from app.providers.base import DNSProvider
from app.config import PROVIDER_TIMEOUT


class TechnitiumProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None
        self.session = requests.Session()
        self.session.timeout = PROVIDER_TIMEOUT

    def _login(self) -> bool:
        try:
            r = self.session.post(
                f"{self.url}/api/user/login",
                data={"user": self._username, "pass": self._password},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    self._token = data["response"]["token"]
                    return True
        except (requests.RequestException, KeyError):
            pass
        return False

    def _ensure_token(self) -> bool:
        if self._token:
            try:
                r = self.session.get(
                    f"{self.url}/api/user/session/get",
                    params={"token": self._token},
                )
                if r.status_code == 200 and r.json().get("status") == "ok":
                    return True
            except (requests.RequestException, ValueError):
                pass
        return self._login()

    def _list_zones(self) -> list[str]:
        try:
            r = self.session.get(
                f"{self.url}/api/zones/list",
                params={"token": self._token},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    return [z["name"] for z in data["response"].get("zones", []) if not z.get("disabled")]
        except (requests.RequestException, KeyError, ValueError):
            pass
        return []

    def _find_zone(self, domain: str) -> str | None:
        """Return the longest matching zone for a domain."""
        zones = self._list_zones()
        domain_lower = domain.lower().rstrip(".")
        for zone in sorted(zones, key=len, reverse=True):
            if domain_lower == zone.lower() or domain_lower.endswith("." + zone.lower()):
                return zone
        return None

    def _zone_fallback(self, domain: str) -> str:
        """Derive a zone from the last two labels when no zone is found."""
        parts = domain.rstrip(".").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else domain

    def test_connection(self) -> bool:
        return self._ensure_token()

    def list_rewrites(self) -> list[dict]:
        if not self._ensure_token():
            return []
        try:
            zones = self._list_zones()
            records: list[dict] = []
            for zone in zones:
                r = self.session.get(
                    f"{self.url}/api/zones/records/get",
                    params={"token": self._token, "zone": zone, "type": "A"},
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                if data.get("status") != "ok":
                    continue
                for rec in data["response"].get("records", []):
                    if rec.get("type") == "A" and not rec.get("isDisabled"):
                        ip = rec.get("rData", {}).get("ipAddress", "")
                        if ip:
                            records.append({"domain": rec["name"], "answer": ip})
            return records
        except (requests.RequestException, KeyError, ValueError):
            return []

    def add_rewrite(self, domain: str, ip: str) -> bool:
        if not self._ensure_token():
            return False
        try:
            zone = self._find_zone(domain) or self._zone_fallback(domain)
            r = self.session.get(
                f"{self.url}/api/zones/records/add",
                params={
                    "token": self._token,
                    "zone": zone,
                    "domain": domain,
                    "type": "A",
                    "ipAddress": ip,
                    "ttl": "3600",
                    "overwrite": "false",
                },
            )
            if r.status_code == 200:
                return r.json().get("status") == "ok"
        except (requests.RequestException, ValueError):
            pass
        return False

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        if not self._ensure_token():
            return False
        try:
            zone = self._find_zone(domain) or self._zone_fallback(domain)
            r = self.session.get(
                f"{self.url}/api/zones/records/delete",
                params={
                    "token": self._token,
                    "zone": zone,
                    "domain": domain,
                    "type": "A",
                    "ipAddress": ip,
                },
            )
            if r.status_code == 200:
                return r.json().get("status") == "ok"
        except (requests.RequestException, ValueError):
            pass
        return False

    def update_rewrite(self, old_domain: str, old_ip: str, new_domain: str, new_ip: str) -> bool:
        if old_domain == new_domain and old_ip == new_ip:
            return True
        if not self.add_rewrite(new_domain, new_ip):
            return False
        if not self.delete_rewrite(old_domain, old_ip):
            return True  # new record created; old delete failed (logged by caller)
        return True
