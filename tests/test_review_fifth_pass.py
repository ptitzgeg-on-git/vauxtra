"""Regression tests for the points left open after the fourth review pass.

H1 (connection-time SSRF guard, embedded IPv4 forms), H3 (restore of services without an
id), M5 (the 409 after a former target was withdrawn), M6 (`dns_ip` stored only once the
DNS provider took it), the Cloudflare lookup outside every zone (404, not 502) and the
base image versions quoted in comments. Fixtures are loaded by path, as in
`test_review_fourth_pass.py`.
"""

import glob
import importlib.util
import ipaddress
import os
import re
import socket
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from app import public_target, webhook_targets
from app.providers.base import embedded_ipv4_addresses, is_blocked_address

_here = os.path.dirname(__file__)
_root = os.path.dirname(_here)

_sec_spec = importlib.util.spec_from_file_location(
    "_security_fixtures_fifth", os.path.join(_here, "test_review_security_fixes.py")
)
_sec = importlib.util.module_from_spec(_sec_spec)
_sec_spec.loader.exec_module(_sec)

_ms_spec = importlib.util.spec_from_file_location(
    "_multisync_fixtures_fifth", os.path.join(_here, "test_multi_sync_targets.py")
)
_ms = importlib.util.module_from_spec(_ms_spec)
_ms_spec.loader.exec_module(_ms)

_PUBLIC = ["93.184.216.34"]
_WAN = "93.184.216.34"


def _answer(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]


# ------------------------------------------------------------- H1: embedded IPv4 forms


class H1EmbeddedIpv4FormsTests(unittest.TestCase):
    def test_every_form_yields_the_address_it_carries(self):
        cases = {
            "::7f00:1": "127.0.0.1",                                # IPv4-compatible
            "64:ff9b::a00:1": "10.0.0.1",                           # NAT64 /96
            "64:ff9b:1:7f00:0:100::": "127.0.0.1",                  # NAT64 local-use /48
            "2001:0:4136:e378:8000:63bf:f5ff:fffe": "10.0.0.1",     # Teredo client
            "2002:a00:1::": "10.0.0.1",                             # 6to4
            "::ffff:10.0.0.1": "10.0.0.1",                          # IPv4-mapped
        }
        for v6, v4 in cases.items():
            with self.subTest(v6=v6):
                found = embedded_ipv4_addresses(ipaddress.ip_address(v6))
                self.assertIn(ipaddress.IPv4Address(v4), found)

    def test_loopback_and_unspecified_are_not_ipv4_compatible(self):
        self.assertEqual(embedded_ipv4_addresses(ipaddress.ip_address("::1")), [])
        self.assertEqual(embedded_ipv4_addresses(ipaddress.ip_address("::")), [])

    def test_metadata_behind_each_form_is_blocked_for_providers(self):
        for v6 in ("::a9fe:a9fe", "64:ff9b:1:a9fe:a9:fe00::",
                   "2001:0:4136:e378:8000:63bf:5601:5601"):
            with self.subTest(v6=v6):
                self.assertTrue(is_blocked_address(v6))

    def test_a_webhook_on_a_lan_address_behind_nat64_or_compat_is_local(self):
        for v6 in ("::7f00:1", "64:ff9b::a00:1", "64:ff9b:1:7f00:0:100::"):
            with self.subTest(v6=v6):
                self.assertTrue(webhook_targets.host_is_local(v6))
        self.assertFalse(webhook_targets.host_is_local("64:ff9b::5db8:d822"))  # 93.184.216.34


# ---------------------------------------------------------- H1: connection-time guard


