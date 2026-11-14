"""The one probe (`app/probe.py`): what a tunnel route's status is measured from.

Measured on a 1.6.1 instance on 2026-11-14: twenty tunnel routes read "Error, check the
tunnel connector" while every one of them answered through Cloudflare. Their targets are
`localhost:<port>` as seen by `cloudflared`, the per-row button dialled that from the Vauxtra
container, and the scheduler skipped tunnels and never wrote anything over the result.
These tests pin the replacement: the public name, through the edge, classified.
"""

import json
import os
import re
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.db as db
from app import models, probe

_ROOT = Path(__file__).resolve().parent.parent
_UPTIME_TS = _ROOT / "frontend" / "src" / "components" / "features" / "monitoring" / "uptime.ts"
_EN_JSON = _ROOT / "frontend" / "src" / "locales" / "en.json"


class ClassifyEdgeAnswerTests(unittest.TestCase):
    def test_a_redirect_to_access_is_a_working_route_behind_access(self):
        self.assertEqual(
            probe.classify_edge_answer(302, "https://team.cloudflareaccess.com/cdn-cgi/access/login/x"),
            ("ok", "edge_access"),
        )

    def test_530_is_the_edge_saying_no_connector_holds_the_tunnel(self):
        self.assertEqual(probe.classify_edge_answer(530), ("error", "edge_tunnel_down"))

    def test_502_and_504_are_the_connector_failing_to_reach_the_origin(self):
        self.assertEqual(probe.classify_edge_answer(502), ("error", "edge_origin_down"))
        self.assertEqual(probe.classify_edge_answer(504), ("error", "edge_origin_down"))

    def test_another_5xx_is_the_origin_itself(self):
        self.assertEqual(probe.classify_edge_answer(500), ("error", "edge_origin_error"))

    def test_an_answer_below_500_means_the_route_works(self):
        """A 401 or a 404 is the application answering: the route reached it."""
        for code in (200, 204, 301, 401, 403, 404):
            self.assertEqual(probe.classify_edge_answer(code, "/login"), ("ok", "edge_ok"), code)


class CloudflareRangesTests(unittest.TestCase):
    def test_the_addresses_a_proxied_name_resolves_to_are_cloudflare(self):
        # What `listo.xeno.homes` resolved to from the instance, both families.
        for addr in ("188.114.96.3", "104.21.3.4", "2a06:98c1:3120::3", "2606:4700::6810:84e5"):
            self.assertTrue(probe.is_cloudflare_ip(addr), addr)

    def test_a_lan_or_foreign_address_is_not(self):
        for addr in ("10.0.10.5", "192.168.1.1", "8.8.8.8", "2001:db8::1", "not-an-ip"):
            self.assertFalse(probe.is_cloudflare_ip(addr), addr)


class EdgeProbeTests(unittest.TestCase):
    def test_a_name_that_resolves_to_cloudflare_is_requested_there(self):
        dialled: list = []

        def head(address, hostname, timeout):
            dialled.append((address, hostname))
            return 302, "https://team.cloudflareaccess.com/cdn-cgi/access/login"

        with patch.object(probe, "_resolve_local", lambda _h: ["2a06:98c1:3120::3", "188.114.96.3"]), \
             patch.object(probe, "_https_head_of", head):
            result = probe.edge_probe("Listo.Example.com.")

        # IPv4 first: a container without IPv6 egress would burn the timeout on the other.
        self.assertEqual(dialled, [("188.114.96.3", "listo.example.com")])
        self.assertEqual((result.status, result.stored_detail), ("ok", "edge_access:302"))
        self.assertIsInstance(result.latency_ms, float)

    def test_a_split_horizon_answer_falls_back_to_the_public_one(self):
        """An AdGuard rewrite to a LAN address must not turn the tunnel route red."""
        dialled: list = []

        def head(address, hostname, timeout):
            dialled.append(address)
            return 200, ""

        with patch.object(probe, "_resolve_local", lambda _h: ["10.0.10.5"]), \
             patch.object(probe, "_resolve_public", lambda _h: ["104.21.3.4"]), \
             patch.object(probe, "_https_head_of", head):
            result = probe.edge_probe("app.example.com")

        self.assertEqual(dialled, ["104.21.3.4"])
        self.assertEqual(result.stored_detail, "edge_ok:200")

    def test_a_name_that_never_reaches_cloudflare_says_so(self):
        with patch.object(probe, "_resolve_local", lambda _h: ["10.0.10.5"]), \
             patch.object(probe, "_resolve_public", lambda _h: ["203.0.113.7"]):
            result = probe.edge_probe("app.example.com")
        self.assertEqual((result.status, result.detail), ("error", "dns_not_cloudflare"))

    def test_a_name_that_does_not_resolve_says_so(self):
        with patch.object(probe, "_resolve_local", lambda _h: []), \
             patch.object(probe, "_resolve_public", lambda _h: []):
            result = probe.edge_probe("gone.example.com")
        self.assertEqual((result.status, result.detail), ("error", "dns_unresolved"))

    def test_an_edge_that_refuses_tls_is_unreachable(self):
        def head(*_args):
            raise ssl.SSLError("handshake failure")

        with patch.object(probe, "_resolve_local", lambda _h: ["188.114.96.3", "188.114.97.3"]), \
             patch.object(probe, "_https_head_of", head):
            result = probe.edge_probe("app.example.com")
        self.assertEqual((result.status, result.detail), ("error", "edge_unreachable"))

    def test_no_name_is_not_a_failure(self):
        result = probe.edge_probe("  ")
        self.assertEqual((result.status, result.detail, result.tested), ("unknown", "no_hostname", False))


