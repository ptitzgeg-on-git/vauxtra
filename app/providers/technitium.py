"""Technitium DNS Server provider — DNS rewrite management via HTTP API."""

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import DNSProvider, TimeoutSession


class TechnitiumProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None
        self.session = TimeoutSession()

    def _login(self) -> bool:
        try:
            r = self.session.post(
                f"{self.url}/api/user/login",
                data={"user": self._username, "pass": self._password},
                timeout=PROVIDER_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    token = data.get("token") or data.get("response", {}).get("token")
                    if token:
                        self._token = token
                        return True
        except (requests.RequestException, KeyError, ValueError, TypeError, AttributeError):
            pass
        return False

    def _api(self, path: str, params: dict | None = None):
        """One authenticated API call: a POST whose form body carries the token.

        Technitium documents two ways to pass the token: the `token` query parameter and
        the same field in a form-encoded POST body. The query string ends up in proxy and
        access logs, and an `Authorization: Bearer` header is not read by every server
        version: when it was ignored, the login still succeeded, every read came back
        refused and every write failed. The form body is read by all versions.
        """
        return self.session.post(
            f"{self.url}{path}",
            data={**(params or {}), "token": self._token or ""},
            timeout=PROVIDER_TIMEOUT,
        )

    def _ensure_token(self) -> bool:
        if self._token:
            try:
                r = self._api("/api/user/session/get")
                if r.status_code == 200 and r.json().get("status") == "ok":
                    return True
            except (requests.RequestException, ValueError, AttributeError):
                pass
        return self._login()

    def _list_zones(self) -> list[str] | None:
        """The enabled zones, or None when the server could not be asked.

        "No zone" and "could not read" lead to opposite actions on the write path, so they
        are two different answers.
        """
        try:
            r = self._api("/api/zones/list")
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    return [z["name"] for z in data["response"].get("zones", []) if not z.get("disabled")]
        except (requests.RequestException, KeyError, ValueError, TypeError, AttributeError):
            pass
        return None

    def _find_zone(self, domain: str, zones: list[str] | None = None) -> str | None:
        """Return the longest matching zone for a domain."""
        if zones is None:
            zones = self._list_zones() or []
        domain_lower = domain.lower().rstrip(".")
        for zone in sorted(zones, key=len, reverse=True):
            if domain_lower == zone.lower() or domain_lower.endswith("." + zone.lower()):
                return zone
        return None

    def _write_zone(self, domain: str) -> str | None:
        """The zone a write goes to, or None when the zone list could not be read.

        The two-label guess is only made when the server answered and no zone matched:
        after a failed read it sent the record into the parent zone, where a child zone
        hides it.
        """
        zones = self._list_zones()
        if zones is None:
            return None
        return self._find_zone(domain, zones) or self._zone_fallback(domain)

    def _zone_fallback(self, domain: str) -> str:
        """Derive a zone from the last two labels when no zone is found."""
        parts = domain.rstrip(".").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else domain

    def test_connection(self) -> bool:
        """True only when an authenticated call is answered, not when the login alone is.

        A token the server does not accept still logs in: the connection used to show green
        while every zone read was refused.
        """
        return self._ensure_token() and self._list_zones() is not None

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        # Every check keeps its English `detail` (logs, older clients) and adds the short
        # `detail_code` the sentence was written from, so the panel can say the same thing in
        # the reader's language via `providers.diag.detail.<code>`.
        checks: list[dict] = []

        # 1. Authentication
        login_ok = self._login()
        checks.append({
            "name": "Login",
            "ok": login_ok,
            "detail": "Authenticated successfully" if login_ok else "Login failed — check username/password and URL",
            "detail_code": "login_ok" if login_ok else "login_failed",
            "blocking": True,
        })
        if not login_ok:
            return {"ok": False, "checks": checks, "warnings": []}

        # 2. List zones
        zones: list[str] = []
        zones_params: dict = {}
        try:
            listed = self._list_zones()
            if listed is None:
                raise RuntimeError("Could not read the zone list")
            zones = listed
            zones_ok = True
            zones_detail = f"{len(zones)} zone(s) accessible" if zones else "No zones found"
            zones_code = "zones_found" if zones else "zones_none"
            if zones:
                zones_params = {"count": len(zones)}
        except Exception as exc:
            zones_ok = False
            zones_detail = str(exc)
            zones_code = "zones_error"
            zones_params = {"error": str(exc)}
        checks.append({
            "name": "List zones",
            "ok": zones_ok,
            "detail": zones_detail,
            "detail_code": zones_code,
            "detail_params": zones_params,
            "blocking": not zones_ok,
        })

        warnings: list[str] = []
        if zones_ok and not zones:
            warnings.append("No DNS zones found. Create at least one zone in Technitium before using Vauxtra.")

        # 3. Optional write probe
        if write_probe and zones:
            test_domain = f"_vauxtra-probe.{zones[0]}"
            test_ip = "127.0.0.1"
            write_ok = self.add_rewrite(test_domain, test_ip)
            if write_ok:
                self.delete_rewrite(test_domain, test_ip)
            checks.append({
                "name": "DNS write",
                "ok": write_ok,
                "detail": "Write probe passed" if write_ok else "Could not write a test record — check permissions",
                "detail_code": "write_ok" if write_ok else "write_denied",
                "blocking": not write_ok,
            })

        overall_ok = all(c["ok"] for c in checks if c.get("blocking"))
        return {"ok": overall_ok, "checks": checks, "warnings": warnings}

    def _a_records(self, zone: str, domain: str, whole_zone: bool) -> list[dict] | None:
        """The enabled A records of *domain* in *zone* (or of the whole zone), or None."""
        r = self._api(
            "/api/zones/records/get",
            {"domain": domain, "zone": zone, "listZone": "true" if whole_zone else "false"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != "ok":
            return None
        records: list[dict] = []
        for rec in data["response"].get("records", []):
            if rec.get("type") == "A" and not rec.get("isDisabled"):
                ip = rec.get("rData", {}).get("ipAddress", "")
                if ip:
                    records.append({"domain": rec["name"], "answer": ip})
        return records

    def list_rewrites(self) -> list[dict] | None:
        """Every A record, or None when any part of the list could not be read.

        A partial list is not an inventory. The push does not read this list: it asks
        `lookup_rewrites` about the one name it writes.
        """
        if not self._ensure_token():
            return None
        try:
            zones = self._list_zones()
            if zones is None:
                return None
            records: list[dict] = []
            for zone in zones:
                found = self._a_records(zone, zone, whole_zone=True)
                if found is None:
                    return None
                records.extend(found)
            return records
        except (requests.RequestException, KeyError, ValueError, TypeError, AttributeError):
            return None

    def lookup_rewrites(self, name: str) -> list[dict] | None:
        """The A records of *name*, read from its own zone only.

        A zone this account may not read, or a forwarder zone that answers an error, has
        nothing to do with the name being pushed and no longer blocks it.
        """
        if not self._ensure_token():
            return None
        try:
            zones = self._list_zones()
            if zones is None:
                return None
            zone = self._find_zone(name, zones)
            if zone is None:
                return []  # no zone holds the name: there is no record to see
            wanted = (name or "").lower().rstrip(".")
            found = self._a_records(zone, wanted, whole_zone=False)
            if found is None:
                return None
            return [r for r in found if str(r["domain"]).lower().rstrip(".") == wanted]
        except (requests.RequestException, KeyError, ValueError, TypeError, AttributeError):
            return None

    def add_rewrite(self, domain: str, ip: str) -> bool:
        if not self._ensure_token():
            return False
        try:
            zone = self._write_zone(domain)
            if zone is None:
                return False
            r = self._api(
                "/api/zones/records/add",
                {
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
        except (requests.RequestException, ValueError, AttributeError):
            pass
        return False

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        if not self._ensure_token():
            return False
        try:
            zone = self._write_zone(domain)
            if zone is None:
                return False
            r = self._api(
                "/api/zones/records/delete",
                {
                    "zone": zone,
                    "domain": domain,
                    "type": "A",
                    "ipAddress": ip,
                },
            )
            if r.status_code == 200:
                return r.json().get("status") == "ok"
        except (requests.RequestException, ValueError, AttributeError):
            pass
        return False