class H1ConnectionGuardTests(unittest.TestCase):
    def test_a_local_answer_is_refused_inside_the_guard_only(self):
        with patch.object(webhook_targets, "_original_getaddrinfo", return_value=_answer("10.0.0.5")):
            with webhook_targets.refuse_local_connections(True) as guard:
                with self.assertRaises(OSError):
                    socket.getaddrinfo("rebinding.example.com", 443)
            self.assertTrue(guard.refused)
            # Outside the block, and with the guard off, the same answer goes through.
            self.assertEqual(socket.getaddrinfo("rebinding.example.com", 443)[0][4][0], "10.0.0.5")
            with webhook_targets.refuse_local_connections(False) as off:
                socket.getaddrinfo("rebinding.example.com", 443)
            self.assertFalse(off.refused)

    def test_a_public_answer_passes(self):
        with patch.object(webhook_targets, "_original_getaddrinfo", return_value=_answer(_WAN)):
            with webhook_targets.refuse_local_connections(True) as guard:
                socket.getaddrinfo("ntfy.example.com", 443)
        self.assertFalse(guard.refused)

    def test_a_nat64_answer_carrying_a_lan_address_is_refused(self):
        with patch.object(webhook_targets, "_original_getaddrinfo", return_value=_answer("64:ff9b::a00:1")):
            with webhook_targets.refuse_local_connections(True) as guard:
                with self.assertRaises(OSError):
                    socket.getaddrinfo("ntfy.example.com", 443)
        self.assertTrue(guard.refused)

    def test_apprise_is_switched_to_sequential_sends(self):
        import apprise

        a = apprise.Apprise()
        a.add("ntfy://ntfy.example.com/one")
        a.add("ntfy://ntfy.example.com/two")
        with webhook_targets.refuse_local_connections(True, a):
            self.assertTrue(all(not instance.asset.async_mode for instance in a))


class H1SendTimeRefusalTests(_sec._WithKeys):
    """A redirect or a discovery lookup that leads inside the network, during the send."""

    def _notify_via_lan(self, *_args, **_kwargs):
        # What apprise does on a redirect: open a new connection, to wherever it points.
        try:
            socket.getaddrinfo("internal.example.com", 80)
        except OSError:
            return False
        return True

    def _pending(self) -> int:
        conn = _sec.models.get_db()
        try:
            return conn.execute("SELECT COUNT(*) FROM webhook_delivery_log").fetchone()[0]
        finally:
            conn.close()

    def test_an_unapproved_webhook_is_refused_and_not_queued(self):
        import apprise

        from app import scheduler

        self._exec("INSERT INTO webhooks (id, name, url, enabled, local_target_allowed) "
                   "VALUES (9, 'w', 'ntfy://ntfy.example.com/t', 1, 0)")
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC), \
             patch.object(webhook_targets, "_original_getaddrinfo", return_value=_answer("192.168.1.5")), \
             patch.object(apprise.Apprise, "notify", self._notify_via_lan), \
             patch.object(scheduler, "add_log") as log:
            self.assertFalse(scheduler._try_send_apprise("ntfy://ntfy.example.com/t", "t", "b", webhook_id=9))
        self.assertEqual(self._pending(), 0)
        self.assertTrue(any("inside the local network" in str(c) for c in log.call_args_list))

    def test_an_admin_approved_webhook_may_reach_the_lan(self):
        import apprise

        from app import scheduler

        self._exec("INSERT INTO webhooks (id, name, url, enabled, local_target_allowed) "
                   "VALUES (10, 'a', 'ntfy://ntfy.example.com/a', 1, 1)")
        with patch.object(webhook_targets, "_original_getaddrinfo", return_value=_answer("192.168.1.5")), \
             patch.object(apprise.Apprise, "notify", self._notify_via_lan):
            self.assertTrue(scheduler._try_send_apprise("ntfy://ntfy.example.com/a", "t", "b", webhook_id=10))

    def test_the_test_route_answers_403_for_a_redirect_into_the_lan(self):
        import apprise

        self._exec("INSERT INTO webhooks (id, name, url, enabled, local_target_allowed) "
                   "VALUES (9, 'w', 'ntfy://ntfy.example.com/t', 1, 0)")
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC), \
             patch.object(webhook_targets, "_original_getaddrinfo", return_value=_answer("192.168.1.5")), \
             patch.object(apprise.Apprise, "notify", self._notify_via_lan):
            r = self.client.post("/api/webhooks/9/test", headers=self._h("rw"))
            self.assertEqual(r.status_code, 403, r.text)
            r = self.client.post("/api/webhooks/test-url", headers=self._h("rw"),
                                 json={"url": "ntfy://ntfy.example.com/t"})
            self.assertEqual(r.status_code, 403, r.text)


# ------------------------------------------------------------------------------- H3


