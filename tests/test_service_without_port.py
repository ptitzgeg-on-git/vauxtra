"""A service published in DNS alone has no port, and nothing probes it.

Measured in production on 2026-09-22, after "check all": 3 services reported down, and two
of the three were records with no port at all -- an A record for a WireGuard endpoint, which
speaks UDP, and a CNAME to a Cloudflare tunnel, which only answers from the edge. The import
had written port 80 for each, a port it read nowhere, and every probe since dialled it and
wrote "down".

A port of 0 now means "no port". The import writes it for a record found alone. It is
accepted for a service that is a name in DNS and nothing else, refused as soon as a proxy or
a tunnel would forward to it, and never probed: not by the health cycle, not by "check all",
not by the per-row check, not by the preflight.
"""

import contextlib
import os
import socket
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from starlette.requests import Request

from app import models, scheduler
from app.api import services as services_api
from app.api import sync as sync_api
from app.api.services import ServiceIn


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


# What the import writes for a DNS record found alone: a DNS provider, the record's address,
# and no proxy. `add_service` and `update_service` refuse a service with neither target.
DNS_ONLY = {
    "subdomain": "vpn", "domain": "example.com", "target_ip": "10.0.0.5", "target_port": 0,
    "dns_provider_id": 1, "dns_ip": "10.0.0.5",
}


class _FakeDns:
    """Accepts every write and writes each one down."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def test_connection(self) -> bool:
        return True

    def list_rewrites(self):
        return [{"domain": "vpn.example.com", "answer": "10.0.0.5"}]

    def add_rewrite(self, domain, ip):
        self.calls.append(("add", domain, ip))
        return True

    def delete_rewrite(self, domain, ip):
        self.calls.append(("delete", domain, ip))
        return True

    def update_rewrite(self, old_domain, old_ip, new_domain, new_ip):
        self.calls.append(("update", old_domain, old_ip, new_domain, new_ip))
        return True


class TheModelAcceptsNoPortOnlyWhereNothingForwardsTests(unittest.TestCase):
    def test_a_service_published_in_dns_alone_may_have_no_port(self) -> None:
        self.assertEqual(ServiceIn(**DNS_ONLY).target_port, 0)

    def test_no_port_behind_a_proxy_is_refused(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            ServiceIn(**DNS_ONLY, proxy_provider_id=2)
        self.assertIn("A port is required", str(caught.exception))

    def test_no_port_behind_an_extra_proxy_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            ServiceIn(**DNS_ONLY, extra_proxy_provider_ids=[3])

    def test_no_port_in_tunnel_mode_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            ServiceIn(**DNS_ONLY, expose_mode="tunnel", tunnel_provider_id=4)

    def test_a_port_behind_a_proxy_is_still_accepted(self) -> None:
        """The witness: a rule that refused every proxied service would pass the two above."""
        self.assertEqual(ServiceIn(**{**DNS_ONLY, "target_port": 8080}, proxy_provider_id=2).target_port, 8080)

    def test_below_zero_and_above_the_last_port_are_still_refused(self) -> None:
        for port in (-1, 65536):
            with self.assertRaises(ValidationError, msg=f"port {port} was accepted"):
                ServiceIn(**{**DNS_ONLY, "target_port": port})


class _ServicesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "noport.test.db")
        models.init_db()

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (1, 'AdGuard', 'adguard', 'http://x', 'admin', 'p', '{}', 1)"""
        )
        conn.commit()
        conn.close()
        self.dns = _FakeDns()

        for p in (
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: self.dns),
        ):
            p.start()
            self.addCleanup(p.stop)

        scheduler._provider_last_status.clear()
        scheduler._dns_update_failures.clear()
        scheduler._cert_alert_state.clear()
        self.addCleanup(scheduler._provider_last_status.clear)
        self.addCleanup(scheduler._dns_update_failures.clear)
        self.addCleanup(scheduler._cert_alert_state.clear)

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures -------------------------------------------------------------------------
    def _seed(self, subdomain: str, port: int, *, status: str = "unknown", mode: str = "proxy_dns") -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services (subdomain, domain, target_ip, target_port, enabled, status,
                                     expose_mode, dns_provider_id, dns_ip)
               VALUES (?, 'example.com', '10.0.0.5', ?, 1, ?, ?, 1, '10.0.0.5')""",
            (subdomain, port, status, mode),
        )
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        return sid

    def _status(self, sid: int) -> str:
        conn = models.get_db()
        row = conn.execute("SELECT status FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return row["status"]

    def _events(self, sid: int) -> list[str]:
        conn = models.get_db()
        rows = conn.execute(
            "SELECT status FROM uptime_events WHERE service_id=? ORDER BY id", (sid,)
        ).fetchall()
        conn.close()
        return [r["status"] for r in rows]

    @contextlib.contextmanager
    def _sockets(self):
        """Every connection opened succeeds, and its port is written down."""
        dialled: list[int] = []

        def connect(address, timeout=None):
            dialled.append(address[1])
            return contextlib.nullcontext()

        def resolve(*_args, **_kwargs):
            raise socket.gaierror("no resolution in tests")

        with patch.object(socket, "create_connection", connect), \
             patch.object(socket, "getaddrinfo", resolve):
            yield dialled


class TheImportWritesNoPortForARecordFoundAloneTests(_ServicesTestCase):
    """Where the 80 came from: a DNS record names an address, and the import made up a port."""

    def setUp(self) -> None:
        super().setUp()
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'p', '{}', 1)"""
        )
        conn.commit()
        conn.close()
        p = patch.object(sync_api, "require_auth_or_setup", lambda _req, scope=None: None)
        p.start()
        self.addCleanup(p.stop)

    def _ports(self) -> dict[str, int]:
        conn = models.get_db()
        rows = conn.execute("SELECT subdomain, target_port FROM services").fetchall()
        conn.close()
        return {r["subdomain"]: r["target_port"] for r in rows}

    def test_a_dns_record_found_alone_comes_in_without_a_port(self) -> None:
        record = {"domain": "vpn.example.com", "answer": "10.0.0.5", "_provider_id": 1,
                  "_provider_name": "AdGuard"}

        result = sync_api.import_services(_request(), {"dns_rewrites": [record]})

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._ports(), {"vpn": 0})

    def test_a_proxy_host_keeps_the_port_it_forwards_to(self) -> None:
        """The witness: an import that dropped every port would pass the one above."""
        host = {"id": 77, "domains": ["app.example.com"], "forward_host": "10.0.0.9",
                "forward_port": 8080, "forward_scheme": "http", "_provider_id": 2,
                "_provider_name": "NPM", "_provider_type": "npm"}

        result = sync_api.import_services(_request(), {"proxy_hosts": [host]})

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._ports(), {"app": 8080})


