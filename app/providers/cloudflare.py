"""Cloudflare DNS provider — manages A/CNAME records via the official Cloudflare API.

Storage convention (DB columns):
  username → Zone ID   (optional — auto-detected per domain if blank)
  password → API Token (required — needs at least Zone:DNS:Edit permission)
  url      → ignored   (always https://api.cloudflare.com)
  extra    → JSON {"proxied": true/false} (optional, default false): the orange cloud of
             a new A or AAAA record. A record already there keeps its own, and a CNAME to
             a tunnel is always proxied (see `add_rewrite`).
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
        # The SDK's own default is a 60-second read timeout, retried twice: three minutes
        # before one silent Cloudflare call gives up, where every other request this class
        # makes (`_api_request`) stops at `PROVIDER_TIMEOUT`. Nothing upstream bounds it
        # either, so a stalled zone listing held a scan or a health round for that long.
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

    # ── Zone helpers ──────────────────────────────────────────────────────

    def _find_zone(self, domain: str, *, strict: bool = False) -> str | None:
        """Return the zone ID for *domain*, using the configured ID or auto-detecting.

        `None` means no zone was found, and by default a lookup that failed reads the same
        way: a write then answers False, which is what its callers expect. `strict` raises
        instead, for `records_for`, where "no zone" is an answer about the records: read that
        way, a failed lookup would say nobody holds a name nobody was asked about.
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
                    # `name` is a server-side filter whose operator is a documented *prefix*
                    # of the value (`equal` by default, but `contains` and `ends_with` exist).
                    # Caching a zone id that does not belong to this domain would send every
                    # later record into someone else's zone, so the answer is checked.
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
        """Every A, AAAA and CNAME record in every zone this token can reach.

        No handler, deliberately. The sweep used to sit inside one broad `except` that
        passed, with `return results` after it, so an API error partway through handed back
        the records collected so far and the caller had no way to tell a short answer from
        a complete one. What a short answer means at each caller is "that record is not
        there", which is the one thing a failed listing does not establish.

        Every caller had already written the honest branch. The two record routes answer
        502, the drift check raises a `dns_check_failed` issue, the scan logs the provider
        that failed, and the removal path falls back to the address Vauxtra stored. The
        handler is what made all five unreachable.

        The one tolerant reading, a zone this token can list but not read, is what PowerDNS
        keeps on purpose and explains over `_zone_rrsets`. It keeps it because it can tell
        that case apart from a listing that failed; one handler wrapped around the whole
        sweep cannot, so it read every failure as the harmless one.

        Each record names the zone it was read from (`zone`). A token scoped to every zone of
        an account reaches every zone on that account: measured in production on
        2026-09-22, one scan listed 59 routes in twelve zones nobody had declared, beside the
        32 in the one that was. The scan sorts them by that name, and the import splits a
        record's name at it rather than at its first dot.
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
        """The A, AAAA and CNAME records named exactly `domain`, from the zone that holds it.

        The inherited answer filters `list_rewrites`, which lists the zones the token reaches
        and reads each one three record types at a time: three calls a zone and one more, for
        one name, each time a drift drawer opens. The production token of 2026-09-22 read
        records from thirteen zones, so forty calls at least. This is the zone lookup and one
        listing that Cloudflare filters by name.
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
        """The name of the configured zone, or "" when the token may not read it.

        The one handler of the listing, and it guards a label, not a record: the scan falls
        back to splitting the record's name without it, as it always did. Failing the whole
        listing over it would hide every record of the zone to protect a heading.
        """
        try:
            zone = self._client.zones.get(zone_id=zone_id)
        except Exception:
            return ""
        return self._clean_zone_name(getattr(zone, "name", None))

    @staticmethod
    def _is_tunnel_target(value: str) -> bool:
        """True for `<tunnel id>.cfargotunnel.com`, the name a Cloudflare Tunnel answers on.

        That name only resolves inside Cloudflare's proxy. A record pointing at it that is
        not proxied answers a CNAME and no address: measured in production on 2026-09-23,
        through two public resolvers, on a test name this class had just created, while the
        proxied record beside it, same target, answered two addresses.
        """
        return (value or "").strip().strip(".").lower().endswith(".cfargotunnel.com")

    @staticmethod
    def _name_key(domain: str) -> str:
        return (domain or "").strip().strip(".").lower()

    def add_rewrite(self, domain: str, ip: str, *, proxied: bool | None = None) -> bool:
        """Point `domain` at `ip`, creating the record or changing the one already there.

        The orange cloud is the operator's setting, and a change of address does not change
        it. It used to be rewritten from the integration's default, which is off, so moving
        a proxied name to a new address turned it grey and put the origin's address in
        public DNS. A record already there keeps its own flag. A new one takes, in this
        order: the `proxied` its caller passes (`update_rewrite`, for the record it moves);
        the flag of the record this instance just removed under the same name, when both
        are addresses or both are CNAMEs (the push path corrects a drift by removing, then
        adding); the integration's default.

        One flag is not a choice: a CNAME to a tunnel only answers when proxied, so it is
        created proxied, and one found grey is turned orange. The rule for every other
        CNAME created it grey, and a grey one cuts its name off.
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
                    # `name` is filtered server-side, and whether it means "equals" or
                    # "contains" belongs to the API version the installed SDK talks to.
                    # `cloudflare` is pinned below 5 for that reason; this check is what
                    # makes the wrong answer harmless rather than destructive.
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
        """The inherited move, carrying the old record's orange cloud to its new name.

        A new address under the same name is an update in place, which keeps the flag by
        itself. A new name is a new record, which the inherited move created from the
        integration's default: a proxied name came back grey after a rename. The flag only
        crosses between two addresses or two CNAMEs, as in `add_rewrite`: an address's
        orange cloud says nothing about a CNAME's target, which a proxy may not reach.
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
                    # `name` is filtered server-side, and whether it means "equals" or
                    # "contains" belongs to the API version the installed SDK talks to.
                    # `cloudflare` is pinned below 5 for that reason; this check is what
                    # makes the wrong answer harmless rather than destructive.
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

    # ── Diagnostics helpers ───────────────────────────────────────────────

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        del write_probe

        checks: list[dict] = []

        # `code` is the short name of the sentence in `detail`; the UI reads
        # `providers.diag.detail.<code>` so the line is not English-only.
        #
        # `skipped` marks a check that was never run: the write probe, which this
        # provider does not attempt at all. See the tunnel provider's `_add` for why it is
        # not a warning, and why `ok` stays False all the same.
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