class ProbeServiceTests(unittest.TestCase):
    def test_a_tunnel_route_is_probed_by_its_public_name(self):
        row = {"expose_mode": "tunnel", "tunnel_hostname": "Board.Example.com",
               "subdomain": "board", "domain": "example.com",
               "target_ip": "localhost", "target_port": 8080}
        seen: list = []
        result = probe.probe_service(
            row,
            tcp=lambda *_a: self.fail("a tunnel target was dialled"),
            edge=lambda h: seen.append(h) or probe.ProbeResult("ok", "edge_ok"),
        )
        self.assertEqual(seen, ["board.example.com"])
        self.assertEqual(result.status, "ok")

    def test_a_tunnel_route_without_its_own_name_uses_the_service_name(self):
        row = {"expose_mode": "tunnel", "tunnel_hostname": "", "subdomain": "board",
               "domain": "example.com", "target_ip": "localhost", "target_port": 8080}
        self.assertEqual(probe.public_hostname(row), "board.example.com")

    def test_anything_else_is_dialled(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            row = {"expose_mode": "proxy_dns", "target_ip": "127.0.0.1",
                   "target_port": listener.getsockname()[1]}
            result = probe.probe_service(row)
        finally:
            listener.close()
        self.assertEqual((result.status, result.detail), ("ok", "tcp_open"))

    def test_no_port_is_not_probed(self):
        result = probe.probe_service({"expose_mode": "dns_only", "target_ip": "10.0.0.5", "target_port": 0})
        self.assertEqual((result.status, result.tested), ("unknown", False))

    def test_many_keep_their_order(self):
        rows = [{"n": n} for n in range(20)]
        out = probe.probe_many(rows, probe=lambda r: probe.ProbeResult("ok", str(r["n"])))
        self.assertEqual([res.detail for _row, res in out], [str(n) for n in range(20)])


class DefaultCheckIntervalTests(unittest.TestCase):
    """Nothing ever wrote `check_interval`, so the cycle never started on its own."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)
        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "interval.test.db")

    def tearDown(self) -> None:
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _interval(self):
        conn = models.get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='check_interval'").fetchone()
        conn.close()
        return row["value"] if row else None

    def test_an_instance_nobody_configured_checks_every_five_minutes(self):
        models.init_db()
        self.assertEqual(self._interval(), "5")

    def test_an_explicit_off_survives_the_next_start(self):
        models.init_db()
        conn = models.get_db()
        conn.execute("UPDATE settings SET value='0' WHERE key='check_interval'")
        conn.commit()
        conn.close()
        models.init_db()
        self.assertEqual(self._interval(), "0")


class DetailCaptionParityTests(unittest.TestCase):
    """Every code a probe can store has a sentence in the panel, and no sentence outlives its code.

    `status_detail` travels as a bare code and the panel turns it into words through
    `DETAIL_KEY` in `uptime.ts`. A code added here with no entry there leaves the line under the
    badge empty, in every language at once, and nothing else notices. The other locales follow
    from `check-locale-parity.mjs`, which holds them to en.json.
    """

    @classmethod
    def setUpClass(cls):
        source = _UPTIME_TS.read_text(encoding="utf-8")
        block = re.search(r"export const DETAIL_KEY[^=]*=\s*\{(.*?)^\};", source, re.S | re.M)
        if block is None:
            raise AssertionError("DETAIL_KEY not found in uptime.ts")
        cls.keys = dict(re.findall(r"^\s*([a-z_]+):\s*'([a-z0-9_.]+)'", block.group(1), re.M))
        cls.en = json.loads(_EN_JSON.read_text(encoding="utf-8"))

    def test_every_code_has_a_caption_in_english(self):
        for code in sorted(probe.DETAIL_CODES):
            with self.subTest(code=code):
                self.assertIn(code, self.keys)
                self.assertTrue(self.en.get(self.keys[code]), self.keys[code])

    def test_no_caption_waits_for_a_code_the_backend_never_sends(self):
        self.assertEqual(set(self.keys), set(probe.DETAIL_CODES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
