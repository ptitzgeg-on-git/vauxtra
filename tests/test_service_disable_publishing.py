"""A disabled service must not stay published.

Vauxtra used to write the `enabled` flag and stop monitoring the host, while the thing
that actually exposes it -- a Cloudflare tunnel ingress rule, or a DNS record -- was left
in place, or even re-published by the very request that disabled the service. From the
operator's side the UI said "off" and the hostname answered from the internet.

Four paths are covered here: PUT on a tunnel service, PUT on an already-disabled
proxy_dns service, the bulk enable/disable action, and creation with `enabled=false`.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import services as services_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _RecordingProvider:
    """Records every provider call instead of talking to anything."""

    def __init__(self, calls: list):
        self._calls = calls

    def _record(self, name, *args):
        self._calls.append((name, *args))

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._record("create_host", domain)
        return {"id": 4242}

    def update_host(self, *args):
        self._record("update_host", args[0], args[1] if len(args) > 1 else None)
        return True

    def delete_host(self, host):
        self._record("delete_host", host)
        return True

    def toggle_host(self, host_id, enabled):
        self._record("toggle_host", host_id, enabled)
        return True

    def add_rewrite(self, host, ip):
        self._record("add_rewrite", host, ip)
        return True

    def delete_rewrite(self, host, ip):
        self._record("delete_rewrite", host, ip)
        return True

    def update_rewrite(self, old_host, old_ip, host, ip):
        self._record("update_rewrite", old_host, host)
        return True

    def find_best_certificate(self, _domain):
        return None


class ServiceExposureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "services.exposure.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        self.calls: list = []
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: _RecordingProvider(self.calls)),
        ]
        for p in self._patchers:
            p.start()

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (1, 'CF Tunnel', 'cloudflare_tunnel', '', 'acct', 'token', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        import app.db as _app_db

        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures ---------------------------------------------------------------

    def _seed_tunnel_service(self, *, enabled: int = 1, hostname: str = "vault.example.com") -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, tunnel_provider_id, tunnel_hostname)
               VALUES ('vault', 'example.com', '10.0.0.9', 8080, 'http', 0, ?, 'tunnel', 1, ?)""",
            (enabled, hostname),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def _seed_proxy_service(self, *, enabled: int = 1) -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, proxy_provider_id, npm_host_id,
                  dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, ?, 'proxy_dns',
                       2, 77, 3, '198.51.100.7', 'manual')""",
            (enabled,),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def _tunnel_payload(self, *, enabled: bool, hostname: str = "vault.example.com"):
        return services_api.ServiceIn(
            subdomain="vault",
            domain="example.com",
            target_ip="10.0.0.9",
            target_port=8080,
            forward_scheme="http",
            websocket=False,
            enabled=enabled,
            expose_mode="tunnel",
            tunnel_provider_id=1,
            tunnel_hostname=hostname,
        )

    def _proxy_payload(self, *, enabled: bool):
        return services_api.ServiceIn(
            subdomain="app",
            domain="example.com",
            target_ip="10.0.0.9",
            target_port=8080,
            forward_scheme="http",
            websocket=False,
            enabled=enabled,
            expose_mode="proxy_dns",
            proxy_provider_id=2,
            dns_provider_id=3,
            dns_ip="198.51.100.7",
            public_target_mode="manual",
        )

    def _names(self) -> list[str]:
        return [c[0] for c in self.calls]


class TestTunnelUpdate(ServiceExposureTestCase):

    def test_disabling_withdraws_the_ingress_rule(self):
        sid = self._seed_tunnel_service()
        services_api.update_service(sid, _request("PUT"), self._tunnel_payload(enabled=False))

        self.assertIn(("delete_host", "vault.example.com"), self.calls)
        self.assertNotIn("create_host", self._names())
        self.assertNotIn("update_host", self._names())

    def test_enabling_publishes_it_again(self):
        sid = self._seed_tunnel_service(enabled=0)
        services_api.update_service(sid, _request("PUT"), self._tunnel_payload(enabled=True))

        self.assertIn("update_host", self._names())
        self.assertNotIn("delete_host", self._names())

    def test_renaming_while_disabling_withdraws_both_hostnames(self):
        """The route lives under the old hostname; the PUT carries the new one."""
        sid = self._seed_tunnel_service()
        services_api.update_service(
            sid, _request("PUT"), self._tunnel_payload(enabled=False, hostname="secret.example.com")
        )

        self.assertIn(("delete_host", "vault.example.com"), self.calls)
        self.assertIn(("delete_host", "secret.example.com"), self.calls)

    def test_the_row_still_records_the_hostname_so_it_can_be_republished(self):
        sid = self._seed_tunnel_service()
        services_api.update_service(sid, _request("PUT"), self._tunnel_payload(enabled=False))

        conn = models.get_db()
        row = conn.execute("SELECT enabled, tunnel_hostname FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["tunnel_hostname"], "vault.example.com")


class TestProxyDnsUpdate(ServiceExposureTestCase):

    def test_editing_an_already_disabled_service_does_not_recreate_the_record(self):
        """No enabled transition happens here, so nothing used to undo the add_rewrite."""
        sid = self._seed_proxy_service(enabled=0)
        services_api.update_service(sid, _request("PUT"), self._proxy_payload(enabled=False))

        self.assertNotIn("add_rewrite", self._names())
        self.assertNotIn("update_rewrite", self._names())
        self.assertIn(("delete_rewrite", "app.example.com", "198.51.100.7"), self.calls)

    def test_enabling_adds_the_record_exactly_once(self):
        sid = self._seed_proxy_service(enabled=0)
        services_api.update_service(sid, _request("PUT"), self._proxy_payload(enabled=True))

        # One push, not one from the update branch plus one from the enable branch.
        self.assertEqual(self._names().count("add_rewrite") + self._names().count("update_rewrite"), 1)
        self.assertNotIn("delete_rewrite", self._names())

    def test_disabling_removes_the_record_exactly_once(self):
        sid = self._seed_proxy_service()
        services_api.update_service(sid, _request("PUT"), self._proxy_payload(enabled=False))

        self.assertEqual(self._names().count("delete_rewrite"), 1)


class TestBulkAction(ServiceExposureTestCase):

    def test_bulk_disable_withdraws_a_tunnel_route(self):
        sid = self._seed_tunnel_service()
        services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action="disable"), _request("POST")
        )

        self.assertIn(("delete_host", "vault.example.com"), self.calls)

    def test_bulk_enable_republishes_a_tunnel_route(self):
        sid = self._seed_tunnel_service(enabled=0)
        services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action="enable"), _request("POST")
        )

        self.assertIn(("create_host", "vault.example.com"), self.calls)

    def test_bulk_disable_still_handles_proxy_dns(self):
        sid = self._seed_proxy_service()
        services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action="disable"), _request("POST")
        )

        self.assertIn(("toggle_host", 77, False), self.calls)
        self.assertIn(("delete_rewrite", "app.example.com", "198.51.100.7"), self.calls)


class TestCreation(ServiceExposureTestCase):

    def test_a_tunnel_service_created_disabled_is_not_published(self):
        services_api.add_service(_request("POST"), self._tunnel_payload(enabled=False))
        self.assertEqual(self.calls, [])

    def test_a_proxy_service_created_disabled_is_not_published(self):
        services_api.add_service(_request("POST"), self._proxy_payload(enabled=False))
        self.assertEqual(self.calls, [])

    def test_the_resolved_dns_target_is_still_stored_for_the_enable_path(self):
        """Withholding the push must not lose the address, or enabling would find nothing."""
        services_api.add_service(_request("POST"), self._proxy_payload(enabled=False))

        conn = models.get_db()
        row = conn.execute("SELECT dns_ip, npm_host_id, enabled FROM services").fetchone()
        conn.close()
        self.assertEqual(row["dns_ip"], "198.51.100.7")
        self.assertIsNone(row["npm_host_id"])
        self.assertEqual(row["enabled"], 0)

    def test_an_enabled_service_is_published_as_before(self):
        services_api.add_service(_request("POST"), self._proxy_payload(enabled=True))
        self.assertIn("create_host", self._names())
        self.assertIn("add_rewrite", self._names())


if __name__ == "__main__":
    unittest.main(verbosity=2)
