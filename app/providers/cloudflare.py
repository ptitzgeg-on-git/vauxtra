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

from app.providers.base import DNSProvider
from app.config import PROVIDER_TIMEOUT

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
        self._client = _cf.Cloudflare(api_token=api_token)
        self._api_url = "https://api.cloudflare.com/client/v4"
        self._proxied = bool((extra or {}).get("proxied", False))

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
                    self._zone_cache[domain] = zone.id
                    self._zone_cache[candidate] = zone.id
                    return zone.id
            except Exception:
                # Zone lookup failed for this candidate; try next subdomain level
                pass
        return None

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

    def list_rewrites(self) -> list[dict]:
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
            # API error during listing; return partial results collected so far
            pass
        return results

    def add_rewrite(self, domain: str, ip: str) -> bool:
        zone_id = self._find_zone(domain)
        if not zone_id:
            return False
        rtype = self._record_type(ip)
        # CNAME records must not be proxied (Cloudflare error 1014 risk)
        proxied = self._proxied if rtype != "CNAME" else False
        try:
            # Upsert: check if a matching record already exists
            for record in self._client.dns.records.list(
                zone_id=zone_id, name=domain, type=rtype
            ):
                if record.content == ip:
                    return True  # already exists with same content
                # Exists with different content → update it
                self._client.dns.records.update(
                    dns_record_id=record.id,
                    zone_id=zone_id,
                    name=domain,
                    type=rtype,
                    content=ip,
                    ttl=1,
                    proxied=proxied,
                )
                return True
            # No existing record → create
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
                zone_id=zone_id, name=domain, type=rtype
            ):
                if record.content == ip:
                    self._client.dns.records.delete(
                        dns_record_id=record.id, zone_id=zone_id
                    )
                    return True
            return False
        except Exception:
            return False

    # ── Diagnostics helpers ───────────────────────────────────────────────

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        del write_probe

        checks: list[dict] = []

        def _add(name: str, ok: bool, detail: str, blocking: bool = True) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking})

        # 1. Verify token is active
        verify = self._api_request("GET", "/user/tokens/verify")
        _add(
            "token_verify",
            verify["ok"],
            "API token is active" if verify["ok"] else "Token verification failed",
            True,
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
                )
            elif zones_resp["ok"]:
                _add(
                    "zones_access",
                    False,
                    "Token valid but no zones accessible - check token permissions",
                    True,
                )
            else:
                _add(
                    "zones_access",
                    False,
                    "Cannot list zones - check token has Zone:Read permission",
                    True,
                )
        else:
            zone = self._api_request("GET", f"/zones/{zone_id}")
            _add(
                "zone_read",
                zone["ok"],
                f"Zone is readable ({zone_source})" if zone["ok"] else f"Cannot read zone ({zone_source})",
                True,
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
                )

                _add(
                    "dns_write",
                    False,
                    "DNS write probe skipped (non-destructive mode). Actual write is validated at runtime on first change.",
                    False,
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
