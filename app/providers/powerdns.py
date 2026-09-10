"""PowerDNS Authoritative Server provider — zone records via the HTTP API.

Storage convention (DB columns):
  url      → API base, e.g. http://192.168.1.10:8081
  username → server id (optional — defaults to "localhost")
  password → API key, sent as `X-API-Key`

PowerDNS models a record set, not a record: one `(name, type)` pair holds *all* its
values at once, and `changetype: REPLACE` deletes every existing value before writing
the ones it is given. Adding an address to a name that already has one is therefore a
read-modify-write, never a blind PATCH -- see `_write_rrset`.
"""

from __future__ import annotations

import ipaddress

import requests

from app.providers.base import DNSProvider, TimeoutSession

DEFAULT_SERVER_ID = "localhost"
DEFAULT_TTL = 3600
MANAGED_TYPES = ("A", "AAAA", "CNAME")


class PowerDNSProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url = (url or "").rstrip("/")
        self.server_id = (username or "").strip() or DEFAULT_SERVER_ID
        self.session = TimeoutSession()
        self.session.headers["X-API-Key"] = (password or "").strip()
        self.session.headers["Accept"] = "application/json"

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _api(self, path: str) -> str:
        return f"{self.url}/api/v1/servers/{self.server_id}{path}"

    @staticmethod
    def _fqdn(name: str) -> str:
        """PowerDNS names are absolute: every name it is given carries the root dot."""
        cleaned = (name or "").strip().rstrip(".")
        return f"{cleaned}." if cleaned else "."

    @staticmethod
    def _relative(name: str) -> str:
        """The inverse of `_fqdn`, for the names Vauxtra stores and compares."""
        return (name or "").strip().rstrip(".").lower()

    @staticmethod
    def _record_type(value: str) -> str:
        """'A' for IPv4, 'AAAA' for IPv6, 'CNAME' for anything else."""
        try:
            return "AAAA" if ipaddress.ip_address(value).version == 6 else "A"
        except ValueError:
            return "CNAME"

    @classmethod
    def _content(cls, rtype: str, value: str) -> str:
        """The wire form of a value: a CNAME target is a name, so it is absolute too."""
        cleaned = (value or "").strip()
        if rtype != "CNAME":
            return cleaned
        # `_fqdn` answers "." for a blank name, and "." is the DNS root -- a perfectly
        # writable CNAME target. A blank value must stay blank so the callers refuse it
        # instead of pointing the name at the root.
        return cls._fqdn(cleaned) if cleaned else ""

    # ── Zones ─────────────────────────────────────────────────────────────

    def _list_zones(self) -> list[dict]:
        try:
            r = self.session.get(self._api("/zones"))
            if r.status_code != 200:
                return []
            data = r.json()
            return [z for z in data if isinstance(z, dict) and z.get("name")] if isinstance(data, list) else []
        except (requests.RequestException, ValueError):
            return []

    def _zone_id(self, zone: dict) -> str:
        """The zone's own id when it has one, its name otherwise.

        `id` is the canonical handle and already carries the root dot, but it is also
        URL-escaped for zones whose name contains a slash. Falling back to the name keeps
        the provider working against an API version that omits `id` from the list view.
        """
        return str(zone.get("id") or self._fqdn(zone.get("name", "")))

    def _find_zone(self, domain: str) -> str | None:
        """The id of the longest zone that contains *domain*, or None."""
        target = self._relative(domain)
        best: tuple[int, str] | None = None
        for zone in self._list_zones():
            name = self._relative(zone.get("name", ""))
            if not name:
                continue
            covers = target == name or target.endswith("." + name)
            if covers and (best is None or len(name) > best[0]):
                best = (len(name), self._zone_id(zone))
        return best[1] if best else None

    def _zone_rrsets(self, zone_id: str) -> list[dict]:
        try:
            r = self.session.get(f"{self._api('/zones')}/{zone_id}")
            if r.status_code != 200:
                return []
            data = r.json()
            rrsets = data.get("rrsets") if isinstance(data, dict) else None
            return [s for s in rrsets if isinstance(s, dict)] if isinstance(rrsets, list) else []
        except (requests.RequestException, ValueError):
            return []

    def _find_rrset(self, zone_id: str, name: str, rtype: str) -> dict | None:
        wanted = self._relative(name)
        for rrset in self._zone_rrsets(zone_id):
            if rrset.get("type") == rtype and self._relative(rrset.get("name", "")) == wanted:
                return rrset
        return None

    # ── Writes ────────────────────────────────────────────────────────────

    def _patch(self, zone_id: str, rrset: dict) -> bool:
        try:
            r = self.session.patch(
                f"{self._api('/zones')}/{zone_id}",
                json={"rrsets": [rrset]},
            )
            # A successful PATCH answers 204 No Content; 200 is accepted for the
            # proxies that rewrite it, and nothing else is a success.
            return r.status_code in (200, 204)
        except requests.RequestException:
            return False

    def _write_rrset(self, zone_id: str, name: str, rtype: str, contents: list[str], ttl: int) -> bool:
        """Replace the `(name, type)` record set with *contents*, or delete it when empty.

        `REPLACE` with an empty `records` list is not a delete -- it is a validation
        error on some versions and an empty RRset on others -- so the empty case gets
        its own changetype.
        """
        if not contents:
            return self._patch(zone_id, {
                "name": self._fqdn(name),
                "type": rtype,
                "changetype": "DELETE",
            })
        return self._patch(zone_id, {
            "name": self._fqdn(name),
            "type": rtype,
            "ttl": ttl,
            "changetype": "REPLACE",
            "records": [{"content": c, "disabled": False} for c in contents],
        })

    # ── DNSProvider interface ─────────────────────────────────────────────

    def test_connection(self) -> bool:
        if not self.url:
            return False
        try:
            r = self.session.get(self._api(""))
            if r.status_code != 200:
                return False
            data = r.json()
            return isinstance(data, dict) and bool(data.get("id") or data.get("type"))
        except (requests.RequestException, ValueError):
            return False

    def list_rewrites(self) -> list[dict]:
        records: list[dict] = []
        for zone in self._list_zones():
            for rrset in self._zone_rrsets(self._zone_id(zone)):
                rtype = rrset.get("type")
                if rtype not in MANAGED_TYPES:
                    continue
                domain = self._relative(rrset.get("name", ""))
                if not domain:
                    continue
                for record in rrset.get("records") or []:
                    if not isinstance(record, dict) or record.get("disabled"):
                        continue
                    answer = self._relative(record.get("content", "")) if rtype == "CNAME" \
                        else str(record.get("content", "")).strip()
                    if answer:
                        records.append({"domain": domain, "answer": answer, "type": rtype})
        return records

    def add_rewrite(self, domain: str, ip: str) -> bool:
        zone_id = self._find_zone(domain)
        if not zone_id:
            return False
        rtype = self._record_type(ip)
        content = self._content(rtype, ip)
        if not content:
            return False

        existing = self._find_rrset(zone_id, domain, rtype)
        ttl = int(existing.get("ttl") or DEFAULT_TTL) if existing else DEFAULT_TTL
        contents = [
            str(r.get("content", "")).strip()
            for r in (existing.get("records") or []) if isinstance(r, dict)
        ] if existing else []
        contents = [c for c in contents if c]

        if content in contents:
            return True
        if rtype == "CNAME":
            # A name has at most one CNAME, and it excludes every other type. Appending
            # would build an RRset the server rejects, so the value is replaced.
            contents = [content]
        else:
            contents = contents + [content]
        return self._write_rrset(zone_id, domain, rtype, contents, ttl)

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        zone_id = self._find_zone(domain)
        if not zone_id:
            return False
        rtype = self._record_type(ip)
        content = self._content(rtype, ip)

        existing = self._find_rrset(zone_id, domain, rtype)
        if not existing:
            return True  # nothing there; the postcondition already holds
        ttl = int(existing.get("ttl") or DEFAULT_TTL)
        contents = [
            str(r.get("content", "")).strip()
            for r in (existing.get("records") or []) if isinstance(r, dict)
        ]
        remaining = [c for c in contents if c and c != content]
        if len(remaining) == len([c for c in contents if c]):
            return True  # this value was not in the set
        return self._write_rrset(zone_id, domain, rtype, remaining, ttl)

    # ── Diagnostics ───────────────────────────────────────────────────────

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        checks: list[dict] = []
        warnings: list[str] = []

        def _add(name: str, ok: bool, detail: str, code: str, blocking: bool = True, **params) -> None:
            entry = {"name": name, "ok": bool(ok), "detail": detail, "detail_code": code, "blocking": blocking}
            if params:
                entry["detail_params"] = params
            checks.append(entry)

        api_ok = self.test_connection()
        _add(
            "API key",
            api_ok,
            "Authenticated against the PowerDNS API" if api_ok
            else "Cannot reach the API — check the URL, the server id and the API key",
            "login_ok" if api_ok else "login_failed",
        )
        if not api_ok:
            return {"ok": False, "checks": checks, "warnings": warnings}

        zones = self._list_zones()
        _add(
            "List zones",
            True,
            f"{len(zones)} zone(s) accessible" if zones else "No zones found",
            "zones_found" if zones else "zones_none",
            blocking=False,
            count=len(zones),
        )
        if not zones:
            warnings.append("No zones found. Create a zone in PowerDNS before using Vauxtra.")

        if hostname_hint and zones:
            matched = self._find_zone(hostname_hint)
            _add(
                "Zone match",
                bool(matched),
                f"{hostname_hint} falls inside a hosted zone" if matched
                else f"No hosted zone covers {hostname_hint}",
                "zone_match" if matched else "zone_missing",
                blocking=False,
                host=hostname_hint,
            )
            if not matched:
                warnings.append(f"No zone on this server covers {hostname_hint}.")

        if write_probe and zones:
            probe_zone = self._relative(zones[0].get("name", ""))
            probe_domain = f"_vauxtra-probe.{probe_zone}"
            write_ok = self.add_rewrite(probe_domain, "127.0.0.1")
            if write_ok:
                self.delete_rewrite(probe_domain, "127.0.0.1")
            _add(
                "DNS write",
                write_ok,
                "Write probe passed" if write_ok
                else "Could not write a test record — the API key may be read-only",
                "write_ok" if write_ok else "write_denied",
            )

        return {
            "ok": all(c["ok"] for c in checks if c["blocking"]),
            "checks": checks,
            "warnings": warnings,
        }

    def health_status(self) -> dict:
        ok = self.test_connection()
        return {
            "ok": ok,
            "status": "healthy" if ok else "down",
            "zones_visible": len(self._list_zones()) if ok else 0,
        }