class TheHealthCycleSkipsItTests(_ServicesTestCase):
    def test_a_service_without_a_port_is_never_probed_and_never_marked_down(self) -> None:
        without = self._seed("vpn", 0)
        with_port = self._seed("app", 9001)
        dialled: list[int] = []

        def probe(_ip, port):
            dialled.append(port)
            return "ok"

        with patch.object(scheduler, "_tcp_ok", side_effect=probe), \
             patch.object(scheduler, "_sync_npm_once"), \
             patch.object(scheduler, "_run_provider_health_checks", return_value=[]), \
             patch.object(scheduler, "_fire_global_webhook"), \
             patch.object(scheduler, "_fire_service_webhooks"):
            scheduler.run_health_checks()

        self.assertEqual(dialled, [9001])
        self.assertEqual(self._events(without), [])
        self.assertEqual(self._status(without), "unknown")
        self.assertEqual(self._events(with_port), ["ok"])


class CheckAllCountsItAsSkippedTests(_ServicesTestCase):
    def test_only_the_services_with_a_port_are_dialled(self) -> None:
        without = self._seed("vpn", 0)
        tunnel = self._seed("tun", 8443, mode="tunnel")
        with_port = self._seed("app", 9001)

        with self._sockets() as dialled:
            result = services_api.check_all(_request())

        self.assertEqual(dialled, [9001])
        self.assertEqual(
            (result["checked"], result["ok"], result["error"], result["skipped"]), (3, 1, 0, 2)
        )
        # Apart, since the panel says why each one was left out.
        self.assertEqual((result["skipped_tunnel"], result["skipped_no_port"]), (1, 1))
        self.assertEqual([r["id"] for r in result["results"]], [with_port])
        self.assertEqual(self._events(without), [])
        self.assertEqual(self._events(tunnel), [])


class TheRowCheckSaysItWasNotTestedTests(_ServicesTestCase):
    def test_a_service_without_a_port_comes_back_untested_and_its_old_verdict_is_cleared(self) -> None:
        sid = self._seed("vpn", 0, status="error")

        with self._sockets() as dialled:
            result = services_api.check_service(sid, _request())

        self.assertEqual(dialled, [])
        self.assertFalse(result["tested"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self._status(sid), "unknown")
        self.assertEqual(self._events(sid), [])

    def test_a_service_with_a_port_is_still_tested(self) -> None:
        """The witness: a check that tested nothing would pass the one above."""
        sid = self._seed("app", 9001)

        with self._sockets() as dialled:
            result = services_api.check_service(sid, _request())

        self.assertEqual(dialled, [9001])
        self.assertTrue(result["tested"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self._events(sid), ["ok"])


class ThePreflightHasNothingToReachTests(_ServicesTestCase):
    def test_no_port_is_said_as_such_and_nothing_is_dialled(self) -> None:
        def _never(*_args, **_kwargs):
            raise AssertionError("the preflight dialled a service that has no port")

        with patch.object(services_api, "_service_target_reachable", _never):
            result = services_api.preflight_service(
                _request("POST", "/api/services/preflight"),
                services_api.ServicePreflightIn(**DNS_ONLY, service_id=None),
            )

        check = next(c for c in result["checks"] if c["name"] == "target_reachable")
        self.assertTrue(check["ok"])
        self.assertFalse(check["blocking"])
        self.assertEqual(check["detail_key"], "target_no_port")


class RemovingThePortClearsTheLastVerdictTests(_ServicesTestCase):
    def test_a_service_edited_to_no_port_is_no_longer_down(self) -> None:
        """Nothing will probe it again, so the "error" of its last probe would stand forever."""
        sid = self._seed("vpn", 80, status="error")

        result = services_api.update_service(sid, _request("PUT"), ServiceIn(**DNS_ONLY))

        self.assertEqual(result["target_port"], 0)
        self.assertEqual(self._status(sid), "unknown")
        # Same name, same address: the record is left exactly as it was.
        self.assertEqual(self.dns.calls, [])

    def test_a_service_edited_with_a_port_keeps_its_verdict(self) -> None:
        sid = self._seed("vpn", 80, status="error")

        services_api.update_service(sid, _request("PUT"), ServiceIn(**{**DNS_ONLY, "target_port": 81}))

        self.assertEqual(self._status(sid), "error")


if __name__ == "__main__":
    unittest.main()
