"""Cloudflare Tunnel provider — manages cloudflared ingress + DNS CNAME records.

Storage convention (DB columns):
  url      → Cloudflare API base (defaults to https://api.cloudflare.com/client/v4)
  username → Account ID (required)
  password → API Token  (requires Account:Cloudflare Tunnel:Edit + Zone:DNS:Edit)
  extra    → JSON object with {"tunnel_id": "<uuid>"}
"""

from __future__ import annotations

import logging
import re
import threading
from urllib.parse import urlparse

from app.config import PROVIDER_TIMEOUT
from app.providers.base import ProxyProvider, TimeoutSession

logger = logging.getLogger(__name__)

_TUNNEL_LOCKS: dict[str, threading.Lock] = {}
_TUNNEL_LOCKS_GUARD = threading.Lock()

# B5: both identifiers are interpolated into API paths. Cloudflare's are a UUID (tunnel) and
# 32 hex digits (account); anything carrying `/`, `?`, `#` or `.` would compose another path
# with the stored token attached, so it is treated as no identifier at all.
_SAFE_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def _safe_id(value) -> str:
    value = str(value or "").strip()
    return value if _SAFE_ID.fullmatch(value) else ""


class CloudflareTunnelProvider(ProxyProvider):
    HOST_ID_IS_HOSTNAME = True

    def __init__(self, url: str, account_id: str, api_token: str, extra: dict | None = None):
        self.account_id = _safe_id(account_id)
        self.tunnel_id = _safe_id((extra or {}).get("tunnel_id", ""))

        base = (url or "").strip().rstrip("/")
        if not base:
            base = "https://api.cloudflare.com/client/v4"
        elif "/client/v4" not in base:
            base = f"{base}/client/v4"
        self.api_url = base

        self.session = TimeoutSession()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            }
        )

    def _request_detailed(self, method: str, path: str, **kwargs) -> dict:
        try:
            r = self.session.request(method, f"{self.api_url}{path}", timeout=PROVIDER_TIMEOUT, **kwargs)
            status = r.status_code
            try:
                payload = r.json()
            except Exception:
                payload = {"success": False, "errors": [{"message": r.text[:200] or "Unknown error"}]}

            ok = bool(r.ok and isinstance(payload, dict) and payload.get("success", False))
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

    def _request(self, method: str, path: str, **kwargs):
        try:
            r = self.session.request(method, f"{self.api_url}{path}", timeout=PROVIDER_TIMEOUT, **kwargs)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, dict) or not payload.get("success", False):
                return None
            return payload.get("result")
        except Exception:
            return None

    def _resolve_tunnel_id(self) -> str:
        if self.tunnel_id:
            return self.tunnel_id
        if not self.account_id:
            return ""

        tunnels = self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel", params={"is_deleted": "false", "per_page": 10})
        if isinstance(tunnels, list) and len(tunnels) == 1:
            candidate = _safe_id(tunnels[0].get("id", "")) if isinstance(tunnels[0], dict) else ""
            if candidate:
                self.tunnel_id = candidate
        elif isinstance(tunnels, list) and len(tunnels) > 1:
            from app.models import add_log
            names = ", ".join(t.get("name", t.get("id", "?")) for t in tunnels[:5])
            add_log("warning", f"Cloudflare: {len(tunnels)} tunnels found ({names}). "
                     "Set tunnel_id in provider extra config to select one.")
        return self.tunnel_id

    def _find_zone(self, hostname: str) -> str:
        labels = (hostname or "").strip(".").split(".")
        if len(labels) < 2:
            return ""

        for i in range(len(labels) - 1):
            candidate = ".".join(labels[i:])
            result = self._request("GET", "/zones", params={"name": candidate, "per_page": 1})
            if not isinstance(result, list):
                # A failed lookup is not an empty answer: walking up to the parent after an
                # error on `sub.example.com` published the CNAME in `example.com` when
                # `sub.example.com` is a zone of its own. Only "no such zone" goes up.
                return ""
            for zone in result:
                # Same reason as the Cloudflare DNS provider: `name` is a filter, not a
                # promise. A zone id that is not this domain's would publish the tunnel
                # CNAME in the wrong zone.
                if str(zone.get("name", "")).strip(".").lower() != candidate.strip(".").lower():
                    continue
                zone_id = str(zone.get("id", "")).strip()
                if zone_id:
                    return zone_id
        return ""

    def _get_configuration(self) -> dict | None:
        """Return the tunnel config, or None when it could not be read.

        The distinction matters: `_request` swallows every error into None, and an
        empty dict here is indistinguishable from a genuinely empty tunnel. Callers
        that rewrite the whole ingress list would then PUT a config containing only
        their own rule -- a transient 502 or a timeout would silently delete every
        other route of the tunnel.
        """
        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            return None
        result = self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations")
        if not isinstance(result, dict):
            return None
        if str(result.get("source") or "").strip().lower() == "local":
            # C8: a locally managed tunnel takes its routes from `config.yml` on the origin.
            # Its `config: null` read as `{}`, and the PUT that followed could switch the
            # tunnel to remote management, dropping every route of that file.
            logger.warning(
                "Tunnel %s is managed locally (config.yml); Vauxtra will not read or rewrite its ingress",
                tunnel_id,
            )
            return None
        if "config" not in result:
            # B6: an answer without the key is not "no configuration yet"; read as `{}`,
            # the next PUT replaced every route of the tunnel with a single one.
            return None
        config = result.get("config")
        if config is None:
            # A tunnel with no configuration yet answers `config: null`. Handing back
            # `result` then made the next PUT send the whole envelope (tunnel_id,
            # version, ...) as if it were the config.
            return {}
        # Anything other than an object is an answer that cannot be edited safely.
        return config if isinstance(config, dict) else None

    def _put_configuration(self, config: dict) -> bool:
        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            return False
        payload = {"config": config}
        result = self._request("PUT", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations", json=payload)
        return isinstance(result, dict)

    def _normalize_ingress(self, ingress_raw) -> list[dict]:
        if not isinstance(ingress_raw, list):
            return []
        ingress: list[dict] = []
        for rule in ingress_raw:
            if isinstance(rule, dict) and rule.get("service"):
                ingress.append(rule)
        return ingress

    def _tunnel_lock(self) -> threading.Lock:
        """One lock per tunnel: the ingress list is read, edited and PUT back whole, so two
        concurrent edits (a push and the scheduler) used to drop each other's rule."""
        key = f"{self.account_id}/{self._resolve_tunnel_id()}"
        with _TUNNEL_LOCKS_GUARD:
            return _TUNNEL_LOCKS.setdefault(key, threading.Lock())

    @staticmethod
    def _split_fallback(ingress: list[dict]) -> tuple[list[dict], dict]:
        """The rules, and the catch-all closing them (kept as the operator set it)."""
        fallback = {"service": "http_status:404"}
        rules: list[dict] = []
        for rule in ingress:
            if not rule.get("hostname") and not rule.get("path"):
                fallback = rule
                continue
            rules.append(rule)
        return rules, fallback

    def _upsert_ingress_rule(self, hostname: str, service_url: str) -> bool:
        with self._tunnel_lock():
            config = self._get_configuration()
            if config is None:
                # Never rewrite an ingress list we failed to read: the PUT replaces it whole.
                return False
            rules, fallback = self._split_fallback(self._normalize_ingress(config.get("ingress")))

            updated: list[dict] = []
            origin_request: dict = {}
            for rule in rules:
                same_host = str(rule.get("hostname", "")).strip().lower() == hostname.lower()
                # Only the host-wide rule is Vauxtra's; `path` rules were added by hand.
                if same_host and not rule.get("path"):
                    if isinstance(rule.get("originRequest"), dict):
                        origin_request = rule["originRequest"]
                    continue
                updated.append(rule)

            updated.append({"hostname": hostname, "service": service_url, "originRequest": origin_request})
            updated.append(fallback)

            config["ingress"] = updated
            return self._put_configuration(config)

    def _delete_ingress_rule(self, hostname: str) -> bool:
        with self._tunnel_lock():
            config = self._get_configuration()
            if config is None:
                return False
            rules, fallback = self._split_fallback(self._normalize_ingress(config.get("ingress")))

            updated: list[dict] = []
            removed = False
            for rule in rules:
                same_host = str(rule.get("hostname", "")).strip().lower() == hostname.lower()
                # Only the host-wide rule is Vauxtra's, as in `_upsert_ingress_rule`: the
                # `path` rules on the same hostname were added by hand and stay.
                if same_host and not rule.get("path"):
                    removed = True
                    continue
                updated.append(rule)

            if not removed:
                # The config WAS read (see the guard above) and the route is genuinely
                # absent: nothing to write, and a delete of an already-absent route is a
                # success. Before the guard, this branch also caught read failures and
                # reported them as successful deletions.
                return True

            updated.append(fallback)
            config["ingress"] = updated
            return self._put_configuration(config)

    def _ensure_dns_record(self, hostname: str) -> bool:
        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            return False

        zone_id = self._find_zone(hostname)
        if not zone_id:
            return False

        cname_target = f"{tunnel_id}.cfargotunnel.com"
        existing = self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"type": "CNAME", "name": hostname},
        )
        if not isinstance(existing, list):
            # B7: a failed listing is not an empty zone; creating on top of it left a
            # second CNAME next to the existing one.
            return False
        # B7: `name` is a filter, not a promise (as in `_find_zone`): only a record that
        # really carries this name may be rewritten.
        records = [
            rec for rec in existing
            if isinstance(rec, dict)
            and str(rec.get("name", hostname)).strip(".").lower() == hostname.strip(".").lower()
        ]

        for rec in records:
            rec_id = str(rec.get("id", "")).strip()
            if not rec_id:
                continue
            if (
                str(rec.get("content", "")).strip().lower() == cname_target.lower()
                and bool(rec.get("proxied"))
            ):
                return True
            # B7: PATCH, not PUT. A PUT replaces the whole record and wiped the comment
            # and tags the operator had set on it.
            updated = self._request(
                "PATCH",
                f"/zones/{zone_id}/dns_records/{rec_id}",
                json={"content": cname_target, "proxied": True},
            )
            return isinstance(updated, dict)

        created = self._request(
            "POST",
            f"/zones/{zone_id}/dns_records",
            json={
                "type": "CNAME",
                "name": hostname,
                "content": cname_target,
                "proxied": True,
                "ttl": 1,
            },
        )
        return isinstance(created, dict)

    def _delete_dns_record(self, hostname: str) -> bool:
        zone_id = self._find_zone(hostname)
        if not zone_id:
            # `_find_zone` returns "" both for "no such zone" and for an API failure;
            # in neither case has the record been removed.
            return False

        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            # Without it every CNAME on the name matched, including ones this tunnel never
            # published: the delete removed the operator's own record.
            return False
        tunnel_target = f"{tunnel_id}.cfargotunnel.com"
        existing = self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"type": "CNAME", "name": hostname},
        )
        if not isinstance(existing, list):
            # `_request` answers None for a network error, for a non-2xx, and for a payload
            # carrying `success: false` alike. Reading that as "the zone holds no such record"
            # made every listing failure return True, so `delete_host` reported the hostname
            # withdrawn while its CNAME was still pointing at the tunnel.
            return False

        ok = True
        for rec in existing:
            if not isinstance(rec, dict):
                continue
            # B7, as in `_ensure_dns_record`: `name` is a filter, not a promise. Without
            # this, a listing that came back unfiltered deleted the CNAME of every hostname
            # this tunnel publishes. Same reading of a missing `name` as there.
            if str(rec.get("name", hostname)).strip(".").lower() != hostname.strip(".").lower():
                continue
            rec_id = str(rec.get("id", "")).strip()
            if not rec_id:
                continue
            content = str(rec.get("content", "")).strip().lower()
            if content != tunnel_target.lower():
                continue
            deleted = self._request("DELETE", f"/zones/{zone_id}/dns_records/{rec_id}")
            ok = ok and (deleted is not None)
        return ok

    # ProxyProvider interface

    def test_connection(self) -> bool:
        if not self.account_id:
            return False
        result = self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel", params={"is_deleted": "false", "per_page": 1})
        if result is None:
            return False
        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            return isinstance(result, list)
        tunnel = self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}")
        return isinstance(tunnel, dict)

    def list_hosts(self) -> list[dict] | None:
        config = self._get_configuration()
        if config is None:
            # C7: unreadable is not empty. `[]` made the push create a second route for a
            # name the tunnel already served, and the drift report "missing".
            return None
        ingress = self._normalize_ingress(config.get("ingress"))

        hosts: list[dict] = []
        for rule in ingress:
            hostname = str(rule.get("hostname", "")).strip().lower()
            service = str(rule.get("service", "")).strip()
            if not hostname or not service or service.startswith("http_status:"):
                continue

            scheme = "http"
            host = ""
            port = 80
            try:
                parsed = urlparse(service)
                scheme = parsed.scheme or "http"
                host = parsed.hostname or ""
                port = parsed.port or (443 if scheme == "https" else 80)
            except Exception:
                # Malformed service URL; use defaults (scheme=http, port=80)
                pass

            hosts.append(
                {
                    "id": hostname,
                    "domains": [hostname],
                    "target": service,
                    "scheme": scheme,
                    "host": host,
                    "port": port,
                    "ssl": scheme == "https",
                    "websocket": False,
                    "cert_id": None,
                }
            )
        return hosts

    def create_host(
        self,
        domain: str,
        ip: str,
        port: int,
        scheme: str = "http",
        websocket: bool = False,
        cert_id: int | None = None,
    ) -> dict | None:
        del websocket, cert_id

        hostname = (domain or "").strip().lower()
        if not hostname:
            return None

        service_url = f"{scheme}://{ip}:{int(port)}"
        if not self._upsert_ingress_rule(hostname, service_url):
            return None
        if not self._ensure_dns_record(hostname):
            return None
        return {"id": hostname, "domain": hostname}

    def update_host(
        self,
        host_id,
        domain: str,
        ip: str,
        port: int,
        scheme: str = "http",
        websocket: bool = False,
        cert_id: int | None = None,
    ) -> bool:
        del websocket, cert_id

        old_hostname = str(host_id or "").strip().lower()
        hostname = (domain or "").strip().lower()
        if not hostname:
            return False

        # New route first, old one after: deleting first left the service unreachable when
        # the publication then failed, and the old deletion's own result was ignored.
        service_url = f"{scheme}://{ip}:{int(port)}"
        if not self._upsert_ingress_rule(hostname, service_url):
            return False
        if not self._ensure_dns_record(hostname):
            return False

        if old_hostname and old_hostname != hostname:
            ingress_removed = self._delete_ingress_rule(old_hostname)
            dns_removed = self._delete_dns_record(old_hostname)
            return ingress_removed and dns_removed
        return True

    def delete_host(self, host_id) -> bool:
        hostname = str(host_id or "").strip().lower()
        if not hostname:
            return False

        ingress_ok = self._delete_ingress_rule(hostname)
        dns_ok = self._delete_dns_record(hostname)
        return ingress_ok and dns_ok

    def get_certificates(self) -> list[dict]:
        return []

    def find_best_certificate(self, domain_suffix: str) -> int | None:
        del domain_suffix
        return None

    # Diagnostics helpers

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        checks: list[dict] = []

        # `code` is the short name of the sentence in `detail`; the UI reads
        # `providers.diag.detail.<code>` so the line is not English-only.
        def _add(name: str, ok: bool, detail: str, blocking: bool = True, code: str = "") -> None:
            entry = {"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking}
            if code:
                entry["detail_code"] = code
            checks.append(entry)

        token_verify = self._request_detailed("GET", "/user/tokens/verify")
        _add(
            "token_verify",
            token_verify["ok"],
            "API token is active" if token_verify["ok"] else "Token verification failed",
            True,
            code="token_active" if token_verify["ok"] else "token_invalid",
        )

        if not self.account_id:
            _add("account_id", False, "Account ID is required", True, code="account_required")
        else:
            tunnel_list = self._request_detailed(
                "GET",
                f"/accounts/{self.account_id}/cfd_tunnel",
                params={"is_deleted": "false", "per_page": 1},
            )
            _add(
                "tunnel_read",
                tunnel_list["ok"],
                "Can list account tunnels" if tunnel_list["ok"] else "Cannot list account tunnels",
                True,
                code="tunnel_list_ok" if tunnel_list["ok"] else "tunnel_list_failed",
            )

        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            _add(
                "tunnel_id",
                False,
                "Tunnel ID is missing or cannot be auto-resolved",
                True,
                code="tunnel_id_missing",
            )
        else:
            tunnel_get = self._request_detailed("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}")
            _add(
                "tunnel_details",
                tunnel_get["ok"],
                "Can read tunnel details" if tunnel_get["ok"] else "Cannot read tunnel details",
                True,
                code="tunnel_read_ok" if tunnel_get["ok"] else "tunnel_read_failed",
            )

            config_get = self._request_detailed(
                "GET",
                f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations",
            )
            _add(
                "tunnel_config_read",
                config_get["ok"],
                "Can read tunnel configuration" if config_get["ok"] else "Cannot read tunnel configuration",
                True,
                code="tunnel_config_read_ok" if config_get["ok"] else "tunnel_config_read_failed",
            )

            if write_probe and config_get["ok"]:
                current_cfg = self._get_configuration()
                write_ok = current_cfg is not None and self._put_configuration(current_cfg)
                _add(
                    "tunnel_config_write",
                    write_ok,
                    "Configuration write probe succeeded" if write_ok else "Configuration write probe failed",
                    True,
                    code="tunnel_config_write_ok" if write_ok else "tunnel_config_write_failed",
                )
            else:
                _add(
                    "tunnel_config_write",
                    False,
                    "Write probe skipped (safe mode).",
                    False,
                    code="tunnel_config_write_skipped",
                )

        hostname = (hostname_hint or "").strip().lower()
        if hostname:
            zone_id = self._find_zone(hostname)
            _add(
                "zone_lookup",
                bool(zone_id),
                "Can resolve DNS zone for hostname" if zone_id else "Cannot resolve DNS zone for hostname",
                False,
                code="zone_lookup_ok" if zone_id else "zone_lookup_failed",
            )
            if zone_id:
                dns_read = self._request_detailed(
                    "GET",
                    f"/zones/{zone_id}/dns_records",
                    params={"type": "CNAME", "name": hostname, "per_page": 1},
                )
                _add(
                    "dns_read",
                    dns_read["ok"],
                    "Can read zone DNS records" if dns_read["ok"] else "Cannot read zone DNS records",
                    False,
                    code="zone_dns_read_ok" if dns_read["ok"] else "zone_dns_read_failed",
                )
        else:
            _add(
                "zone_lookup",
                False,
                "No hostname hint provided for DNS scope checks",
                False,
                code="zone_lookup_no_hint",
            )

        blocking_failures = [c for c in checks if c["blocking"] and not c["ok"]]
        warnings = [c["detail"] for c in checks if not c["blocking"] and not c["ok"]]
        return {
            "ok": len(blocking_failures) == 0,
            "checks": checks,
            "warnings": warnings,
        }

    def health_status(self) -> dict:
        tunnel_id = self._resolve_tunnel_id()
        if not self.account_id:
            return {
                "ok": False,
                "status": "down",
                "reason": "missing_account_id",
                "tunnel_id": tunnel_id,
            }

        if not tunnel_id:
            return {
                "ok": False,
                "status": "inactive",
                "reason": "missing_tunnel_id",
                "tunnel_id": "",
            }

        tunnel_resp = self._request_detailed("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}")
        conn_resp = self._request_detailed("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/connections")

        tunnel = tunnel_resp["result"] if isinstance(tunnel_resp.get("result"), dict) else {}
        clients = conn_resp["result"] if isinstance(conn_resp.get("result"), list) else []

        active_connections = 0
        active_clients = 0
        versions: set[str] = set()
        for client in clients:
            if not isinstance(client, dict):
                continue
            conns = client.get("conns")
            conns_list = conns if isinstance(conns, list) else []
            active = [c for c in conns_list if not bool(c.get("is_pending_reconnect"))]
            if active:
                active_clients += 1
            active_connections += len(active)
            version = str(client.get("version") or client.get("client_version") or "").strip()
            if version:
                versions.add(version)

        declared_status = str(tunnel.get("status") or "").strip().lower()
        if declared_status in {"healthy", "degraded", "down", "inactive"}:
            status = declared_status
        elif active_connections > 0:
            status = "healthy"
        elif conn_resp["ok"]:
            status = "down"
        else:
            status = "unknown"

        ok = status in {"healthy", "degraded"}
        return {
            "ok": ok,
            "status": status,
            "tunnel_id": tunnel_id,
            "active_connections": active_connections,
            "active_clients": active_clients,
            "cloudflared_versions": sorted(versions),
            "declared_status": declared_status or None,
        }
