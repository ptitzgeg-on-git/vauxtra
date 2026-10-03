"""Zoraxy provider: proxy rule and certificate management over the webmin API."""

import datetime
import json
import logging
import re

import requests

from app.config import PROVIDER_TIMEOUT
from app.providers.base import (
    ProviderListingRefused,
    ProxyProvider,
    TimeoutSession,
    login_check,
    reachability_check,
)
from app.text import plural

log = logging.getLogger(__name__)

_CSRF_META = re.compile(
    r'<meta\s+name=["\']zoraxy\.csrf\.Token["\']\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_HOSTNAME = re.compile(r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$", re.IGNORECASE)
_REDIRECTS = (301, 302, 303, 307, 308)
_PROXY_TYPE_HOST = 1


def _rule_key(host_id) -> str | None:
    """Return the rule key (Zoraxy keys rules by hostname), or None if `host_id` is not a non-empty string.

    Zoraxy answers HTTP 200 to a delete of an unknown rule, so a bad key is refused here.
    """
    if not isinstance(host_id, str):
        return None
    key = host_id.strip()
    return key or None


def _flag(value) -> str:
    """The form value Zoraxy handlers compare booleans against.

    `PostBool` accepts several spellings, but the edit handler compares `bpgtls`, `rate`
    and `enableConnectSupport` to the literal string "true", so only that spelling is safe.
    """
    return "true" if value else "false"


def _join_origin(host: str, port: int) -> str:
    """Zoraxy's upstream string for a host and port: no scheme, IPv6 in brackets."""
    host = (host or "").strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{int(port)}"


def _split_origin(origin: str, scheme: str) -> tuple[str, int]:
    """Split a Zoraxy upstream (`host:port`, no scheme) into host and port.

    A bare host gets the scheme's default port; IPv6 brackets are removed.
    """
    origin = (origin or "").strip()
    default = 443 if scheme == "https" else 80
    if not origin:
        return "", 0
    if origin.startswith("["):
        end = origin.find("]")
        if end == -1:
            return origin, default
        host, rest = origin[1:end], origin[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, default
    host, sep, port = origin.rpartition(":")
    if sep and port.isdigit() and ":" not in host:
        return host, int(port)
    return origin, default


def _iso_expiry(raw) -> str:
    """Convert Zoraxy's `2006-01-02 15:04:05` UTC expiry to ISO 8601 with `Z`, or "".

    "Unknown" or anything unparseable gives "", so callers skip the entry.
    """
    text = str(raw or "").strip()
    try:
        parsed = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_reply(r) -> tuple[bool, str]:
    """Return (ok, error) for a Zoraxy mutation reply.

    Success and failure both use HTTP 200 (`"OK"` vs `{"error": ...}`), so the body decides.
    A redirect (expired session) or a non-JSON body is a failure.
    """
    if r.status_code in _REDIRECTS:
        return False, "not authenticated"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    try:
        body = r.json()
    except ValueError:
        return False, "unexpected non-JSON response"
    if body == "OK":
        return True, ""
    if isinstance(body, dict) and "error" in body:
        return False, str(body["error"])
    return False, f"unexpected response: {body!r}"


class ZoraxyProvider(ProxyProvider):
    HOST_ID_IS_HOSTNAME = True

    def __init__(self, url: str, username: str, password: str):
        self.base_url = url.rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.session = TimeoutSession()
        self._csrf_token: str | None = None

    def _fetch_csrf(self) -> str | None:
        """Return a fresh CSRF token, or None if the page could not be read.

        gorilla/csrf pairs the page's meta token with the `zoraxy_csrf` cookie, even with
        `-noauth`. Read from /login.html, which is served to unauthenticated clients.
        """
        try:
            r = self.session.get(
                f"{self.base_url}/login.html",
                timeout=PROVIDER_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException:
            return None
        match = _CSRF_META.search(r.text or "") if r.status_code == 200 else None
        if not match:
            return None
        self._csrf_token = match.group(1)
        return self._csrf_token

    def _get(self, path: str, params: dict | None = None):
        """Decoded JSON of a GET, or None when Zoraxy did not answer with JSON.

        Redirects are not followed: for an auth-protected route a 3xx is the login page,
        and following it would turn "session expired" into a successful HTML download.
        """
        try:
            r = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=PROVIDER_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def _send(self, path: str, data: dict) -> tuple[bool, str]:
        """POST one form with the CSRF header and read Zoraxy's verdict.

        A 403 here is gorilla/csrf rejecting the token, which happens when Zoraxy restarted
        or rotated the cookie behind our back; the token is then re-read once and the
        request replayed. A second 403 is reported rather than retried forever.
        """
        token = self._csrf_token or self._fetch_csrf()
        if not token:
            return False, "could not read the CSRF token"
        for attempt in (0, 1):
            try:
                r = self.session.post(
                    f"{self.base_url}{path}",
                    data=data,
                    headers={"X-CSRF-Token": token},
                    timeout=PROVIDER_TIMEOUT,
                    allow_redirects=False,
                )
            except requests.RequestException as e:
                return False, str(e) or e.__class__.__name__
            if r.status_code == 403 and attempt == 0:
                token = self._fetch_csrf()
                if not token:
                    return False, "could not refresh the CSRF token"
                continue
            return _parse_reply(r)
        return False, "CSRF token rejected"

    def _login(self) -> bool:
        """Log in with the configured credentials.

        Zoraxy answers HTTP 200 either way; only the body says whether it worked.
        """
        ok, _err = self._send(
            "/api/auth/login",
            {"username": self.username, "password": self.password, "rmbme": "true"},
        )
        return ok

    def _ensure_auth(self) -> bool:
        """Ensure the session is usable, logging in when it is not.

        `checkLogin` is unprotected and answers `true` on a `-noauth` instance, which lets a
        provider with empty credentials work there.
        """
        if self._get("/api/auth/checkLogin") is True:
            return True
        if not self.username:
            return False
        return self._login()

    def _post(self, path: str, data: dict) -> tuple[bool, str]:
        if not self._ensure_auth():
            return False, "authentication failed"
        return self._send(path, data)

    def test_connection(self) -> bool:
        if not self._ensure_auth():
            return False
        return isinstance(self._get("/api/proxy/list", {"type": "host"}), list)

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False) -> dict:
        """Check reachability, then credentials, then the rule list, as separate results.

        Separated because a wrong password still gets HTTP 200 from Zoraxy.
        """
        checks = [reachability_check(self.session, f"{self.base_url}/login.html")]
        if not checks[0]["ok"]:
            return {"ok": False, "checks": checks, "warnings": []}

        authenticated = self._ensure_auth()
        checks.append(login_check(authenticated))
        if not authenticated:
            return {"ok": False, "checks": checks, "warnings": []}

        rules = self._get("/api/proxy/list", {"type": "host"})
        read_ok = isinstance(rules, list)
        checks.append({
            "name": "List rules",
            "ok": read_ok,
            "detail": f"{plural(len(rules), 'rule')} readable" if read_ok else "Zoraxy did not return its rule list",
            "detail_code": "proxy_read_ok" if read_ok else "proxy_read_failed",
            "blocking": not read_ok,
        })
        return {"ok": read_ok, "checks": checks, "warnings": []}

    @staticmethod
    def _normalize_rule(rule: dict) -> dict:
        """Convert a Zoraxy rule to Vauxtra's host shape.

        The id is the hostname as Zoraxy stores it (the config file is named after it);
        `domains` are lowercased. `ssl` is always True since TLS is listener-wide. A rule
        with only inactive origins is still normalized; one with no origin has an empty target.
        """
        domain = str(rule.get("RootOrMatchingDomain") or "")
        aliases = [str(a).lower() for a in (rule.get("MatchingDomainAlias") or []) if a]
        origins = rule.get("ActiveOrigins") or rule.get("InactiveOrigins") or []
        origin = origins[0] if origins and isinstance(origins[0], dict) else {}
        scheme = "https" if origin.get("RequireTLS") else "http"
        host, port = _split_origin(origin.get("OriginIpOrDomain") or "", scheme)
        preferred = (rule.get("TlsOptions") or {}).get("PreferredCertificate") or {}
        target = ""
        if host:
            target = f"{scheme}://[{host}]:{port}" if ":" in host else f"{scheme}://{host}:{port}"
        return {
            "id": domain,
            "domains": [domain.lower(), *aliases],
            "target": target,
            "scheme": scheme,
            "host": host,
            "port": port,
            "ssl": True,
            "bypass_global_tls": bool(rule.get("BypassGlobalTLS")),
            "websocket": not rule.get("DisableWebSocket", False),
            "cert_id": preferred.get(domain) or None,
            "enabled": not rule.get("Disabled", False),
            "tags": list(rule.get("Tags") or []),
        }

    def list_hosts(self) -> list[dict]:
        """Return every host rule. Raises instead of returning [] when the list is unreadable,
        so the drift check does not report routes as missing.
        """
        if not self._ensure_auth():
            raise ProviderListingRefused("Zoraxy refused the credentials")
        rules = self._get("/api/proxy/list", {"type": "host"})
        if not isinstance(rules, list):
            raise ProviderListingRefused("Zoraxy would not list its proxy rules")
        return [
            self._normalize_rule(rule)
            for rule in rules
            if isinstance(rule, dict) and rule.get("ProxyType", _PROXY_TYPE_HOST) == _PROXY_TYPE_HOST
        ]

    def _set_preferred_certificate(self, domain: str, cert_id: str | None,
                                   current: dict | None = None) -> tuple[bool, str]:
        """Record `cert_id` as the rule's preferred certificate.

        setTlsConfig replaces the whole TlsOptions block, so current flags are copied. The
        preferred map is rebuilt around the current hostname, since a rename leaves it stale.
        """
        options = dict(current or {})
        tls_config = {
            "DisableSNI": bool(options.get("DisableSNI")),
            "DisableLegacyCertificateMatching": bool(options.get("DisableLegacyCertificateMatching")),
            "EnableAutoHTTPS": bool(options.get("EnableAutoHTTPS")),
            "PreferredCertificate": {domain: str(cert_id)} if cert_id else {},
        }
        return self._post(
            "/api/proxy/setTlsConfig",
            {"ep": domain, "tlsConfig": json.dumps(tls_config)},
        )

    def create_host(self, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: str | None = None) -> dict | None:
        """Create a host rule; record the chosen certificate as preferred.

        Refuses an existing hostname (Zoraxy's add silently overwrites). Upstream TLS is not
        validated (LAN self-signed certs). A failed setTlsConfig is logged, not rolled back.
        """
        domain = (domain or "").strip()
        if not domain or not self._ensure_auth():
            return None
        existing = self._get("/api/proxy/detail", {"type": "host", "epname": domain})
        if not isinstance(existing, dict) or "error" not in existing:
            if isinstance(existing, dict) and existing.get("RootOrMatchingDomain"):
                log.warning("Zoraxy: rule %s already exists, not overwriting it", domain)
            else:
                log.warning("Zoraxy: could not check whether rule %s exists, not adding it", domain)
            return None
        https = scheme == "https"
        payload = {
            "type": "host",
            "rootname": domain,
            "ep": _join_origin(ip, port),
            "tls": _flag(https),
            "tlsval": _flag(https),
            "bypassGlobalTLS": "false",
            "access": "default",
            "disableWebSocket": _flag(not websocket),
            "websocketTimeout": "0",
            "enableUtm": "true",
        }
        ok, err = self._post("/api/proxy/add", payload)
        if not ok:
            log.warning("Zoraxy: could not add rule %s: %s", domain, err)
            return None
        if cert_id:
            ok, err = self._set_preferred_certificate(domain, cert_id)
            if not ok:
                log.warning("Zoraxy: rule %s added but certificate %s not set: %s", domain, cert_id, err)
        target_host = f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip
        return {
            "id": domain,
            "domain": domain,
            "domains": [domain],
            "target": f"{scheme}://{target_host}:{port}",
            "scheme": scheme,
            "host": ip,
            "port": port,
            "ssl": True,
            "websocket": websocket,
            "cert_id": cert_id,
            "enabled": True,
        }

    @staticmethod
    def _edit_form(current: dict, *, disable_websocket: bool) -> dict:
        """Build the complete /api/proxy/edit form for a rule with one flag changed.

        The edit handler resets every parameter it reads but is not sent, so all of them are
        echoed from the current rule. `tls` is read but never applied, so it is omitted;
        captcha ExceptionRules are not read, as in the official UI.
        """
        auth = current.get("AuthenticationProvider") or {}
        captcha = current.get("CaptchaConfig") or {}
        form = {
            "type": "host",
            "rootname": str(current.get("RootOrMatchingDomain") or ""),
            "ss": _flag(current.get("UseStickySession")),
            "bpgtls": _flag(current.get("BypassGlobalTLS")),
            "enableConnectSupport": _flag(current.get("EnableConnectSupport")),
            "dutm": _flag(current.get("DisableUptimeMonitor")),
            "utmURI": str(current.get("UptimeMonitorURI") or ""),
            "dAutoFallback": _flag(current.get("DisableAutoFallback")),
            "authprovider": str(int(auth.get("AuthMethod") or 0)),
            "rate": _flag(current.get("RequireRateLimit")),
            "ratenum": str(int(current.get("RateLimit") or 0)),
            "captcha": _flag(current.get("RequireCaptcha")),
            "dChunkedEnc": _flag(current.get("DisableChunkedTransferEncoding")),
            "forceHTTP11": _flag(current.get("ForceHTTP11")),
            "disableWebSocket": _flag(disable_websocket),
            "websocketTimeout": str(int(current.get("WebsocketTimeout") or 0)),
            "enableTimeoutRefreshOnActivity": _flag(current.get("EnableTimeoutRefreshOnActivity")),
            "dLogging": _flag(current.get("DisableLogging")),
            "dStatisticCollection": _flag(current.get("DisableStatisticCollection")),
            "blockCommonExploits": _flag(current.get("BlockCommonExploits")),
            "blockAICrawlers": _flag(current.get("BlockAICrawlers")),
            "mitigationAction": str(int(current.get("MitigationAction") or 0)),
            "tags": ",".join(str(t) for t in (current.get("Tags") or [])),
        }
        if current.get("RequireCaptcha"):
            score = captcha.get("RecaptchaScore")
            form.update({
                "captchaProvider": str(int(captcha.get("Provider") or 0)),
                "captchaSiteKey": str(captcha.get("SiteKey") or ""),
                "captchaSecretKey": str(captcha.get("SecretKey") or ""),
                "captchaSessionDuration": str(int(captcha.get("SessionDuration") or 3600)),
                "captchaRecaptchaVersion": str(captcha.get("RecaptchaVersion") or "v2"),
                "captchaRecaptchaScore": str(score if score is not None else 0.5),
                "captchaPathPrefixes": ",".join(
                    str(p) for p in (captcha.get("ProtectedPathPrefixes") or [])
                ),
            })
        return form

    def update_host(self, host_id: str, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: str | None = None) -> bool:
        """Bring a rule to the requested state, calling only the endpoints whose aspect changed.

        The rename runs first since every other endpoint is keyed on the name, and is
        decided case-insensitively (Zoraxy looks rules up lowercased). True only if every
        needed step answered "OK".
        """
        key = _rule_key(host_id)
        if key is None or not self._ensure_auth():
            return False
        current = self._get("/api/proxy/detail", {"type": "host", "epname": key})
        if not isinstance(current, dict) or "error" in current or not current.get("RootOrMatchingDomain"):
            return False
        key = str(current["RootOrMatchingDomain"])
        domain = (domain or "").strip() or key

        if domain.lower() != key.lower():
            ok, err = self._post("/api/proxy/setHostname", {"oldHostname": key, "newHostname": domain})
            if not ok:
                log.warning("Zoraxy: could not rename %s to %s: %s", key, domain, err)
                return False
            renamed = True
            key = domain
        else:
            renamed = False

        wanted_origin = _join_origin(ip, port)
        wanted_tls = scheme == "https"
        active = [o for o in (current.get("ActiveOrigins") or []) if isinstance(o, dict)]
        if active:
            origin = active[0]
            if (str(origin.get("OriginIpOrDomain") or "") != wanted_origin
                    or bool(origin.get("RequireTLS")) != wanted_tls):
                ok, err = self._post("/api/proxy/upstream/update", {
                    "ep": key,
                    "origin": str(origin.get("OriginIpOrDomain") or ""),
                    "payload": json.dumps({
                        "OriginIpOrDomain": wanted_origin,
                        "RequireTLS": wanted_tls,
                        "SkipCertValidations": wanted_tls,
                    }),
                    "active": "true",
                })
                if not ok:
                    log.warning("Zoraxy: could not update upstream of %s: %s", key, err)
                    return False
        else:
            ok, err = self._post("/api/proxy/upstream/add", {
                "ep": key,
                "origin": wanted_origin,
                "tls": _flag(wanted_tls),
                "tlsval": _flag(wanted_tls),
                "bpwsorg": "false",
                "active": "true",
            })
            if not ok:
                log.warning("Zoraxy: could not add upstream to %s: %s", key, err)
                return False

        if bool(current.get("DisableWebSocket", False)) != (not websocket):
            form = self._edit_form({**current, "RootOrMatchingDomain": key}, disable_websocket=not websocket)
            ok, err = self._post("/api/proxy/edit", form)
            if not ok:
                log.warning("Zoraxy: could not edit websocket flag of %s: %s", key, err)
                return False

        tls_options = current.get("TlsOptions") or {}
        current_cert = (tls_options.get("PreferredCertificate") or {}).get(
            str(current["RootOrMatchingDomain"])
        ) or None
        wanted_cert = str(cert_id) if cert_id else None
        if wanted_cert != current_cert or (renamed and wanted_cert):
            ok, err = self._set_preferred_certificate(key, wanted_cert, tls_options)
            if not ok:
                log.warning("Zoraxy: could not set certificate of %s: %s", key, err)
                return False
        return True

    def toggle_host(self, host_id: str, enabled: bool) -> bool:
        key = _rule_key(host_id)
        if key is None:
            return False
        ok, err = self._post("/api/proxy/toggle", {"ep": key, "enable": _flag(enabled)})
        if not ok:
            log.warning("Zoraxy: could not toggle %s: %s", key, err)
        return ok

    def delete_host(self, host_id: str) -> bool:
        key = _rule_key(host_id)
        if key is None:
            return False
        ok, err = self._post("/api/proxy/del", {"ep": key})
        if not ok:
            log.warning("Zoraxy: could not delete %s: %s", key, err)
        return ok

    def get_certificates(self) -> list[dict]:
        """Return every certificate in Zoraxy's store, id = filename without extension.

        ACME's `_.example.com` is reported as `*.example.com`, and a filename that is a
        hostname counts as a domain (legacy matching). Only the CN is exposed, not SANs.
        """
        if not self._ensure_auth():
            raise RuntimeError("Zoraxy authentication failed (check username and password)")
        r = self.session.get(
            f"{self.base_url}/api/cert/list",
            params={"date": "true"},
            timeout=PROVIDER_TIMEOUT,
            allow_redirects=False,
        )
        r.raise_for_status()
        if r.status_code != 200:
            raise RuntimeError("Zoraxy did not return the certificate list (session expired?)")
        data = r.json()
        if not isinstance(data, list):
            return []
        result = []
        for c in data:
            if isinstance(c, str):
                c = {"Filename": c}
            if not isinstance(c, dict):
                continue
            filename = str(c.get("Filename") or "").strip()
            if not filename:
                continue
            domains = []
            common_name = str(c.get("Domain") or "").strip()
            if common_name:
                domains.append(common_name)
            as_hostname = f"*.{filename[2:]}" if filename.startswith("_.") else filename
            if _HOSTNAME.match(as_hostname) and as_hostname.lower() != common_name.lower():
                domains.append(as_hostname)
            remaining = c.get("RemainingDays")
            expires_on = _iso_expiry(c.get("ExpireDate"))
            result.append({
                "id":             filename,
                "nice_name":      filename,
                "domains":        domains,
                "expires_on":     expires_on,
                # Zoraxy reports -1 both for expired and for unreadable dates, so the count
                # is only kept when a date was parsed.
                "remaining_days": remaining if expires_on and isinstance(remaining, int) else None,
                "use_dns":        bool(c.get("UseDNS")),
                "is_fallback":    bool(c.get("IsFallback")),
            })
        return result

    def find_best_certificate(self, host: str) -> str | None:
        """Return a certificate covering `host` (exact name or parent wildcard), or None.

        Same rule as NPM: a non-covering preferred cert would be served if SNI were disabled.
        """
        host = (host or "").strip().lower().rstrip(".")
        if not host:
            return None
        parent = host.split(".", 1)[1] if "." in host else ""

        exact: str | None = None
        wildcard: str | None = None
        for cert in self.get_certificates():
            cid = cert["id"]
            names = {str(n).strip().lower().rstrip(".") for n in cert["domains"] if n}
            nice = str(cert.get("nice_name") or "").strip().lower().rstrip(".")
            if nice:
                names.add(nice)

            if exact is None and host in names:
                exact = cid
            if wildcard is None and parent and f"*.{parent}" in names:
                wildcard = cid

        return exact if exact is not None else wildcard
