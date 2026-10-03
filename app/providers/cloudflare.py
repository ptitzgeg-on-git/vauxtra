"""Cloudflare DNS provider: A, AAAA and CNAME records through the official API.

Columns: username = zone ID (optional, auto-detected per domain), password = API token
(needs Zone:DNS:Edit), url ignored, extra = JSON {"proxied": bool}, the default orange
cloud for new address records.
"""

from __future__ import annotations

import ipaddress

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import DNSProvider
from app.text import plural

try:
    import cloudflare as _cf
    _HAS_CF = True
except ImportError:
    _cf = None
    _HAS_CF = False


class CloudflareProvider(DNSProvider):

    def __init__(self, url: str, zone_id: str, api_token: str, extra: dict | None = None):
        if not _HAS_CF:
            raise RuntimeError(
                "Package 'cloudflare' not installed — rebuild the Docker image."
            )
        self._configured_zone_id = zone_id.strip() if zone_id else ""
        self._zone_cache: dict[str, str] = {}  # domain → zone_id (per-domain cache)
        self._api_token = (api_token or "").strip()
        # The SDK defaults to a 60 s read timeout with two retries; bound it like other calls.
        self._client = _cf.Cloudflare(api_token=api_token, timeout=PROVIDER_TIMEOUT)
        self._api_url = "https://api.cloudflare.com/client/v4"
        self._proxied = bool((extra or {}).get("proxied", False))
        # name → (is a CNAME, orange cloud) of each record `delete_rewrite` removed, so that
        # the record `add_rewrite` then puts back under that name keeps its flag.
        self._removed: dict[str, tuple[bool, bool]] = {}

    def _api_request(self, method: str, path: str, **kwargs) -> dict:
        headers = kwargs.pop("headers", {}) or {}
        headers.update({"Authorization": f"Bearer {self._api_token}"})
        try:
            resp = requests.request(
                method,
                f"{self._api_url}{path}",
                headers=headers,
                timeout=PROVIDER_TIMEOUT,
                **kwargs,
            )
            status = resp.status_code
            try:
                payload = resp.json()
            except Exception:
                payload = {"success": False, "errors": [{"message": resp.text[:200] or "Unknown error"}]}

            ok = bool(resp.ok and isinstance(payload, dict) and payload.get("success", False))
            result = payload.get("result") if isinstance(payload, dict) else None
            errors = payload.get("errors") if isinstance(payload, dict) else None
            return {
                "ok": ok,
                "status": status,
                "result": result,
                "errors": errors if isinstance(errors, list) else [],
            }
        except Exception as e:
            return {
                "ok": False,
                "status": None,
                "result": None,
                "errors": [{"message": str(e)}],
            }


    def _find_zone(self, domain: str, *, strict: bool = False) -> str | None:
        """Return the zone ID for `domain`, from configuration or by lookup, or None.

        A failed lookup also returns None (writes then return False); `strict` raises
        instead, so `records_for` does not report "no records" for an unanswered question.
        """
        if self._configured_zone_id:
            return self._configured_zone_id
        # Check per-domain cache
        if domain in self._zone_cache:
            return self._zone_cache[domain]
        # Walk up the label hierarchy: sub.example.com → example.com
        parts = domain.rstrip(".").split(".")
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in self._zone_cache:
                self._zone_cache[domain] = self._zone_cache[candidate]
                return self._zone_cache[domain]
            try:
                for zone in self._client.zones.list(name=candidate, per_page=1):
                    # The `name` filter may match by prefix; verify it before caching the id.
                    if not self._same_name(zone.name, candidate):
                        continue
                    self._zone_cache[domain] = zone.id
                    self._zone_cache[candidate] = zone.id
                    return zone.id
            except Exception:
                if strict:
                    raise
                # Zone lookup failed for this candidate; try next subdomain level
                pass
        return None

    @staticmethod
    def _same_name(record_name: str, domain: str) -> bool:
        """Compare two DNS names, ignoring case and the root dot."""
        return (record_name or "").strip(".").lower() == (domain or "").strip(".").lower()

    @staticmethod
    def _is_ip(value: str) -> bool:
        """Return True if value is a valid IPv4 or IPv6 address."""
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    def _record_type(self, value: str) -> str:
        """Return 'A' for IPs, 'AAAA' for IPv6, 'CNAME' for hostnames."""
        try:
            addr = ipaddress.ip_address(value)
            return "AAAA" if addr.version == 6 else "A"
        except ValueError:
            return "CNAME"


    def test_connection(self) -> bool:
        try:
            # Fetching the first zone verifies credentials + network
            for _ in self._client.zones.list(per_page=1):
                break
            return True
        except Exception:
            return False

    def list_rewrites(self) -> list[dict]:
        """Return every A, AAAA and CNAME record in every zone the token can reach.

        Each record carries the `zone` it was read from. API errors propagate: a partial
        list would be read by callers as "record absent".
        """
        zones: list[tuple[str, str]] = []
        if self._configured_zone_id:
            zones = [(self._configured_zone_id, self._zone_name(self._configured_zone_id))]
        else:
            # Discover all visible zones
            for zone in self._client.zones.list(per_page=50):
                zones.append((zone.id, self._clean_zone_name(zone.name)))
        results: list[dict] = []
        for zid, zone_name in zones:
            for rtype in ("A", "AAAA", "CNAME"):
                for r in self._client.dns.records.list(zone_id=zid, type=rtype):
                    results.append(
                        {
                            "domain": r.name,
                            "answer": r.content,
                            "type": rtype,
                            "proxied": r.proxied,
                            "zone": zone_name,
                        }
                    )
        return results

    def records_for(self, domain: str) -> list[dict]:
        """Return the A, AAAA and CNAME records named exactly `domain`.

        One zone lookup and one name-filtered listing, instead of reading every zone.
        """
        wanted = (domain or "").strip().strip(".").lower()
        if not wanted:
            return []
        zone_id = self._find_zone(wanted, strict=True)
        if not zone_id:
            return []
        return [
            {"domain": r.name, "answer": r.content, "type": r.type, "proxied": r.proxied}
            for r in self._client.dns.records.list(zone_id=zone_id, name={"exact": wanted})
            if r.type in ("A", "AAAA", "CNAME") and self._same_name(r.name, wanted)
        ]

    @staticmethod
    def _clean_zone_name(name) -> str:
        """A zone name as the scan compares it, or "" for anything that is not one."""
        return name.strip(".").lower() if isinstance(name, str) else ""

    def _zone_name(self, zone_id: str) -> str:
        """Return the configured zone's name, or "" if the token cannot read it.

        Only a label: the scan can do without it, so a failure is not raised.
        """
        try:
            zone = self._client.zones.get(zone_id=zone_id)
        except Exception:
            return ""
        return self._clean_zone_name(getattr(zone, "name", None))

    @staticmethod
    def _is_tunnel_target(value: str) -> bool:
        """True for `<tunnel id>.cfargotunnel.com`, which only resolves when proxied."""
        return (value or "").strip().strip(".").lower().endswith(".cfargotunnel.com")

    @staticmethod
    def _name_key(domain: str) -> str:
        return (domain or "").strip().strip(".").lower()

    def add_rewrite(self, domain: str, ip: str, *, proxied: bool | None = None) -> bool:
        """Point `domain` at `ip`, creating the record or updating the existing one.

        An existing record keeps its proxied flag. A new one takes `proxied` if given, else
        the flag of a record of the same kind just removed under that name (push corrects
        drift by delete then add), else the integration default. A CNAME to a tunnel is
        always proxied, since it does not resolve otherwise.
        """
        zone_id = self._find_zone(domain)
        if not zone_id:
            return False
        rtype = self._record_type(ip)
        tunnel = rtype == "CNAME" and self._is_tunnel_target(ip)
        try:
            # Upsert: check if a matching record already exists
            for record in self._client.dns.records.list(
                zone_id=zone_id, name={"exact": domain}, type=rtype
            ):
                if not self._same_name(record.name, domain):
                    # The `name` filter may not be exact depending on the API version
                    # (cloudflare is pinned below 5); this check keeps it harmless.
                    continue
                if record.content == ip and (bool(record.proxied) or not tunnel):
                    return True  # already exists with same content
                # Exists with different content, or a tunnel CNAME left grey → update it
                keep = bool(record.proxied) if proxied is None else proxied
                self._client.dns.records.update(
                    dns_record_id=record.id,
                    zone_id=zone_id,
                    name=domain,
                    type=rtype,
                    content=ip,
                    ttl=1,
                    proxied=tunnel or keep,
                )
                return True
            # No existing record → create
            if proxied is None:
                removed = self._removed.get(self._name_key(domain))
                if removed and removed[0] == (rtype == "CNAME"):
                    proxied = removed[1]
            if proxied is None:
                # Any other CNAME is created grey: a proxied one pointing at a name on
                # another Cloudflare account answers error 1014.
                proxied = self._proxied if rtype != "CNAME" else False
            self._client.dns.records.create(
                zone_id=zone_id,
                name=domain,
                type=rtype,
                content=ip,
                ttl=1,
                proxied=tunnel or proxied,
            )
            return True
        except Exception:
            return False

    def _proxied_of(self, domain: str, ip: str) -> bool | None:
        """The orange cloud of the record `domain` → `ip`, or None when none is read."""
        zone_id = self._find_zone(domain)
        if not zone_id:
            return None
        try:
            for record in self._client.dns.records.list(
                zone_id=zone_id, name={"exact": domain}, type=self._record_type(ip)
            ):
                if self._same_name(record.name, domain) and record.content == ip:
                    return bool(record.proxied)
        except Exception:
            return None
        return None

    def update_rewrite(self, old_domain: str, old_ip: str, new_domain: str, new_ip: str) -> bool:
        """Move a record, carrying its proxied flag to the new name.

        The flag only carries between records of the same kind (address or CNAME).
        """
        if self._same_name(old_domain, new_domain):
            return super().update_rewrite(old_domain, old_ip, new_domain, new_ip)
        carried = None
        if (self._record_type(old_ip) == "CNAME") == (self._record_type(new_ip) == "CNAME"):
            carried = self._proxied_of(old_domain, old_ip)
        if not self.add_rewrite(new_domain, new_ip, proxied=carried):
            return False
        self.delete_rewrite(old_domain, old_ip)  # a failed removal answers True, as inherited
        return True

    def delete_rewrite(self, domain: str, ip: str) -> bool:
        zone_id = self._find_zone(domain)
        if not zone_id:
            return False
        rtype = self._record_type(ip)
        try:
            for record in self._client.dns.records.list(
                zone_id=zone_id, name={"exact": domain}, type=rtype
            ):
                if not self._same_name(record.name, domain):
                    # Same non-exact `name` filter as in add_rewrite.
                    continue
                if record.content == ip:
                    self._client.dns.records.delete(
                        dns_record_id=record.id, zone_id=zone_id
                    )
                    self._removed[self._name_key(domain)] = (rtype == "CNAME", bool(record.proxied))
                    return True
            return False
        except Exception:
            return False


    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        del write_probe

        checks: list[dict] = []

        # `code` becomes detail_code (i18n key under providers.diag.detail). `skipped` marks
        # the write probe this provider never runs: not a warning, but ok stays False.
        def _add(
            name: str,
            ok: bool,
            detail: str,
            blocking: bool = True,
            code: str = "",
            skipped: bool = False,
            **params,
        ) -> None:
            entry = {"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking}
            if code:
                entry["detail_code"] = code
            if params:
                entry["detail_params"] = params
            if skipped:
                entry["skipped"] = True
            checks.append(entry)

        # 1. Verify token is active
        verify = self._api_request("GET", "/user/tokens/verify")
        _add(
            "token_verify",
            verify["ok"],
            "API token is active" if verify["ok"] else "Token verification failed",
            True,
            code="token_active" if verify["ok"] else "token_invalid",
        )

        # 2. Check zone access
        zone_source = "configured_zone_id"
        zone_id = self._configured_zone_id
        if not zone_id and hostname_hint:
            zone_id = self._find_zone(hostname_hint)
            zone_source = "hostname_hint_lookup"

        if not zone_id:
            # No specific zone configured - check if we can list any zones
            zones_resp = self._api_request("GET", "/zones", params={"per_page": 5})
            zones_list = zones_resp.get("result") or []
            zone_count = len(zones_list) if isinstance(zones_list, list) else 0

            if zones_resp["ok"] and zone_count > 0:
                _add(
                    "zones_access",
                    True,
                    f"Can access {plural(zone_count, 'zone')} via token - auto-detection will work",
                    False,
                    code="zones_listed",
                    count=zone_count,
                )
            elif zones_resp["ok"]:
                _add(
                    "zones_access",
                    False,
                    "Token valid but no zones accessible - check token permissions",
                    True,
                    code="zones_empty",
                )
            else:
                _add(
                    "zones_access",
                    False,
                    "Cannot list zones - check token has Zone:Read permission",
                    True,
                    code="zones_denied",
                )
        else:
            zone = self._api_request("GET", f"/zones/{zone_id}")
            _add(
                "zone_read",
                zone["ok"],
                f"Zone is readable ({zone_source})" if zone["ok"] else f"Cannot read zone ({zone_source})",
                True,
                code="zone_readable" if zone["ok"] else "zone_unreadable",
                source=zone_source,
            )

            if zone["ok"]:
                dns_read = self._api_request(
                    "GET",
                    f"/zones/{zone_id}/dns_records",
                    params={"per_page": 1, "type": "A"},
                )
                _add(
                    "dns_read",
                    dns_read["ok"],
                    "Can list DNS records" if dns_read["ok"] else "Cannot list DNS records",
                    True,
                    code="dns_read_ok" if dns_read["ok"] else "dns_read_failed",
                )

                _add(
                    "dns_write",
                    False,
                    "DNS write probe skipped (non-destructive mode). Actual write is validated at runtime on first change.",
                    False,
                    code="dns_write_skipped",
                    skipped=True,
                )

        blocking_failures = [c for c in checks if c["blocking"] and not c["ok"]]
        warnings = [c["detail"] for c in checks if not c["blocking"] and not c["ok"] and not c.get("skipped")]
        return {
            "ok": len(blocking_failures) == 0,
            "checks": checks,
            "warnings": warnings,
        }

    def health_status(self) -> dict:
        zones_total = 0
        try:
            zones = list(self._client.zones.list(per_page=5))
            zones_total = len(zones)
            ok = True
        except Exception:
            ok = False

        return {
            "ok": ok,
            "status": "healthy" if ok else "down",
            "zones_visible": zones_total,
        }
