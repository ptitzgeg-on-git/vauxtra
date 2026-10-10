"""AdGuard Home provider — DNS rewrite management."""

import requests

from app.providers.base import DNSProvider, TimeoutSession, record_value_key


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

    def list_rewrites(self) -> list[dict] | None:
        # None, not [], when the list could not be read: the push took [] for "no record
        # yet" and AdGuard accepts a second rewrite for the same name.
        try:
            r = self.session.get(f"{self.url}/control/rewrite/list")
            r.raise_for_status()
            return [{"domain": e["domain"], "answer": e["answer"]} for e in r.json()]
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            # A body of another shape (`null`, an object, an entry without `answer`) is as
            # unreadable as a network error; it used to escape as KeyError or TypeError.
            return None

    def add_rewrite(self, domain: str, ip: str) -> bool:
        try:
            # Check for existing duplicate before creating
            existing = self.list_rewrites()
            if existing is None:
                return False  # unknown state: adding could duplicate a record
            def _n(v) -> str:
                return str(v or "").strip().lower().rstrip(".")

            if any(_n(r["domain"]) == _n(domain)
                   and record_value_key(r["answer"]) == record_value_key(ip) for r in existing):
                return True  # already exists
            r = self.session.post(
                f"{self.url}/control/rewrite/add",
                json={"domain": domain, "answer": ip},
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        # M13: AdGuard deletes an exact (domain, answer) pair and answers 200 even when that
        # pair does not exist. Sending the caller's spelling (`App.Example.com`,
        # `2001:DB8::1`) of a record stored under another one reported a delete that
        # removed nothing. The stored spelling is looked up and sent instead.
        existing = self.list_rewrites()
        if existing is None:
            pairs = [(domain, ip)]  # unknown state: try the spelling we were given
        else:
            wanted_domain = str(domain or "").strip().lower().rstrip(".")
            pairs = [
                (r["domain"], r["answer"])
                for r in existing
                if str(r.get("domain") or "").strip().lower().rstrip(".") == wanted_domain
                and record_value_key(r.get("answer")) == record_value_key(ip)
            ]
            if not pairs:
                pairs = [(domain, ip)]  # not listed: the spelling given, as before
        try:
            for stored_domain, stored_answer in pairs:
                r = self.session.post(
                    f"{self.url}/control/rewrite/delete",
                    json={"domain": stored_domain, "answer": stored_answer},
                )
                if r.status_code != 200:
                    return False
            return True
        except requests.RequestException:
            return False
