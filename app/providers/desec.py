"""deSEC provider — public DNS records via the deSEC REST API.

Storage convention (DB columns):
  url      → API base (optional — defaults to https://desec.io/api/v1)
  username → domain name (optional — auto-detected from the account's domains)
  password → API token, sent as `Authorization: Token …`

deSEC exposes a record *set* per `(subname, type)`, and `records` is the whole set:
sending `["10.0.0.1"]` to a name that already answers two addresses drops the other
one, and sending `[]` deletes the set outright. Every write here therefore reads the
set first and merges -- see `_write_records`.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import quote

import requests

from app.providers.base import DNSProvider, TimeoutSession

DEFAULT_API = "https://desec.io/api/v1"
DEFAULT_TTL = 3600
MANAGED_TYPES = ("A", "AAAA", "CNAME")
# deSEC pages at 500 items; the cap is a runaway guard, not a coverage limit.
MAX_PAGES = 20


class DesecProvider(DNSProvider):

    def __init__(self, url: str, username: str, password: str):
        self.url = self._normalize_base(url)
        self._domain_hint = (username or "").strip().rstrip(".").lower()
        self._domains: list[dict] | None = None
        self.session = TimeoutSession()
        self.session.headers["Authorization"] = f"Token {(password or '').strip()}"
        self.session.headers["Accept"] = "application/json"

    @staticmethod
    def _normalize_base(url: str) -> str:
        """Accept `https://desec.io`, `…/api/v1` or a blank field, and answer one form."""
        base = (url or "").strip().rstrip("/")
        if not base:
            return DEFAULT_API
        return base if base.endswith("/api/v1") else f"{base}/api/v1"

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get_all(self, url: str) -> list[dict] | None:
        """Every item of a paginated collection, or None when the API refused.

        The empty list and the failure are different answers: one means the account has
        no records, the other means we do not know. A caller that conflates them reports
        a record as missing when the API merely rate-limited us.
        """
        items: list[dict] = []
        pages = 0
        next_url: str | None = url
        while next_url and pages < MAX_PAGES:
            try:
                r = self.session.get(next_url)
            except requests.RequestException:
                return None
            if r.status_code != 200:
                return None
            try:
                page = r.json()
            except ValueError:
                return None
            if not isinstance(page, list):
                return None
            items.extend(item for item in page if isinstance(item, dict))
            links = getattr(r, "links", None)
            nxt = links.get("next") if isinstance(links, dict) else None
            next_url = nxt.get("url") if isinstance(nxt, dict) else None
            pages += 1
        return items

    # ── Domains ───────────────────────────────────────────────────────────

    def _list_domains(self, refresh: bool = False) -> list[dict] | None:
        if self._domains is not None and not refresh:
            return self._domains
        domains = self._get_all(f"{self.url}/domains/")
        if domains is None:
            return None
        if self._domain_hint:
            # A token may be scoped to one domain while the account holds several; the
            # configured name is what the operator asked Vauxtra to write into.
            scoped = [d for d in domains if str(d.get("name", "")).lower() == self._domain_hint]
            domains = scoped or [{"name": self._domain_hint, "minimum_ttl": DEFAULT_TTL}]
        self._domains = domains
        return domains

    def _find_domain(self, fqdn: str) -> dict | None:
        """The longest account domain that contains *fqdn*."""
        target = (fqdn or "").strip().rstrip(".").lower()
        best: tuple[int, dict] | None = None
        for domain in self._list_domains() or []:
            name = str(domain.get("name", "")).strip().rstrip(".").lower()
            if not name:
                continue
            covers = target == name or target.endswith("." + name)
            if covers and (best is None or len(name) > best[0]):
                best = (len(name), domain)
        return best[1] if best else None

    @staticmethod
    def _subname(fqdn: str, domain_name: str) -> str:
        """The part of *fqdn* below the domain — empty at the apex."""
        target = (fqdn or "").strip().rstrip(".").lower()
        name = (domain_name or "").strip().rstrip(".").lower()
        if target == name:
            return ""
        return target[: -(len(name) + 1)]

    @staticmethod
    def _ttl(domain: dict) -> int:
        """The domain's floor, never below it: a smaller TTL is a 400."""
        try:
            minimum = int(domain.get("minimum_ttl") or 0)
        except (TypeError, ValueError):
            minimum = 0
        return max(DEFAULT_TTL, minimum)

    @staticmethod
    def _record_type(value: str) -> str:
        try:
            return "AAAA" if ipaddress.ip_address(value).version == 6 else "A"
        except ValueError:
            return "CNAME"

    @staticmethod
    def _content(rtype: str, value: str) -> str:
        """A CNAME target is a name, and deSEC only accepts it fully qualified."""
        cleaned = (value or "").strip()
        if rtype != "CNAME":
            return cleaned
        return f"{cleaned.rstrip('.')}." if cleaned else ""

    def _rrset_url(self, domain_name: str, subname: str, rtype: str) -> str:
        # The apex is addressed as `@` in a URL and as the empty string in a payload.
        segment = quote(subname, safe="") if subname else "@"
        return f"{self.url}/domains/{quote(domain_name, safe='')}/rrsets/{segment}/{rtype}/"

    # ── Reads ─────────────────────────────────────────────────────────────

    def _get_rrset(self, domain_name: str, subname: str, rtype: str) -> dict | None | bool:
        """The record set, None when it does not exist, False when the API refused."""
        try:
            r = self.session.get(self._rrset_url(domain_name, subname, rtype))
        except requests.RequestException:
            return False
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except ValueError:
            return False
        return data if isinstance(data, dict) else False

    # ── Writes ────────────────────────────────────────────────────────────

    def _write_records(self, domain_name: str, subname: str, rtype: str,
                       records: list[str], ttl: int, exists: bool) -> bool:
        """Set the record set to *records* — creating, replacing or deleting it."""
        url = self._rrset_url(domain_name, subname, rtype)
        try:
            if not exists:
                if not records:
                    return True
                r = self.session.post(
                    f"{self.url}/domains/{quote(domain_name, safe='')}/rrsets/",
                    json={"subname": subname, "type": rtype, "ttl": ttl, "records": records},
                )
                return r.status_code in (200, 201)
            if not records:
                r = self.session.delete(url)
                # DELETE answers 204 whether or not the set was there.
                return r.status_code in (200, 204)
            r = self.session.patch(url, json={"ttl": ttl, "records": records})
            return r.status_code in (200, 204)
        except requests.RequestException:
            return False

    # ── DNSProvider interface ─────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.url}/domains/")
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_rewrites(self) -> list[dict]:
        records: list[dict] = []
        for domain in self._list_domains(refresh=True) or []:
            name = str(domain.get("name", "")).strip().rstrip(".").lower()
            if not name:
                continue
            rrsets = self._get_all(f"{self.url}/domains/{quote(name, safe='')}/rrsets/")
            for rrset in rrsets or []:
                rtype = rrset.get("type")
                if rtype not in MANAGED_TYPES:
                    continue
                subname = str(rrset.get("subname", "") or "").strip().rstrip(".")
                fqdn = f"{subname}.{name}" if subname else name
                for value in rrset.get("records") or []:
                    answer = str(value or "").strip()
                    if rtype == "CNAME":
                        answer = answer.rstrip(".").lower()
                    if answer:
                        records.append({"domain": fqdn.lower(), "answer": answer, "type": rtype})
        return records

    def add_rewrite(self, domain: str, ip: str) -> bool:
        target = self._find_domain(domain)
        if not target:
            return False
        name = str(target.get("name", "")).strip().rstrip(".").lower()
        subname = self._subname(domain, name)
        rtype = self._record_type(ip)
        content = self._content(rtype, ip)
        if not content:
            return False

        current = self._get_rrset(name, subname, rtype)
        if current is False:
            return False  # unknown state — writing now could drop a sibling record

        existing = [str(v).strip() for v in (current or {}).get("records") or []] if current else []
        existing = [v for v in existing if v]
        if content in existing:
            return True
        # A name carries at most one CNAME, and it excludes every other type, so that
        # set is replaced rather than extended.
        records = [content] if rtype == "CNAME" else existing + [content]
        ttl = int((current or {}).get("ttl") or 0) if current else 0
        return self._write_records(name, subname, rtype, records, ttl or self._ttl(target), bool(current))

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        target = self._find_domain(domain)
        if not target:
            return False
        name = str(target.get("name", "")).strip().rstrip(".").lower()
        subname = self._subname(domain, name)
        rtype = self._record_type(ip)
        content = self._content(rtype, ip)

        current = self._get_rrset(name, subname, rtype)
        if current is False:
            return False
        if not current:
            return True  # nothing there; the postcondition already holds

        existing = [str(v).strip() for v in current.get("records") or []]
        existing = [v for v in existing if v]
        remaining = [v for v in existing if v != content]
        if len(remaining) == len(existing):
            return True  # this value was not in the set
        ttl = int(current.get("ttl") or 0) or self._ttl(target)
        return self._write_records(name, subname, rtype, remaining, ttl, True)

    # ── Diagnostics ───────────────────────────────────────────────────────

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        checks: list[dict] = []
        warnings: list[str] = []

        def _add(name: str, ok: bool, detail: str, code: str, blocking: bool = True, **params) -> None:
            entry = {"name": name, "ok": bool(ok), "detail": detail, "detail_code": code, "blocking": blocking}
            if params:
                entry["detail_params"] = params
            checks.append(entry)

        domains = self._list_domains(refresh=True)
        _add(
            "API token",
            domains is not None,
            "Token accepted by deSEC" if domains is not None
            else "deSEC refused the token — check it has not expired and allows this address",
            "login_ok" if domains is not None else "login_failed",
        )
        if domains is None:
            return {"ok": False, "checks": checks, "warnings": warnings}

        _add(
            "List domains",
            True,
            f"{len(domains)} domain(s) accessible" if domains else "No domains found",
            "zones_found" if domains else "zones_none",
            blocking=False,
            count=len(domains),
        )
        if not domains:
            warnings.append("No domains on this account. Register one at desec.io before using Vauxtra.")

        if hostname_hint and domains:
            matched = self._find_domain(hostname_hint)
            _add(
                "Domain match",
                bool(matched),
                f"{hostname_hint} falls inside a registered domain" if matched
                else f"No registered domain covers {hostname_hint}",
                "zone_match" if matched else "zone_missing",
                blocking=False,
                host=hostname_hint,
            )
            if not matched:
                warnings.append(f"No domain on this account covers {hostname_hint}.")

        if write_probe and domains:
            probe_domain = f"_vauxtra-probe.{str(domains[0].get('name', '')).strip().rstrip('.')}"
            write_ok = self.add_rewrite(probe_domain, "127.0.0.1")
            if write_ok:
                self.delete_rewrite(probe_domain, "127.0.0.1")
            _add(
                "DNS write",
                write_ok,
                "Write probe passed" if write_ok
                else "Could not write a test record — the token may lack write access",
                "write_ok" if write_ok else "write_denied",
            )

        return {
            "ok": all(c["ok"] for c in checks if c["blocking"]),
            "checks": checks,
            "warnings": warnings,
        }

    def health_status(self) -> dict:
        domains = self._list_domains(refresh=True)
        return {
            "ok": domains is not None,
            "status": "healthy" if domains is not None else "down",
            "zones_visible": len(domains) if domains else 0,
        }
