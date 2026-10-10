"""Regression tests for the provider findings of the second review pass (M3 to M14, B1, B21).

Each class names the finding it covers. `tests/` is not a package, so the isolated-database
base class is duplicated here by design, like in the other files.
"""

import hashlib
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient

import app.auth as auth
import app.db as app_db
import app.main as app_main
import app.scheduler as scheduler
from app import models
from app.providers.base import (
    BlockedDestinationError,
    TimeoutSession,
    is_blocked_address,
    validate_provider_url,
)


def _response(status_code: int = 200, json_data=None, headers=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.content = b"data" if json_data is not None else b""
    r.json.return_value = json_data
    r.headers = headers or {}
    r.raise_for_status = MagicMock(
        side_effect=None if status_code < 400 else requests.HTTPError
    )
    return r


class _IsolatedDB(unittest.TestCase):
    APP_PASSWORD = "test-password"

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "providers.test.db")
        models.init_db()

        from app.limiter import limiter as _limiter

        def _no_rate_limit(request, *_a, **_kw):
            request.state.view_rate_limit = None

        self._patchers = [
            patch.object(auth, "APP_PASSWORD", self.APP_PASSWORD),
            patch.object(_limiter, "_check_request_limit", _no_rate_limit),
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
        ]
        for p in self._patchers:
            p.start()

        conn = models.get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                   prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                   created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
        )
        for name, scopes in (("rw", "write"), ("adm", "admin")):
            conn.execute(
                "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                (name, hashlib.sha256(f"key-{name}".encode()).hexdigest(), name[:8], scopes),
            )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (1, 'Tunnel', 'cloudflare_tunnel', 'https://api.cloudflare.com/client/v4',
                       'acc', 'pass', '{"tunnel_id": "t1", "proxied": false}', 1)"""
        )
        conn.commit()
        conn.close()
        self._client_cm = TestClient(app_main.app, base_url="http://localhost")
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _h(self, key: str) -> dict:
        return {"Authorization": f"Bearer key-{key}"}

    def _extra(self) -> dict:
        conn = models.get_db()
        try:
            return json.loads(conn.execute("SELECT extra FROM providers WHERE id=1").fetchone()[0])
        finally:
            conn.close()


# ------------------------------------------------------------------------------------ M3


class _FakeSocket:
    def __init__(self, peer: str):
        self._peer = peer
        self.closed = False

    def getpeername(self):
        return (self._peer, 80)

    def close(self):
        self.closed = True


class M3MetadataIsRefusedWhateverTheNameTests(unittest.TestCase):
    def test_metadata_names_and_addresses_are_refused_by_the_validator(self):
        for url in (
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://METADATA.google.internal./",
            "http://169.254.169.254/",
            "http://100.100.100.200/",
            "http://[fd00:ec2::254]/",
            "http://[::ffff:169.254.169.254]/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_provider_url(url)

    def test_a_name_resolving_to_metadata_is_refused_when_resolution_is_asked(self):
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        with patch("app.providers.base.socket.getaddrinfo", return_value=answer):
            with self.assertRaises(ValueError):
                validate_provider_url("http://169.254.169.254.nip.io/", resolve=True)
            # Without `resolve`, only the spelling is judged: the connection check does the rest.
            self.assertEqual(
                validate_provider_url("http://169.254.169.254.nip.io/"),
                "http://169.254.169.254.nip.io/",
            )

    def test_lan_addresses_and_an_empty_url_stay_accepted(self):
        self.assertEqual(validate_provider_url("http://192.168.1.10:81"), "http://192.168.1.10:81")
        self.assertEqual(validate_provider_url("  "), "")
        for address in ("192.168.1.10", "10.0.0.5", "172.16.0.1", "127.0.0.1", "fd00::1"):
            with self.subTest(address=address):
                self.assertFalse(is_blocked_address(address))

    def test_the_provider_form_refuses_the_metadata_name(self):
        from pydantic import ValidationError

        from app.api.providers import ProviderIn

        with self.assertRaises(ValidationError):
            ProviderIn(name="x", type="npm", url="http://metadata.google.internal")

    def test_a_literal_metadata_url_never_leaves_the_session(self):
        session = TimeoutSession()
        with patch("requests.adapters.HTTPAdapter.send") as send:
            with self.assertRaises(BlockedDestinationError):
                session.get("http://metadata.google.internal/computeMetadata/v1/")
            with self.assertRaises(requests.ConnectionError):
                session.get("http://2852039166/latest/meta-data/")
        send.assert_not_called()

    def test_a_name_that_connects_to_metadata_is_refused_on_the_socket(self):
        from urllib3.connection import HTTPConnection

        sock = _FakeSocket("169.254.169.254")
        with patch.object(HTTPConnection, "_new_conn", lambda self: sock):
            with self.assertRaises(BlockedDestinationError):
                TimeoutSession().get("http://169.254.169.254.nip.io/latest/meta-data/")
        self.assertTrue(sock.closed)

    def test_the_provider_reports_it_as_unreachable(self):
        from app.providers.adguard import AdGuardProvider

        self.assertIsNone(AdGuardProvider("http://metadata.google.internal", "u", "p").list_rewrites())

    def test_a_lan_upstream_is_still_reached(self):
        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_a):
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            r = TimeoutSession(timeout=5).get(f"http://127.0.0.1:{server.server_port}/")
            self.assertEqual(r.status_code, 200)
        finally:
            server.shutdown()
            server.server_close()


# ------------------------------------------------------------------------------------ M4


class M4RedirectsKeepTheWholeOriginTests(unittest.TestCase):
    def _redirect(self, location: str, url: str):
        resp = requests.Response()
        resp.status_code = 307
        resp.headers["location"] = location
        resp.request = requests.Request("POST", url, json={"password": "x"}).prepare()
        return TimeoutSession().get_redirect_target(resp)

    def test_a_downgrade_to_http_is_not_followed(self):
        self.assertIsNone(self._redirect("http://pihole.lan/api/auth", "https://pihole.lan/api/auth"))

    def test_another_port_on_the_same_host_is_not_followed(self):
        self.assertIsNone(self._redirect("http://pihole.lan:8080/x", "http://pihole.lan/api/auth"))
        self.assertIsNone(self._redirect("https://pihole.lan:8443/x", "http://pihole.lan/api/auth"))
        self.assertIsNone(self._redirect("https://npm.lan/x", "http://npm.lan:81/api"))

    def test_the_same_origin_and_the_https_upgrade_still_are(self):
        self.assertEqual(self._redirect("/admin/", "http://pihole.lan/api/auth"), "/admin/")
        self.assertEqual(
            self._redirect("http://pihole.lan:80/admin/", "http://pihole.lan/api/auth"),
            "http://pihole.lan:80/admin/",
        )
        self.assertEqual(
            self._redirect("https://pihole.lan/api/auth", "http://pihole.lan/api/auth"),
            "https://pihole.lan/api/auth",
        )


# ------------------------------------------------------------------------------------ M9


class M9CloudflareAddressChangeIsAPatchTests(unittest.TestCase):
    def test_only_the_address_is_sent(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "zone", "tok", {"proxied": False})
        p._client = MagicMock()
        p._client.dns.records.list.return_value = [
            SimpleNamespace(id="r1", name="app.example.com", content="10.0.0.1", ttl=300,
                            proxied=True, comment="keep me", tags=["team:a"])
        ]
        self.assertTrue(p.add_rewrite("app.example.com", "10.0.0.2"))
        p._client.dns.records.update.assert_not_called()
        kwargs = p._client.dns.records.edit.call_args.kwargs
        self.assertEqual(kwargs["content"], "10.0.0.2")
        self.assertEqual(kwargs["ttl"], 300)
        for absent in ("proxied", "comment", "tags"):
            self.assertNotIn(absent, kwargs)


# ----------------------------------------------------------------------------------- M10


class M10ZoneLookupErrorsDoNotWalkUpTests(unittest.TestCase):
    def test_cloudflare_dns_stops_on_an_error_and_caches_nothing(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "", "tok", {})
        p._client = MagicMock()
        p._client.zones.list.side_effect = Exception("502")
        self.assertIsNone(p._find_zone("sub.example.com"))
        self.assertEqual(p._client.zones.list.call_count, 1)
        self.assertEqual(p._zone_cache, {})

    def test_cloudflare_dns_still_walks_up_on_an_empty_answer(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "", "tok", {})
        p._client = MagicMock()
        p._client.zones.list.side_effect = [[], [SimpleNamespace(id="z1", name="example.com")]]
        self.assertEqual(p._find_zone("sub.example.com"), "z1")

    def test_tunnel_stops_on_an_error(self):
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        p = CloudflareTunnelProvider("", "acc", "tok", {"tunnel_id": "t1"})
        p._request = MagicMock(side_effect=[None, [{"id": "parent", "name": "example.com"}]])
        self.assertEqual(p._find_zone("sub.example.com"), "")
        self.assertEqual(p._request.call_count, 1)

    def test_tunnel_still_walks_up_on_an_empty_answer(self):
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        p = CloudflareTunnelProvider("", "acc", "tok", {"tunnel_id": "t1"})
        p._request = MagicMock(side_effect=[[], [{"id": "parent", "name": "example.com"}]])
        self.assertEqual(p._find_zone("sub.example.com"), "parent")


# ----------------------------------------------------------------------------------- M11


class M11MissingCaBundleTests(_IsolatedDB):
    MISSING = os.path.join(tempfile.gettempdir(), "vauxtra-no-such-dir", "ca.pem")

    def test_creating_a_provider_with_a_missing_bundle_is_a_400(self):
        resp = self.client.post(
            "/api/providers",
            json={"name": "NPM", "type": "npm", "url": "https://npm.lan", "username": "a",
                  "password": "b", "extra": {"ca_bundle": self.MISSING}},
            headers=self._h("adm"),
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_updating_to_a_missing_bundle_is_a_400(self):
        resp = self.client.put(
            "/api/providers/1",
            json={"extra": {"tunnel_id": "t1", "ca_bundle": self.MISSING}},
            headers=self._h("adm"),
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertNotIn("ca_bundle", self._extra())

    def test_a_bundle_gone_after_saving_fails_as_a_request_error(self):
        from app.providers.factory import create_provider

        row = {
            "type": "npm", "url": "https://127.0.0.1:9", "username": "u", "password": "",
            "extra": json.dumps({"ca_bundle": self.MISSING}),
        }
        with patch("app.providers.factory.decrypt_secret", lambda v: v):
            npm = create_provider(row)  # does not raise
        with self.assertRaises(requests.RequestException) as ctx:
            npm.session.get("https://127.0.0.1:9/api")
        self.assertIn("CA certificate bundle", str(ctx.exception))
        self.assertFalse(npm.test_connection())


# ----------------------------------------------------------------------------------- M12


class M12PiholeNeverGuessesV5Tests(unittest.TestCase):
    def test_a_timeout_reports_down_and_never_sends_the_password_in_the_url(self):
        from app.providers.pihole import PiholeProvider

        p = PiholeProvider("http://pihole", "", "secret-pw")
        p.session.get = MagicMock(side_effect=requests.Timeout("slow"))
        p.session.post = MagicMock()
        self.assertFalse(p.test_connection())
        self.assertIsNone(p.list_rewrites())
        self.assertFalse(p.add_rewrite("a.example.com", "10.0.0.1"))
        self.assertIsNone(p._version, "a guessed version was cached")
        for call in p.session.get.call_args_list:
            self.assertNotIn("auth", call.kwargs.get("params") or {})
            self.assertNotIn("/admin/api.php", call.args[0])

    def test_the_next_call_detects_again(self):
        from app.providers.pihole import PiholeProvider

        p = PiholeProvider("http://pihole", "", "secret-pw")
        p.session.get = MagicMock(side_effect=requests.Timeout("slow"))
        self.assertFalse(p.test_connection())
        p.session.get = MagicMock(return_value=_response(401))
        self.assertEqual(p._detect_version(), 6)


# ----------------------------------------------------------------------------------- M14


class M14TunnelNullConfigTests(unittest.TestCase):
    def test_a_null_config_is_an_empty_one_not_the_envelope(self):
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        p = CloudflareTunnelProvider("", "acc", "tok", {"tunnel_id": "t1"})
        sent = {}

        def _request(method, path, **kwargs):
            if method == "GET":
                return {"tunnel_id": "t1", "version": 3, "config": None, "source": "cloudflare"}
            sent.update(kwargs.get("json") or {})
            return {"config": sent.get("config")}

        p._request = MagicMock(side_effect=_request)
        self.assertEqual(p._get_configuration(), {})
        self.assertTrue(p._upsert_ingress_rule("app.example.com", "http://10.0.0.5:80"))
        self.assertEqual(set(sent["config"]), {"ingress"})


# ------------------------------------------------------------------------------------ B1


class B1OnlyTlsSettingsNeedAdminTests(_IsolatedDB):
    def test_a_write_key_may_change_proxied_and_tunnel_id(self):
        resp = self.client.put(
            "/api/providers/1",
            json={"extra": {"tunnel_id": "t2", "proxied": True}},
            headers=self._h("rw"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._extra(), {"tunnel_id": "t2", "proxied": True})

    def test_a_write_key_cannot_change_tls_verify_or_ca_bundle(self):
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
            path = fh.name
        try:
            for extra in (
                {"tunnel_id": "t1", "proxied": False, "tls_verify": False},
                {"tunnel_id": "t1", "proxied": False, "ca_bundle": path},
            ):
                with self.subTest(extra=extra):
                    resp = self.client.put("/api/providers/1", json={"extra": extra},
                                           headers=self._h("rw"))
                    self.assertEqual(resp.status_code, 403, resp.text)
                    self.assertEqual(self._extra(), {"tunnel_id": "t1", "proxied": False})
            resp = self.client.put(
                "/api/providers/1",
                json={"extra": {"tunnel_id": "t1", "tls_verify": False}},
                headers=self._h("adm"),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
        finally:
            os.unlink(path)


# ----------------------------------------------------------------------------------- B21


class B21TraefikUnreadableIsNotEmptyTests(unittest.TestCase):
    def test_an_error_is_none(self):
        from app.providers.traefik import TraefikProvider

        t = TraefikProvider("http://traefik:8080", "", "")
        t.session.get = MagicMock(side_effect=requests.ConnectionError("down"))
        self.assertIsNone(t.list_hosts())

    def test_a_non_json_body_is_none(self):
        from app.providers.traefik import TraefikProvider

        t = TraefikProvider("http://traefik:8080", "", "")
        r = _response(200, [])
        r.json.side_effect = ValueError("not json")
        t.session.get = MagicMock(return_value=r)
        self.assertIsNone(t.list_hosts())

    def test_hitting_the_page_cap_with_pages_left_is_none(self):
        from app.providers.traefik import TraefikProvider

        t = TraefikProvider("http://traefik:8080", "", "")
        t._MAX_PAGES = 2

        def _get(_url, params=None, **_kw):
            page = (params or {}).get("page", 1)
            return _response(200, [], headers={"X-Next-Page": str(page + 1)})

        t.session.get = MagicMock(side_effect=_get)
        self.assertIsNone(t.list_hosts())


class B21CloudflareProxyAndClientTests(unittest.TestCase):
    def test_a_cname_is_created_with_the_configured_proxy_flag(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "zone", "tok", {"proxied": True})
        p._client = MagicMock()
        p._client.dns.records.list.return_value = []
        self.assertTrue(p.add_rewrite("app.example.com", "target.example.net"))
        self.assertIs(p._client.dns.records.create.call_args.kwargs["proxied"], True)

    def test_the_client_is_closed(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "zone", "tok", {})
        client = MagicMock()
        p._client = client
        p.close()
        client.close.assert_called_once()
        p.close()  # idempotent
        client.close.assert_called_once()

    def test_closing_a_real_client_does_not_raise(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "zone", "tok", {})
        p.close()
        p.__del__()


class B21NpmUpdateKeepsTlsFlagsTests(unittest.TestCase):
    def _npm(self, current: dict):
        from app.providers.npm import NPMProvider

        npm = NPMProvider("http://npm:81", "admin@example.com", "pw")
        npm._ensure_auth = MagicMock(return_value=True)
        got = MagicMock(status_code=200)
        got.json.return_value = dict(current)
        npm.session.get = MagicMock(return_value=got)
        self.sent = {}

        def _put(_url, json=None, **_kw):
            self.sent.update(json or {})
            return MagicMock(status_code=200)

        npm.session.put = MagicMock(side_effect=_put)
        return npm

    def test_flags_switched_off_by_the_operator_stay_off(self):
        current = {"id": 7, "domain_names": ["a.example.com"], "certificate_id": 4,
                   "ssl_forced": False, "http2_support": False}
        self.assertTrue(self._npm(current).update_host(7, "a.example.com", "10.0.0.5", 80, cert_id=4))
        self.assertFalse(self.sent["ssl_forced"])
        self.assertFalse(self.sent["http2_support"])

    def test_attaching_a_first_certificate_still_turns_them_on(self):
        current = {"id": 7, "domain_names": ["a.example.com"], "certificate_id": 0,
                   "ssl_forced": False, "http2_support": False}
        self._npm(current).update_host(7, "a.example.com", "10.0.0.5", 80, cert_id=4)
        self.assertTrue(self.sent["ssl_forced"])
        self.assertTrue(self.sent["http2_support"])


class B21AdguardShapeErrorsAreNoneTests(unittest.TestCase):
    def test_unexpected_bodies_are_none(self):
        from app.providers.adguard import AdGuardProvider

        for body in (None, [{"domain": "a.example.com"}], {"oops": 1}, [None]):
            with self.subTest(body=body):
                ag = AdGuardProvider("http://adguard", "u", "p")
                ag.session.get = MagicMock(return_value=_response(200, body))
                self.assertIsNone(ag.list_rewrites())
                self.assertFalse(ag.add_rewrite("a.example.com", "10.0.0.1"))


class B21TunnelDeleteKeepsPathRulesTests(unittest.TestCase):
    def test_only_the_host_wide_rule_is_removed(self):
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        p = CloudflareTunnelProvider("", "acc", "tok", {"tunnel_id": "t1"})
        config = {
            "ingress": [
                {"hostname": "app.example.com", "path": "/admin", "service": "http://admin:9000"},
                {"hostname": "app.example.com", "service": "http://app:3000"},
                {"service": "http_status:404"},
            ]
        }
        p._get_configuration = MagicMock(return_value=config)
        p._put_configuration = MagicMock(return_value=True)
        self.assertTrue(p._delete_ingress_rule("app.example.com"))
        ingress = p._put_configuration.call_args.args[0]["ingress"]
        self.assertEqual(ingress, [
            {"hostname": "app.example.com", "path": "/admin", "service": "http://admin:9000"},
            {"service": "http_status:404"},
        ])


if __name__ == "__main__":
    unittest.main()