class H3RestoreOfServicesWithoutAnIdTests(_sec._WithKeys):
    def test_a_row_without_an_id_does_not_take_a_later_rows_id(self):
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        backup["tags"] = [{"id": 1, "name": "prod", "color": "#fff"}]
        backup["services"] = [
            {"subdomain": "first", "domain": "example.com", "target_ip": "10.0.0.1", "target_port": 80},
            {"id": 1, "subdomain": "second", "domain": "example.com", "target_ip": "10.0.0.2", "target_port": 80},
        ]
        backup["service_tags"] = [{"service_id": 1, "tag_id": 1}]
        r = self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})
        self.assertEqual(r.status_code, 200, r.text)
        conn = _sec.models.get_db()
        try:
            rows = {row["subdomain"]: row["id"] for row in conn.execute("SELECT id, subdomain FROM services")}
            tags = [tuple(row) for row in conn.execute("SELECT service_id, tag_id FROM service_tags")]
        finally:
            conn.close()
        self.assertEqual(set(rows), {"first", "second"})
        self.assertEqual(rows["second"], 1)
        self.assertNotEqual(rows["first"], 1)
        self.assertEqual(tags, [(1, 1)])


# ------------------------------------------------------------------------------- M5, M6


class _Routes(_ms._MultiSyncTestCase):
    def _row(self, sid: int):
        conn = _ms.models.get_db()
        try:
            return conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        finally:
            conn.close()

    def _update(self, sid: int, **overrides):
        return _ms.services_api.update_service(sid, _ms._request("PUT"), self._body(**overrides))

    def _set_auto(self, sid: int) -> None:
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto' WHERE id=?", (sid,))
        conn.commit()
        conn.close()


class M5ConflictAfterWithdrawalTests(_Routes):
    def test_the_409_says_the_former_target_was_already_withdrawn(self):
        from fastapi import HTTPException

        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        status, body = self._create(dns_provider_id=2, extra_dns_provider_ids=[3])
        self.assertEqual(status, 201, body)
        original_get_db = _ms.services_api.get_db

        class _Conn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *args):
                if sql.lstrip().startswith("UPDATE services SET\n"):
                    raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
                return self._inner.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        with patch.object(_ms.services_api, "get_db", lambda: _Conn(original_get_db())), \
             self.assertRaises(HTTPException) as caught:
            self._update(body["id"], dns_provider_id=2, extra_dns_provider_ids=[])
        detail = caught.exception.detail
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn("Nothing was changed", detail)
        self.assertIn("already withdrawn from 1 former target", detail)
        self.assertFalse(self.providers[3].holds("vault.example.com"))


class M6DnsIpStoredOnlyAfterSuccessTests(_Routes):
    def setUp(self) -> None:
        super().setUp()
        self.dns = self._add_provider(2, "AdGuard", "adguard")

    def test_update_keeps_the_address_when_the_provider_refuses_the_move(self):
        status, body = self._create(dns_provider_id=2, dns_ip="198.51.100.7")
        self.assertEqual(status, 201, body)
        self._set_auto(body["id"])
        self.dns.update_rewrite = lambda *_a: False
        with patch.object(public_target, "_detect_server_public_ip", return_value=_WAN):
            self._update(body["id"], dns_provider_id=2, public_target_mode="auto", dns_ip="")
        self.assertEqual(self._row(body["id"])["dns_ip"], "198.51.100.7")

    def test_update_stores_the_address_the_provider_took(self):
        status, body = self._create(dns_provider_id=2, dns_ip="198.51.100.7")
        self._set_auto(body["id"])
        with patch.object(public_target, "_detect_server_public_ip", return_value=_WAN):
            self._update(body["id"], dns_provider_id=2, public_target_mode="auto", dns_ip="")
        self.assertEqual(self._row(body["id"])["dns_ip"], _WAN)

    def test_a_manual_address_is_kept_as_typed_for_the_push_to_retry(self):
        status, body = self._create(dns_provider_id=2, dns_ip="198.51.100.7")
        self.dns.update_rewrite = lambda *_a: False
        self._update(body["id"], dns_provider_id=2, dns_ip="198.51.100.8")
        self.assertEqual(self._row(body["id"])["dns_ip"], "198.51.100.8")

    def test_create_clears_an_automatic_address_no_server_took(self):
        self.dns.ok = False
        with patch.object(public_target, "_detect_server_public_ip", return_value=_WAN):
            status, body = self._create(dns_provider_id=2, public_target_mode="auto", dns_ip="")
        self.assertEqual(status, 207, body)
        self.assertEqual(self._row(body["id"])["dns_ip"], "")

    def _bulk(self, sid: int, action: str):
        return _ms.services_api.bulk_action(
            _ms.services_api._BulkActionBody(ids=[sid], action=action), _ms._request()
        )

    def test_bulk_enable_stores_a_refreshed_address_only_once_published(self):
        status, body = self._create(dns_provider_id=2, dns_ip="198.51.100.7")
        sid = body["id"]
        self._bulk(sid, "disable")
        self._set_auto(sid)
        self.dns.ok = False
        with patch.object(public_target, "_detect_server_public_ip", return_value=_WAN):
            self._bulk(sid, "enable")
        self.assertEqual(self._row(sid)["dns_ip"], "198.51.100.7")

        self._bulk(sid, "disable")
        self.dns.ok = True
        with patch.object(public_target, "_detect_server_public_ip", return_value=_WAN):
            self._bulk(sid, "enable")
        self.assertEqual(self._row(sid)["dns_ip"], _WAN)
        self.assertIn({"domain": "vault.example.com", "answer": _WAN}, self.dns.rewrites)


