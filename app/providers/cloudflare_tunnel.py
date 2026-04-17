"""Cloudflare Tunnel provider — manages cloudflared ingress + DNS CNAME records.

Storage convention (DB columns):
  url      → Cloudflare API base (defaults to https://api.cloudflare.com/client/v4)
  username → Account ID (required)
  password → API Token  (requires Account:Cloudflare Tunnel:Edit + Zone:DNS:Edit)
  extra    → JSON object with {"tunnel_id": "<uuid>"}
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import ProxyProvider


class CloudflareTunnelProvider(ProxyProvider):
    def __init__(self, url: str, account_id: str, api_token: str, extra: dict | None = None):
        self.account_id = (account_id or "").strip()
        self.tunnel_id = str((extra or {}).get("tunnel_id", "")).strip()

        base = (url or "").strip().rstrip("/")
        if not base:
            base = "https://api.cloudflare.com/client/v4"
        elif "/client/v4" not in base:
            base = f"{base}/client/v4"
        self.api_url = base

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            }
        )
        self.session.timeout = PROVIDER_TIMEOUT

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
            candidate = str(tunnels[0].get("id", "")).strip()
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
            if isinstance(result, list) and result:
                zone_id = str(result[0].get("id", "")).strip()
                if zone_id:
                    return zone_id
        return ""

    def _get_configuration(self) -> dict:
        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            return {}
        result = self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations")
        if not isinstance(result, dict):
            return {}
        config = result.get("config")
        if isinstance(config, dict):
            return config
        return result

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

    def _upsert_ingress_rule(self, hostname: str, service_url: str) -> bool:
        config = self._get_configuration()
        ingress = self._normalize_ingress(config.get("ingress"))

        updated: list[dict] = []
        for rule in ingress:
            if str(rule.get("hostname", "")).strip().lower() == hostname.lower():
                continue
            # Drop fallback to re-append exactly one rule at the end.
            if not rule.get("hostname") and str(rule.get("service", "")).startswith("http_status:"):
                continue
            updated.append(rule)

        updated.append({"hostname": hostname, "service": service_url, "originRequest": {}})
        updated.append({"service": "http_status:404"})

        config["ingress"] = updated
        return self._put_configuration(config)

    def _delete_ingress_rule(self, hostname: str) -> bool:
        config = self._get_configuration()
        ingress = self._normalize_ingress(config.get("ingress"))

        updated: list[dict] = []
        removed = False
        for rule in ingress:
            if str(rule.get("hostname", "")).strip().lower() == hostname.lower():
                removed = True
                continue
            if not rule.get("hostname") and str(rule.get("service", "")).startswith("http_status:"):
                continue
            updated.append(rule)

        updated.append({"service": "http_status:404"})
        config["ingress"] = updated
        if not removed:
            return True
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
        records = existing if isinstance(existing, list) else []

        for rec in records:
            rec_id = str(rec.get("id", "")).strip()
            if not rec_id:
                continue
            if (
                str(rec.get("content", "")).strip().lower() == cname_target.lower()
                and bool(rec.get("proxied"))
            ):
                return True
            updated = self._request(
                "PUT",
                f"/zones/{zone_id}/dns_records/{rec_id}",
                json={
                    "type": "CNAME",
                    "name": hostname,
                    "content": cname_target,
                    "proxied": True,
                    "ttl": 1,
                },
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
            return True

        tunnel_id = self._resolve_tunnel_id()
        tunnel_target = f"{tunnel_id}.cfargotunnel.com" if tunnel_id else ""
        existing = self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"type": "CNAME", "name": hostname},
        )
        records = existing if isinstance(existing, list) else []

        ok = True
        for rec in records:
            rec_id = str(rec.get("id", "")).strip()
            if not rec_id:
                continue
            content = str(rec.get("content", "")).strip().lower()
            if tunnel_target and content != tunnel_target.lower():
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

    def list_hosts(self) -> list[dict]:
        config = self._get_configuration()
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

        if old_hostname and old_hostname != hostname:
            self._delete_ingress_rule(old_hostname)
            self._delete_dns_record(old_hostname)

        service_url = f"{scheme}://{ip}:{int(port)}"
        if not self._upsert_ingress_rule(hostname, service_url):
            return False
        return self._ensure_dns_record(hostname)

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

        def _add(name: str, ok: bool, detail: str, blocking: bool = True) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking})

        token_verify = self._request_detailed("GET", "/user/tokens/verify")
        _add(
            "token_verify",
            token_verify["ok"],
            "API token is active" if token_verify["ok"] else "Token verification failed",
            True,
        )

        if not self.account_id:
            _add("account_id", False, "Account ID is required", True)
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
            )

        tunnel_id = self._resolve_tunnel_id()
        if not tunnel_id:
            _add(
                "tunnel_id",
                False,
                "Tunnel ID is missing or cannot be auto-resolved",
                True,
            )
        else:
            tunnel_get = self._request_detailed("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}")
            _add(
                "tunnel_details",
                tunnel_get["ok"],
                "Can read tunnel details" if tunnel_get["ok"] else "Cannot read tunnel details",
                True,
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
            )

            if write_probe and config_get["ok"]:
                current_cfg = self._get_configuration()
                write_ok = bool(current_cfg) and self._put_configuration(current_cfg)
                _add(
                    "tunnel_config_write",
                    write_ok,
                    "Configuration write probe succeeded" if write_ok else "Configuration write probe failed",
                    True,
                )
            else:
                _add(
                    "tunnel_config_write",
                    False,
                    "Write probe skipped (safe mode).",
                    False,
                )

        hostname = (hostname_hint or "").strip().lower()
        if hostname:
            zone_id = self._find_zone(hostname)
            _add(
                "zone_lookup",
                bool(zone_id),
                "Can resolve DNS zone for hostname" if zone_id else "Cannot resolve DNS zone for hostname",
                False,
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
                )
        else:
            _add(
                "zone_lookup",
                False,
                "No hostname hint provided for DNS scope checks",
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
