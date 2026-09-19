"""AdGuard Home provider — DNS rewrite management."""

import requests

from app.providers.base import (
    DNSProvider,
    ProviderListingRefused,
    TimeoutSession,
    login_check,
    reachability_check,
)
from app.text import plural


class AdGuardProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.session = TimeoutSession()
        self.session.auth = (username, password)
        self.session.headers["Content-Type"] = "application/json"

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.url}/control/status")
            return r.status_code == 200
        except requests.RequestException:
            return False

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        """Reachability, then credentials, then the one read the provider actually needs.

        Without this, `_provider_diagnostics` falls back to `test_connection` alone and
        reports every failure as `connection_failed`, credentials included. AdGuard answers
        401 to a wrong password on every route, so the fallback said "the connection test
        failed" about a host that was answering perfectly.
        """
        status_url = f"{self.url}/control/status"
        checks = [reachability_check(self.session, status_url)]
        if not checks[0]["ok"]:
            return {"ok": False, "checks": checks, "warnings": []}

        authenticated = self.test_connection()
        checks.append(login_check(authenticated))
        if not authenticated:
            return {"ok": False, "checks": checks, "warnings": []}

        try:
            count = len(self.list_rewrites())
            read_ok, detail = True, f"{plural(count, 'rewrite')} readable"
        except Exception as exc:
            read_ok, detail = False, str(exc)
        checks.append({
            "name": "List rewrites",
            "ok": read_ok,
            "detail": detail,
            "detail_code": "dns_read_ok" if read_ok else "dns_read_failed",
            "blocking": not read_ok,
        })
        return {"ok": read_ok, "checks": checks, "warnings": []}

    def list_rewrites(self) -> list[dict]:
        """Every rewrite AdGuard holds.

        Raises rather than answering []. AdGuard's whole inventory comes back in one call,
        so there was never a partial list to hand out here -- but [] is not a neutral
        answer either. It is how `add_rewrite` decides the name is free, how `/drift` finds
        a rewrite missing, and how the record routes answer 404. A request that failed says
        nothing about what AdGuard holds.
        """
        try:
            r = self.session.get(f"{self.url}/control/rewrite/list")
            r.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderListingRefused(f"AdGuard would not list its rewrites: {exc}") from exc
        return [{"domain": e["domain"], "answer": e["answer"]} for e in r.json()]

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