# -------------------------------------------------------------- Cloudflare, no zone


def _cloudflare(client):
    import app.providers.cloudflare as cf_module

    module = MagicMock()
    module.Cloudflare.return_value = client
    with patch.object(cf_module, "_HAS_CF", True), patch.object(cf_module, "_cf", module):
        provider = cf_module.CloudflareProvider("https://api.cloudflare.com/client/v4", "", "tok", {})
    provider._client = client
    return provider


class CloudflareNameOutsideEveryZoneTests(unittest.TestCase):
    def test_no_zone_is_no_record_and_a_failed_lookup_is_unknown(self):
        client = MagicMock()
        client.zones.list.return_value = []
        self.assertEqual(_cloudflare(client).lookup_rewrites("app.nowhere.test"), [])
        client.zones.list.side_effect = RuntimeError("API down")
        self.assertIsNone(_cloudflare(client).lookup_rewrites("app.nowhere.test"))

    def test_the_delete_route_answers_404(self):
        from fastapi import HTTPException

        from app.api import providers as providers_api

        client = MagicMock()
        client.zones.list.return_value = []
        row = {"id": 1, "type": "cloudflare", "name": "CF"}
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row
        with patch.object(providers_api, "require_auth", lambda *_a, **_k: None), \
             patch.object(providers_api, "get_db", return_value=conn), \
             patch.object(providers_api, "create_provider", return_value=_cloudflare(client)), \
             self.assertRaises(HTTPException) as caught:
            providers_api.delete_dns_record(1, "app.nowhere.test", _ms._request("DELETE"))
        self.assertEqual(caught.exception.status_code, 404)


# ------------------------------------------------------------------- version comments


class BaseImageVersionsInCommentsTests(unittest.TestCase):
    def test_comments_quote_the_images_the_dockerfile_uses(self):
        with open(os.path.join(_root, "Dockerfile"), encoding="utf-8") as fh:
            dockerfile = fh.read()
        used = set(re.findall(r"^FROM\s+((?:python|node):[\w.-]+)", dockerfile, re.M))
        self.assertTrue(used)
        files = [os.path.join(_root, "Dockerfile"), *glob.glob(os.path.join(_root, ".github", "**", "*.yml"), recursive=True)]
        for path in files:
            with open(path, encoding="utf-8") as fh:
                text = re.sub(r"\s*#\s*", " ", fh.read())  # join wrapped comment lines
            for image in re.findall(r"\b((?:python|node):\s*[\d.]+-slim)", text):
                with self.subTest(path=os.path.relpath(path, _root), image=image):
                    self.assertIn(image.replace(" ", ""), used)
            with self.subTest(path=path):
                self.assertNotIn("python3.13", text)


if __name__ == "__main__":
    unittest.main()
