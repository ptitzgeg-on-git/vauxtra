"""AdGuard Home provider — DNS rewrite management."""

import requests
from app.providers.base import DNSProvider
from app.config import PROVIDER_TIMEOUT


class AdGuardProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers["Content-Type"] = "application/json"
        self.session.timeout = PROVIDER_TIMEOUT

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.url}/control/status")
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_rewrites(self) -> list[dict]:
        try:
            r = self.session.get(f"{self.url}/control/rewrite/list")
            r.raise_for_status()
            return [{"domain": e["domain"], "answer": e["answer"]} for e in r.json()]
        except requests.RequestException:
            return []

    def add_rewrite(self, domain: str, ip: str) -> bool:
        try:
            # Check for existing duplicate before creating
            existing = self.list_rewrites()
            if any(r["domain"] == domain and r["answer"] == ip for r in existing):
                return True  # already exists
            r = self.session.post(
                f"{self.url}/control/rewrite/add",
                json={"domain": domain, "answer": ip},
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        try:
            r = self.session.post(
                f"{self.url}/control/rewrite/delete",
                json={"domain": domain, "answer": ip},
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def update_rewrite(self, old_domain: str, old_ip: str, new_domain: str, new_ip: str) -> bool:
        """Update a DNS rewrite (create new first, then delete old to avoid data loss)."""
        if old_domain == new_domain and old_ip == new_ip:
            return True  # nothing to change
        if not self.add_rewrite(new_domain, new_ip):
            return False
        if not self.delete_rewrite(old_domain, old_ip):
            return True  # new record created; old delete failed (logged by caller)
        return True
