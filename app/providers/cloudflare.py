"""Cloudflare DNS provider — manages A/CNAME records via the official Cloudflare API.

Storage convention (DB columns):
  username → Zone ID   (optional — auto-detected per domain if blank)
  password → API Token (required — needs at least Zone:DNS:Edit permission)
  url      → ignored   (always https://api.cloudflare.com)
  extra    → JSON {"proxied": true/false} (optional, default false)
"""

from __future__ import annotations

import ipaddress

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import DNSProvider, record_value_key

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
        # The SDK defaults to a 60 s timeout and two retries: about three minutes for one
        # call, while the caller may hold the database write lock.
        self._client = _cf.Cloudflare(
            api_token=api_token, timeout=PROVIDER_TIMEOUT, max_retries=1
        )
        self._api_url = "https://api.cloudflare.com/client/v4"
        self._proxied = bool((extra or {}).get("proxied", False))

    def close(self) -> None:
        """Release the SDK client's connection pool.

        A provider is built for every request and scheduler pass; each one opened an
        httpx client that nothing closed, and their sockets piled up until collected.
        """
        client = getattr(self, "_client", None)
        self._client = None
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Best effort: a client that cannot close has nothing left to release.
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

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

    # ── Zone helpers ──────────────────────────────────────────────────────

    def _find_zone(self, domain: str) -> str | None:
        """Return the zone ID for *domain*, using the configured ID or auto-detecting."""
        return self._locate_zone(domain)[0]

    def _locate_zone(self, domain: str) -> tuple[str | None, bool]:
        """The zone ID for *domain*, and whether the answer can be trusted.

        `(None, True)` means the account holds no zone for the name; `(None, False)` that
        the lookup failed and nothing is known. `_find_zone` folds both into None, which
        made `lookup_rewrites` call a name outside every zone "unreadable" (a 502) instead
        of "no record" (a 404).
        """
        if self._configured_zone_id:
            return self._configured_zone_id, True
        # Check per-domain cache
        if domain in self._zone_cache:
            return self._zone_cache[domain], True
        # Walk up the label hierarchy: sub.example.com → example.com
        parts = domain.rstrip(".").split(".")
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in self._zone_cache:
                self._zone_cache[domain] = self._zone_cache[candidate]
                return self._zone_cache[domain], True
            try:
                for zone in self._client.zones.list(name=candidate, per_page=1):
                    # `name` is a server-side filter whose operator is a documented *prefix*
                    # of the value (`equal` by default, but `contains` and `ends_with` exist).
                    # Caching a zone id that does not belong to this domain would send every
                    # later record into someone else's zone, so the answer is checked.
                    if not self._same_name(zone.name, candidate):
                        continue
                    self._zone_cache[domain] = zone.id
                    self._zone_cache[candidate] = zone.id
                    return zone.id, True
            except Exception:
                # Only an empty answer means "not this zone, try the parent". A failed
                # lookup of `sub.example.com` says nothing about it: walking up would find
                # `example.com`, cache it for the subdomain and write into the parent zone
                # when `sub.example.com` is a zone of its own.
                return None, False
        return None, True

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

    # ── DNSProvider interface ─────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            # Fetching the first zone verifies credentials + network
            for _ in self._client.zones.list(per_page=1):
                break
            return True
        except Exception:
            return False

    def list_rewrites(self) -> list[dict] | None:
        results: list[dict] = []
        try:
            zone_ids: list[str] = []
            if self._configured_zone_id:
                zone_ids = [self._configured_zone_id]
            else:
                # Discover all visible zones
                for zone in self._client.zones.list(per_page=50):
                    zone_ids.append(zone.id)
            for zid in zone_ids:
                for rtype in ("A", "AAAA", "CNAME"):
                    for r in self._client.dns.records.list(zone_id=zid, type=rtype):
                        results.append({"domain": r.name, "answer": r.content, "type": rtype, "proxied": r.proxied})
        except Exception:
            # C7: a partial list is not an answer. Read as complete, a record the failed
            # page held was taken for absent: the push added a duplicate next to it and the
            # drift reported it missing.
            return None
        return results

    def lookup_rewrites(self, name: str) -> list[dict] | None:
        """The records of *name*, read with an exact-name filter from its own zone only.

        M16: the default walked `list_rewrites`, which reads every zone the token sees when
        no zone is configured: each push listed them all, and one zone the token cannot
        read blocked the push of every service. None when the zone or its records cannot be
        read; an empty list when no zone of the account holds the name, since then the name
        has no record here (a 404 on delete, not a 502).
        """
        zone_id, known = self._locate_zone(name)
        if not zone_id:
            return [] if known else None
        records: list[dict] = []
        try:
            for rtype in ("A", "AAAA", "CNAME"):
                for r in self._client.dns.records.list(zone_id=zone_id, name={"exact": name}, type=rtype):
                    if self._same_name(r.name, name):
                        records.append({"domain": r.name, "answer": r.content, "type": rtype, "proxied": r.proxied})
        except Exception:
            return None
        return records

    def add_rewrite(self, domain: str, ip: str) -> bool:
        zone_id = self._find_zone(domain)
        if not zone_id:
            return False
        rtype = self._record_type(ip)
        # The configured value, CNAMEs included: a proxied CNAME is valid in Cloudflare
        # (error 1014 only concerns a CNAME into another account's zone), and forcing it
        # off exposed the origin of every service published by name.
        proxied = self._proxied
        try:
            # Upsert: check if a matching record already exists. `name` is filtered
            # server-side, and whether it means "equals" or "contains" belongs to the API
            # version the installed SDK talks to. `cloudflare` is held to the 5.x line
            # (`>=5.7,<6`) for that reason; `_same_name` is what makes a wrong answer
            # harmless, not destructive.
            matching = [
                record
                for record in self._client.dns.records.list(
                    zone_id=zone_id, name={"exact": domain}, type=rtype
                )
                if self._same_name(record.name, domain)
            ]
            # M12: with a round-robin set (several records for the name), the first record
            # whose address differed was rewritten -- a legitimate member of the set lost --
            # even when another one already held the wanted address.
            if any(record_value_key(record.content) == record_value_key(ip) for record in matching):
                return True  # already exists with same content
            if len(matching) == 1:
                record = matching[0]
                # Exists with different content → change the address only. `update` is a PUT
                # that replaces the whole record: it reset the TTL and the proxy flag and
                # wiped the operator's comment and tags. `edit` is a PATCH; the SDK requires
                # `name`, `type` and `ttl`, which are sent back unchanged, and `proxied`,
                # `comment` and `tags` are left out so Cloudflare keeps them.
                existing_ttl = getattr(record, "ttl", None)
                self._client.dns.records.edit(
                    dns_record_id=record.id,
                    zone_id=zone_id,
                    name=domain,
                    type=rtype,
                    content=ip,
                    ttl=existing_ttl if isinstance(existing_ttl, int) and existing_ttl > 0 else 1,
                )
                return True
            # No existing record, or a round-robin set left as it is → create
            self._client.dns.records.create(
                zone_id=zone_id,
                name=domain,
                type=rtype,
                content=ip,
                ttl=1,
                proxied=proxied,
            )
            return True
        except Exception:
            return False

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
                    # Same server-side filter as in `add_rewrite`, same guard.
                    continue
                if record_value_key(record.content) == record_value_key(ip):
                    self._client.dns.records.delete(
                        dns_record_id=record.id, zone_id=zone_id
                    )
                    return True
            # Nothing holds that value. False, as before: the push reads the records again
            # before treating that as a failed move (`_move_dns_record` in app/api/sync.py),
            # since `add_rewrite` above may have just edited the record in place.
            return False
        except Exception:
            return False

    # ── Diagnostics helpers ───────────────────────────────────────────────

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        del write_probe

        checks: list[dict] = []

        # `code` is the short name of the sentence in `detail`; the UI reads
        # `providers.diag.detail.<code>` so the line is not English-only.
        def _add(name: str, ok: bool, detail: str, blocking: bool = True, code: str = "", **params) -> None:
            entry = {"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking}
            if code:
                entry["detail_code"] = code
            if params:
                entry["detail_params"] = params
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
                    f"Can access {zone_count} zone(s) via token - auto-detection will work",
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
                )

        blocking_failures = [c for c in checks if c["blocking"] and not c["ok"]]
        warnings = [c["detail"] for c in checks if not c["blocking"] and not c["ok"]]
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
