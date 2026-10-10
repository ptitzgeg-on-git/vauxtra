"""Regression tests for the fourth review pass (H1 to H4, M1 to M16, B1 to B26).

Each class names the finding it covers. The isolated-database fixtures come from
`test_review_security_fixes.py` and `test_multi_sync_targets.py`; `tests/` is not a
package, so they are loaded by path.
"""

import importlib.util
import os
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from app import public_target, webhook_targets

_here = os.path.dirname(__file__)

_sec_spec = importlib.util.spec_from_file_location(
    "_security_fixtures_fourth", os.path.join(_here, "test_review_security_fixes.py")
)
_sec = importlib.util.module_from_spec(_sec_spec)
_sec_spec.loader.exec_module(_sec)

_ms_spec = importlib.util.spec_from_file_location(
    "_multisync_fixtures_fourth", os.path.join(_here, "test_multi_sync_targets.py")
)
_ms = importlib.util.module_from_spec(_ms_spec)
_ms_spec.loader.exec_module(_ms)

_PUBLIC = ["93.184.216.34"]
_PRIVATE = ["192.168.1.5"]


# ------------------------------------------------------------------------- H1, M10, B1


class H1M10B1WhereApprisePointsTests(unittest.TestCase):
    def test_a_doubled_at_sign_counts_as_local(self):
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC):
            self.assertTrue(webhook_targets.targets_the_local_network("ntfy://a@127.0.0.1@example.com/t"))

    def test_an_smtp_relay_in_the_query_string_is_checked(self):
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC):
            self.assertTrue(webhook_targets.targets_the_local_network(
                "mailto://user:pass@example.com?smtp=10.0.0.1"
            ))
            self.assertFalse(webhook_targets.targets_the_local_network("mailto://user:pass@example.com"))

    def test_a_dotless_container_name_resolving_privately_is_local(self):
        with patch.object(webhook_targets, "_resolve", return_value=_PRIVATE):
            self.assertTrue(webhook_targets.host_is_local("gotify"))
            self.assertTrue(webhook_targets.targets_the_local_network("gotify://gotify/TOKEN"))

    def test_an_unparseable_url_fails_closed(self):
        self.assertTrue(webhook_targets.targets_the_local_network("ntfy://[::1/t"))


class H1WebhookRoutesTests(_sec._WithKeys):
    def test_a_write_key_cannot_hide_a_local_host(self):
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC):
            for url in ("ntfy://a@127.0.0.1@example.com/t",
                        "mailto://user:pass@example.com?smtp=10.0.0.1"):
                with self.subTest(url=url):
                    r = self.client.post("/api/webhooks", headers=self._h("rw"),
                                         json={"name": "x", "url": url})
                    self.assertEqual(r.status_code, 403, r.text)


# ------------------------------------------------------------------------------- M11


class M11LocalTargetsNeedAnAdminSaveTests(_sec._WithKeys):
    def _flag(self, wid: int) -> int:
        conn = _sec.models.get_db()
        try:
            return conn.execute("SELECT local_target_allowed FROM webhooks WHERE id=?", (wid,)).fetchone()[0]
        finally:
            conn.close()

    def test_a_write_key_save_is_not_allowed_local_and_an_admin_save_is(self):
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC):
            rw = self.client.post("/api/webhooks", headers=self._h("rw"),
                                  json={"name": "rw", "url": "ntfy://ntfy.example.com/t"})
            adm = self.client.post("/api/webhooks", headers=self._h("adm"),
                                   json={"name": "adm", "url": "ntfy://ntfy.example.com/t"})
        self.assertEqual(rw.status_code, 201, rw.text)
        self.assertEqual(adm.status_code, 201, adm.text)
        self.assertEqual(self._flag(rw.json()["id"]), 0)
        self.assertEqual(self._flag(adm.json()["id"]), 1)

    def test_a_name_that_moved_onto_the_lan_is_not_sent(self):
        from app import scheduler

        self._exec("INSERT INTO webhooks (id, name, url, enabled, local_target_allowed) "
                   "VALUES (9, 'w', 'ntfy://ntfy.example.com/t', 1, 0)")
        with patch.object(webhook_targets, "_resolve", return_value=_PRIVATE):
            r = self.client.post("/api/webhooks/9/test", headers=self._h("rw"))
            self.assertEqual(r.status_code, 403, r.text)
            conn = _sec.models.get_db()
            try:
                self.assertTrue(scheduler._local_send_refused("ntfy://ntfy.example.com/t", 9, conn, None))
            finally:
                conn.close()
        with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC):
            conn = _sec.models.get_db()
            try:
                self.assertFalse(scheduler._local_send_refused("ntfy://ntfy.example.com/t", 9, conn, None))
            finally:
                conn.close()

    def test_a_restore_keeps_a_webhook_unapproved(self):
        """M11: the restore dropped the flag, so the column default approved every webhook."""
        self._exec("INSERT INTO webhooks (id, name, url, enabled, local_target_allowed) "
                   "VALUES (9, 'w', 'ntfy://ntfy.example.com/t', 1, 0)")
        self._exec("INSERT INTO webhooks (id, name, url, enabled, local_target_allowed) "
                   "VALUES (10, 'a', 'ntfy://ntfy.example.com/a', 1, 1)")
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        r = self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._flag(9), 0)
        self.assertEqual(self._flag(10), 1)

    def test_an_unreadable_approval_refuses_a_lan_target_only(self):
        """M11: a failed read of the flag used to count as an approval."""
        from app import scheduler

        broken = MagicMock()
        broken.execute.side_effect = sqlite3.OperationalError("database is locked")
        with patch.object(scheduler, "add_log"):
            with patch.object(webhook_targets, "_resolve", return_value=_PRIVATE):
                self.assertTrue(scheduler._local_send_refused("ntfy://ntfy.example.com/t", 9, broken, None))
            with patch.object(webhook_targets, "_resolve", return_value=_PUBLIC):
                self.assertFalse(scheduler._local_send_refused("ntfy://ntfy.example.com/t", 9, broken, None))


# ------------------------------------------------------------------------------- B25


class B25GenericFailureLogsNoTextTests(unittest.TestCase):
    def test_the_exception_text_stays_out_of_the_log(self):
        from app.api import webhooks

        with self.assertLogs(webhooks.logger, "WARNING") as logs:
            webhooks._generic_failure("send", RuntimeError("https://hooks.example/SECRET-TOKEN"))
        self.assertNotIn("SECRET-TOKEN", "\n".join(logs.output))
        self.assertIn("RuntimeError", "\n".join(logs.output))


# ------------------------------------------------------------------------------ H2, B15


class H2B15DnsTargetTests(_ms._MultiSyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "Cloudflare", "cloudflare")
        self._add_provider(3, "AdGuard", "adguard")
        conn = _ms.models.get_db()
        conn.execute("UPDATE providers SET url='http://192.168.1.20:81' WHERE id=1")
        conn.commit()
        conn.close()

    def _resolve(self, dns_ids, wan="", priority=None):
        conn = _ms.models.get_db()
        try:
            if priority is not None:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('public_target_priority', ?)",
                             (priority,))
                conn.commit()
            with patch.object(public_target, "_detect_server_public_ip", return_value=wan) as detect:
                result = public_target.resolve_dns_target(
                    conn, "auto", "", proxy_provider_id=1, dns_provider_ids=dns_ids
                )
            return result, detect.call_count
        finally:
            conn.close()

    def test_a_public_provider_never_gets_the_lan_proxy_host(self):
        self.assertEqual(self._resolve([2])[0], ("", "auto_unavailable"))
        value, _ = self._resolve([3, 2], priority="proxy_provider_host,server_public_ip")[0]
        self.assertNotEqual(value, "192.168.1.20")

    def test_a_private_provider_still_gets_the_proxy_host(self):
        self.assertEqual(self._resolve([3])[0], ("192.168.1.20", "proxy_provider_host"))

    def test_the_wan_address_is_used_when_found(self):
        self.assertEqual(self._resolve([2], wan="93.184.216.34")[0], ("93.184.216.34", "server_public_ip"))

    def test_a_removed_wan_source_is_not_consulted(self):
        _, calls = self._resolve([3], wan="203.0.113.5", priority="proxy_provider_host,current")
        self.assertEqual(calls, 0)
        conn = _ms.models.get_db()
        try:
            with patch.object(public_target, "_detect_server_public_ip", return_value="203.0.113.5") as detect:
                public_target.suggest_public_targets(conn, proxy_provider_id=1)
            self.assertEqual(detect.call_count, 0)
        finally:
            conn.close()


# ------------------------------------------------------------------------------- H3


class H3RestoreSkipsCaseDuplicatesTests(_sec._WithKeys):
    def test_duplicates_are_skipped_and_reported(self):
        backup = self.client.get("/api/backup", headers=self._h("adm")).json()
        backup["tags"] = [{"id": 1, "name": "prod", "color": "#fff"}]
        backup["services"] = [
            {"id": 7, "subdomain": "app", "domain": "example.com", "target_ip": "10.0.0.1", "target_port": 80},
            {"id": 8, "subdomain": "App", "domain": "Example.com", "target_ip": "10.0.0.2", "target_port": 80},
        ]
        backup["service_tags"] = [{"service_id": 7, "tag_id": 1}]
        r = self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(any("same id or public hostname" in s for s in r.json()["skipped"]), r.json())
        conn = _sec.models.get_db()
        try:
            ids = [row[0] for row in conn.execute("SELECT id FROM services")]
            tags = [tuple(row) for row in conn.execute("SELECT service_id, tag_id FROM service_tags")]
        finally:
            conn.close()
        self.assertEqual(ids, [7])
        self.assertEqual(tags, [(7, 1)])


# ------------------------------------------------------------------------------- H4, M3


class H4AutoDnsReachesTheSecondaryServerTests(_ms._MultiSyncTestCase):
    def test_a_new_wan_address_reaches_every_dns_target(self):
        from app import scheduler

        self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium")
        status, body = self._create(dns_provider_id=2, extra_dns_provider_ids=[3], dns_ip="203.0.113.1")
        self.assertEqual(status, 201, body)
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto', auto_update_dns=1")
        conn.commit()
        try:
            with patch.object(scheduler, "create_provider", self._provider_for), \
                 patch.object(public_target, "_detect_server_public_ip", return_value="203.0.113.99"):
                scheduler._run_dns_auto_updates_once(conn)
            conn.commit()
        finally:
            conn.close()
        answers = [r["answer"] for r in second.rewrites if r["domain"] == "vault.example.com"]
        self.assertEqual(answers, ["203.0.113.99"])

    def _auto_cycle(self, wan="203.0.113.99"):
        from app import scheduler

        conn = _ms.models.get_db()
        try:
            with patch.object(scheduler, "create_provider", self._provider_for), \
                 patch.object(public_target, "_detect_server_public_ip", return_value=wan) as detect:
                scheduler._run_dns_auto_updates_once(conn)
            conn.commit()
        finally:
            conn.close()
        return detect

    def test_a_secondary_that_missed_the_move_is_retried(self):
        from app import scheduler

        self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium")
        status, body = self._create(dns_provider_id=2, extra_dns_provider_ids=[3], dns_ip="203.0.113.1")
        self.assertEqual(status, 201, body)
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto', auto_update_dns=1")
        conn.commit()
        conn.close()
        self.addCleanup(scheduler._secondary_dns_pending.clear)

        second.ok = False
        self._auto_cycle()
        self.assertIn(body["id"], scheduler._secondary_dns_pending)
        second.ok = True
        self._auto_cycle()  # the primary is up to date: only the secondary is left to fix
        answers = [r["answer"] for r in second.rewrites if r["domain"] == "vault.example.com"]
        self.assertEqual(answers, ["203.0.113.99"])
        self.assertNotIn(body["id"], scheduler._secondary_dns_pending)

    def test_a_public_secondary_decides_the_address_and_nothing_flaps(self):
        self._add_provider(1, "NPM", "npm")
        primary = self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Cloudflare", "cloudflare")
        status, body = self._create(proxy_provider_id=1, dns_provider_id=2,
                                    extra_dns_provider_ids=[3], dns_ip="203.0.113.1")
        self.assertEqual(status, 201, body)
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto', auto_update_dns=1")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES "
                     "('public_target_priority', 'proxy_provider_host,server_public_ip')")
        conn.commit()
        conn.close()
        with patch.object(public_target, "_provider_host_candidate", return_value="192.168.1.10"):
            self._auto_cycle(wan=_PUBLIC[0])
            self._auto_cycle(wan=_PUBLIC[0])
        answers = [r["answer"] for r in primary.rewrites if r["domain"] == "vault.example.com"]
        self.assertNotIn("192.168.1.10", answers)
        self.assertEqual(answers, [_PUBLIC[0]])
        self.assertEqual(self._row(body["id"])["dns_ip"], _PUBLIC[0])

    def test_resolvers_removed_from_the_priority_are_not_called(self):
        self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(dns_provider_id=2, dns_ip="203.0.113.1")
        self.assertEqual(status, 201, body)
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto', auto_update_dns=1")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES "
                     "('public_target_priority', 'proxy_provider_host,current')")
        conn.commit()
        conn.close()
        detect = self._auto_cycle()
        detect.assert_not_called()

    def _row(self, sid):
        conn = _ms.models.get_db()
        try:
            return conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        finally:
            conn.close()


class M3BulkEnableSkipsEnabledServicesTests(_ms._MultiSyncTestCase):
    def test_an_enabled_service_is_not_pushed_again(self):
        from app.api import services as services_api

        self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(dns_provider_id=2)
        self.assertEqual(status, 201, body)
        self.calls.clear()
        result = services_api.bulk_action(
            services_api._BulkActionBody(ids=[body["id"]], action="enable"), _ms._request()
        )
        self.assertEqual([c for c in self.calls if c[1] == "add_rewrite"], [])
        # Nothing changed, so nothing is reported as affected (the UI shows a no-op).
        self.assertEqual(result["affected"], 0)


# ------------------------------------------------------------- M1, M2, M4, M5, M6, M7, M8


class _ServiceRoutesBase(_ms._MultiSyncTestCase):
    def _row(self, sid: int):
        conn = _ms.models.get_db()
        try:
            return conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        finally:
            conn.close()

    def _update(self, sid: int, **overrides):
        return _ms.services_api.update_service(sid, _ms._request("PUT"), self._body(**overrides))


class M1QueuedRemovalIsCommittedTests(_ServiceRoutesBase):
    def test_the_writer_lock_is_released_once_the_removal_is_queued(self):
        self._add_provider(1, "NPM", "npm", ok=False)
        conn = _ms.models.get_db()
        try:
            message = _ms.sync_api.withdraw_former_proxy_host(conn, 1, 55, "vault.example.com")
            self.assertTrue(message)
            self.assertFalse(conn.in_transaction, "the INSERT still holds SQLite's writer lock")
        finally:
            conn.close()
        conn = _ms.models.get_db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pending_proxy_removals").fetchone()[0], 1)
        finally:
            conn.close()


class M2RenameRaceIsA409Tests(_ServiceRoutesBase):
    def test_a_hostname_taken_meanwhile_is_a_409(self):
        from fastapi import HTTPException

        self._add_provider(2, "AdGuard", "adguard")
        self.assertEqual(self._create(dns_provider_id=2)[0], 201)
        status, other = self._create(subdomain="other", dns_provider_id=2)
        self.assertEqual(status, 201)
        self.calls.clear()
        with patch.object(_ms.services_api, "_conflicting_service", return_value=None), \
             self.assertRaises(HTTPException) as caught:
            self._update(other["id"], subdomain="vault", dns_provider_id=2)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self._row(other["id"])["subdomain"], "other")
        # The name is refused before any provider is touched: nothing was published under
        # "vault" for this service, nor anything withdrawn under "other".
        self.assertEqual(self.calls, [])

    def test_a_proxy_rule_is_not_renamed_onto_a_taken_name(self):
        from fastapi import HTTPException

        self._add_provider(1, "NPM", "npm")
        self.assertEqual(self._create(proxy_provider_id=1)[0], 201)
        status, other = self._create(subdomain="other", proxy_provider_id=1)
        self.assertEqual(status, 201)
        self.calls.clear()
        with patch.object(_ms.services_api, "_conflicting_service", return_value=None), \
             self.assertRaises(HTTPException) as caught:
            self._update(other["id"], subdomain="vault", proxy_provider_id=1)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn("update_host", [c[1] for c in self.calls])
        self.assertEqual(self._row(other["id"])["subdomain"], "other")

    def test_a_foreign_key_failure_is_not_reported_as_a_taken_hostname(self):
        import sqlite3

        from fastapi import HTTPException

        self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(dns_provider_id=2)
        self.assertEqual(status, 201)
        conn_execute_error = sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        original_get_db = _ms.services_api.get_db

        class _Conn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *args):
                if sql.lstrip().startswith("UPDATE services SET\n"):
                    raise conn_execute_error
                return self._inner.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        with patch.object(_ms.services_api, "get_db", lambda: _Conn(original_get_db())), \
             self.assertRaises(HTTPException) as caught:
            self._update(body["id"], target_port=8081, dns_provider_id=2)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("provider", caught.exception.detail)
        self.assertNotIn("hostname was taken", caught.exception.detail)


class M4DisabledServiceGetsNoActiveHostTests(_ServiceRoutesBase):
    def test_moving_a_disabled_service_creates_nothing_on_the_new_proxy(self):
        old = self._add_provider(1, "NPM", "npm")
        new = self._add_provider(4, "NPM2", "npm")
        status, body = self._create(proxy_provider_id=1)
        self.assertEqual(status, 201)
        self._update(body["id"], proxy_provider_id=1, enabled=False)
        self.assertIsNotNone(self._row(body["id"])["npm_host_id"])
        self.calls.clear()

        self._update(body["id"], proxy_provider_id=4, enabled=False)

        self.assertNotIn(("NPM2", "create_host", "vault.example.com"), self.calls)
        self.assertFalse(new.holds("vault.example.com"))
        self.assertFalse(old.holds("vault.example.com"))
        self.assertIsNone(self._row(body["id"])["npm_host_id"])

    def test_it_is_deployed_on_the_next_enable(self):
        self._add_provider(1, "NPM", "npm")
        new = self._add_provider(4, "NPM2", "npm")
        status, body = self._create(proxy_provider_id=1)
        self._update(body["id"], proxy_provider_id=1, enabled=False)
        self._update(body["id"], proxy_provider_id=4, enabled=False)
        self._update(body["id"], proxy_provider_id=4, enabled=True)
        self.assertTrue(new.holds("vault.example.com"))


class M5WithdrawalRunsOutsideTheUpdateTests(_ServiceRoutesBase):
    def test_no_transaction_is_open_when_a_former_target_is_withdrawn(self):
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        status, body = self._create(dns_provider_id=2, extra_dns_provider_ids=[3])
        self.assertEqual(status, 201)
        seen = []
        real = _ms.services_api.withdraw_service_routes

        def spy(conn, svc, sid, **kw):
            seen.append(conn.in_transaction)
            return real(conn, svc, sid, **kw)

        with patch.object(_ms.services_api, "withdraw_service_routes", spy):
            self._update(body["id"], dns_provider_id=2, extra_dns_provider_ids=[])
        self.assertEqual(seen, [False])
        self.assertFalse(self.providers[3].holds("vault.example.com"))


class M6M7PushDnsTests(_ServiceRoutesBase):
    def setUp(self) -> None:
        super().setUp()
        self.dns = self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(dns_provider_id=2, dns_ip="198.51.100.7")
        self.assertEqual(status, 201)
        self.sid = body["id"]
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto' WHERE id=?", (self.sid,))
        conn.commit()
        conn.close()

    def _push(self):
        conn = _ms.models.get_db()
        try:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (self.sid,)).fetchone()
            with patch.object(public_target, "_detect_server_public_ip", return_value="93.184.216.34"):
                return _ms.sync_api._push_service_row(conn, svc, self.sid)
        finally:
            conn.close()

    def test_a_refused_write_leaves_the_stored_address_alone(self):
        self.dns.ok = False
        self.dns.update_rewrite = lambda *_a: False
        result = self._push()
        self.assertFalse(result["ok"])
        self.assertEqual(self._row(self.sid)["dns_ip"], "198.51.100.7")

    def test_the_record_is_never_deleted_before_the_new_one_exists(self):
        self.dns.ok = False
        self.dns.update_rewrite = lambda *_a: False
        self.calls.clear()
        self._push()
        self.assertNotIn("delete_rewrite", [c[1] for c in self.calls])
        self.assertTrue(self.dns.holds("vault.example.com"))

    def test_a_successful_move_adds_then_deletes_and_is_stored(self):
        # A provider's own `update_rewrite` is not used: Pi-hole's answered True with both
        # records live. Add first, then delete the old value.
        self.calls.clear()
        result = self._push()
        self.assertTrue(result["ok"], result)
        self.assertEqual([c[1] for c in self.calls if "rewrite" in c[1]], ["add_rewrite", "delete_rewrite"])
        self.assertEqual([r["answer"] for r in self.dns.rewrites], ["93.184.216.34"])
        self.assertEqual(self._row(self.sid)["dns_ip"], "93.184.216.34")

    def test_an_add_that_edits_in_place_does_not_lose_the_record(self):
        # Cloudflare's `add_rewrite` edits its single record: the old value is gone, the
        # delete finds nothing and answers False. That is not a reason to undo the add.
        dns = self.dns

        def edit_in_place(domain, ip):
            dns.rewrites = [r for r in dns.rewrites if r["domain"] != domain]
            dns.rewrites.append({"domain": domain, "answer": ip})
            return True

        dns.add_rewrite = edit_in_place
        dns.delete_rewrite = lambda *_a: False
        result = self._push()
        self.assertTrue(result["ok"], result)
        self.assertEqual([r["answer"] for r in dns.rewrites], ["93.184.216.34"])

    def test_a_refused_delete_withdraws_only_the_record_this_push_added(self):
        real_delete = self.dns.delete_rewrite
        self.dns.delete_rewrite = lambda d, ip: False if ip == "198.51.100.7" else real_delete(d, ip)
        result = self._push()
        self.assertFalse(result["ok"])
        self.assertEqual([r["answer"] for r in self.dns.rewrites], ["198.51.100.7"])
        self.assertEqual(self._row(self.sid)["dns_ip"], "198.51.100.7")

    def test_a_failed_rollback_is_reported(self):
        self.dns.delete_rewrite = lambda *_a: False
        result = self._push()
        self.assertFalse(result["ok"])
        self.assertTrue(any("both are live" in e for e in result["errors"]), result)


class M14PushTreatsTwoSpellingsAsOneValueTests(_ServiceRoutesBase):
    def test_an_ipv6_written_differently_is_not_moved_onto_itself(self):
        dns = self._add_provider(2, "AdGuard", "adguard")
        status, body = self._create(dns_provider_id=2, dns_ip="2001:db8::1")
        self.assertEqual(status, 201)
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET dns_ip='2001:0db8:0::1' WHERE id=?", (body["id"],))
        conn.commit()
        try:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (body["id"],)).fetchone()
            self.calls.clear()
            result = _ms.sync_api._push_service_row(conn, svc, body["id"])
        finally:
            conn.close()
        self.assertTrue(result["ok"], result)
        self.assertNotIn("delete_rewrite", [c[1] for c in self.calls])
        self.assertTrue(dns.holds("vault.example.com"))


class M6ExtraOnlyPushDoesNotMoveTheStoredAddressTests(_ServiceRoutesBase):
    def test_a_push_to_the_secondary_alone_leaves_dns_ip(self):
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        status, body = self._create(dns_provider_id=2, extra_dns_provider_ids=[3], dns_ip="198.51.100.7")
        self.assertEqual(status, 201)
        conn = _ms.models.get_db()
        conn.execute("UPDATE services SET public_target_mode='auto' WHERE id=?", (body["id"],))
        conn.commit()
        try:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (body["id"],)).fetchone()
            with patch.object(public_target, "_detect_server_public_ip", return_value="93.184.216.34"):
                _ms.sync_api._push_service_row(conn, svc, body["id"], only_provider_ids={3})
        finally:
            conn.close()
        # The primary still serves the old address: the row must keep naming it, or
        # auto-DNS would see nothing left to fix there.
        self.assertEqual(self._row(body["id"])["dns_ip"], "198.51.100.7")


class M8PendingRemovalOwnerTests(_ServiceRoutesBase):
    def test_a_route_now_served_as_an_extra_target_is_not_removed(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(4, "NPM2", "npm")
        status, _ = self._create(proxy_provider_id=4, extra_proxy_provider_ids=[1])
        self.assertEqual(status, 201)
        conn = _ms.models.get_db()
        try:
            conn.execute("INSERT INTO pending_proxy_removals (provider_id, host_id, public_host) "
                         "VALUES (1, '55', 'VAULT.example.com')")
            conn.commit()
            self.calls.clear()
            _ms.sync_api.retry_pending_proxy_removals(conn)
            left = conn.execute("SELECT COUNT(*) FROM pending_proxy_removals").fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn(("NPM", "delete_host", 55), self.calls)
        self.assertEqual(left, 0)


class B9PendingRemovalsEndTests(_ServiceRoutesBase):
    def _pending(self, conn) -> int:
        return conn.execute("SELECT COUNT(*) FROM pending_proxy_removals").fetchone()[0]

    def test_a_read_only_proxy_gets_no_queued_removal(self):
        self._add_provider(1, "Traefik", "traefik", ok=False)
        conn = _ms.models.get_db()
        try:
            self.assertEqual(_ms.sync_api.withdraw_former_proxy_host(conn, 1, "vault.example.com",
                                                                     "vault.example.com"), "")
            self.assertEqual(self._pending(conn), 0)
            # An entry queued by an older version is dropped, not retried forever.
            conn.execute("INSERT INTO pending_proxy_removals (provider_id, host_id, public_host) "
                         "VALUES (1, 'vault.example.com', 'vault.example.com')")
            conn.commit()
            _ms.sync_api.retry_pending_proxy_removals(conn)
            self.assertEqual(self._pending(conn), 0)
        finally:
            conn.close()
        self.assertNotIn("delete_host", [c[1] for c in self.calls])

    def test_an_old_refused_removal_is_given_up(self):
        self._add_provider(1, "NPM", "npm", ok=False)
        conn = _ms.models.get_db()
        try:
            conn.execute("INSERT INTO pending_proxy_removals (provider_id, host_id, public_host, created_at) "
                         "VALUES (1, '55', 'old.example.com', datetime('now', '-30 days')), "
                         "(1, '56', 'new.example.com', datetime('now'))")
            conn.commit()
            _ms.sync_api.retry_pending_proxy_removals(conn)
            left = [r[0] for r in conn.execute("SELECT public_host FROM pending_proxy_removals")]
        finally:
            conn.close()
        self.assertEqual(left, ["new.example.com"])
        self.assertNotIn(("NPM", "delete_host", 55), self.calls)


class H2PushReadsTheColumnsOfADatabaseRowTests(unittest.TestCase):
    def test_a_tunnel_row_publishes_its_tunnel_hostname(self):
        import sqlite3

        from app.api import sync as sync_api

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT 'tunnel' AS expose_mode, 'Edge.Example.org' AS tunnel_hostname, "
            "'app' AS subdomain, 'example.com' AS domain, 'auto' AS public_target_mode"
        ).fetchone()
        conn.close()
        self.assertEqual(sync_api._service_public_host(row), "edge.example.org")
        self.assertEqual(sync_api._col(row, "public_target_mode"), "auto")
        self.assertIsNone(sync_api._col(row, "missing"))


# ------------------------------------------------------------------------------- M9


class M9ReconcileLocksPerServiceTests(_ServiceRoutesBase):
    def test_the_lock_is_released_between_services(self):
        from app import scheduler

        self._add_provider(2, "AdGuard", "adguard")
        self._create(dns_provider_id=2)
        self._create(subdomain="other", dns_provider_id=2)
        conn = _ms.models.get_db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')")
        conn.commit()
        conn.close()

        entries = []

        class _CountingLock:
            def __enter__(self):
                entries.append(1)

            def __exit__(self, *_exc):
                return False

        with patch.object(scheduler, "_lock", _CountingLock()), \
             patch.object(_ms.sync_api, "_compute_service_drift", return_value={"ok": True}):
            scheduler.run_auto_reconcile()
        self.assertEqual(len(entries), 2)


# --------------------------------------------------------------------------- M12, M16


class _CfRecord:
    def __init__(self, rid, name, content):
        self.id, self.name, self.content, self.proxied, self.ttl = rid, name, content, False, 300


class M12M16CloudflareTests(unittest.TestCase):
    def _provider(self, records, *, fail=False):
        from app.providers.cloudflare import CloudflareProvider

        provider = CloudflareProvider("", "", "token")
        self.edited, self.created, self.listed = [], [], []
        outer = self

        class _Records:
            def list(self, **kwargs):
                if fail:
                    raise RuntimeError("forbidden")
                outer.listed.append(kwargs)
                return [r for r in records if kwargs.get("type") in (None, "A")]

            def edit(self, **kwargs):
                outer.edited.append(kwargs)

            def create(self, **kwargs):
                outer.created.append(kwargs)

        class _Zones:
            def list(self, **kwargs):
                return [type("Z", (), {"id": "zone-1", "name": kwargs.get("name", "")})()]

        class _Client:
            dns = type("_DNS", (), {"records": _Records()})()
            zones = _Zones()

        provider._client = _Client()  # noqa: SLF001
        return provider

    def test_a_round_robin_member_already_present_is_left_alone(self):
        p = self._provider([_CfRecord("a", "app.example.com", "198.51.100.1"),
                            _CfRecord("b", "app.example.com", "198.51.100.2")])
        self.assertTrue(p.add_rewrite("app.example.com", "198.51.100.2"))
        self.assertEqual((self.edited, self.created), ([], []))

    def test_a_round_robin_set_is_not_overwritten(self):
        p = self._provider([_CfRecord("a", "app.example.com", "198.51.100.1"),
                            _CfRecord("b", "app.example.com", "198.51.100.2")])
        self.assertTrue(p.add_rewrite("app.example.com", "198.51.100.3"))
        self.assertEqual(self.edited, [])
        self.assertEqual(len(self.created), 1)

    def test_a_single_record_is_edited(self):
        p = self._provider([_CfRecord("a", "app.example.com", "198.51.100.1")])
        self.assertTrue(p.add_rewrite("app.example.com", "198.51.100.3"))
        self.assertEqual(len(self.edited), 1)
        self.assertEqual(self.created, [])

    def test_lookup_reads_one_name_exactly(self):
        p = self._provider([_CfRecord("a", "app.example.com", "198.51.100.1"),
                            _CfRecord("b", "app.example.com.evil.net", "203.0.113.9")])
        self.assertEqual(
            [r["answer"] for r in p.lookup_rewrites("app.example.com")], ["198.51.100.1"]
        )
        self.assertTrue(all(k.get("name") == {"exact": "app.example.com"} for k in self.listed))

    def test_an_unreadable_zone_is_unknown(self):
        self.assertIsNone(self._provider([], fail=True).lookup_rewrites("app.example.com"))


# ------------------------------------------------------------------------------- M13


class M13DeleteDnsRecordTests(_sec._WithKeys):
    def setUp(self) -> None:
        super().setUp()
        self._exec("INSERT INTO providers (id, name, type, url, username, password, extra, enabled) "
                   "VALUES (5, 'AG', 'adguard', 'http://x', 'u', 'p', '{}', 1)")
        self.fake = MagicMock()
        self.fake.delete_rewrite.return_value = True

    def _delete(self):
        from app.api import providers as providers_api

        with patch.object(providers_api, "create_provider", return_value=self.fake):
            return self.client.delete("/api/providers/5/dns-records/app.example.com", headers=self._h("rw"))

    def test_an_unreadable_list_is_a_502(self):
        self.fake.lookup_rewrites.return_value = None
        self.assertEqual(self._delete().status_code, 502)
        self.fake.delete_rewrite.assert_not_called()

    def test_several_answers_are_a_409(self):
        self.fake.lookup_rewrites.return_value = [
            {"domain": "app.example.com", "answer": "10.0.0.1"},
            {"domain": "app.example.com", "answer": "10.0.0.2"},
        ]
        self.assertEqual(self._delete().status_code, 409)
        self.fake.delete_rewrite.assert_not_called()

    def test_a_single_answer_is_deleted(self):
        self.fake.lookup_rewrites.return_value = [{"domain": "app.example.com", "answer": "10.0.0.1"}]
        r = self._delete()
        self.assertEqual(r.status_code, 200, r.text)
        self.fake.delete_rewrite.assert_called_once_with("app.example.com", "10.0.0.1")


# ------------------------------------------------------------------------------- M14


class M14RecordValuesAreNormalizedTests(unittest.TestCase):
    def test_powerdns_deletes_another_spelling_of_the_address(self):
        from app.providers.powerdns import PowerDNSProvider

        p = PowerDNSProvider("http://pdns:8081", "", "key")
        p._find_zone = lambda _d: "example.com."
        p._find_rrset = lambda *_a: {"ttl": 60, "records": [{"content": "2001:db8:0:0::1"}]}
        written = []
        p._write_rrset = lambda *a, **_k: written.append(a) or True
        self.assertTrue(p.delete_rewrite("app.example.com", "2001:DB8::1"))
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][3], [])

    def test_desec_deletes_a_cname_written_in_capitals(self):
        from app.providers.desec import DesecProvider

        p = DesecProvider("", "", "tok")
        p._find_domain = lambda _d: {"name": "example.com"}
        p._get_rrset = lambda *_a: {"ttl": 3600, "records": ["Target.Example.NET."]}
        written = []
        p._write_records = lambda *a: written.append(a) or True
        self.assertTrue(p.delete_rewrite("app.example.com", "target.example.net"))
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][3], [])

    def test_powerdns_add_reuses_the_stored_spelling(self):
        from app.providers.powerdns import PowerDNSProvider

        p = PowerDNSProvider("http://pdns:8081", "", "key")
        p._find_zone = lambda _d: "example.com."
        p._find_rrset = lambda *_a: {"ttl": 60, "records": [{"content": "2001:db8::1"}]}
        written = []
        p._write_rrset = lambda *a, **_k: written.append(a) or True
        self.assertTrue(p.add_rewrite("app.example.com", "2001:0db8:0::1"))
        self.assertEqual(written, [], "an existing value in another spelling was appended again")

    def test_adguard_deletes_the_stored_spelling(self):
        from app.providers.adguard import AdGuardProvider

        ag = AdGuardProvider("http://adguard:3000", "admin", "secret")
        listing = MagicMock(status_code=200)
        listing.raise_for_status.return_value = None
        listing.json.return_value = [{"domain": "app.example.com", "answer": "2001:db8::1"}]
        ag.session.get = MagicMock(return_value=listing)
        ag.session.post = MagicMock(return_value=MagicMock(status_code=200))
        self.assertTrue(ag.delete_rewrite("App.Example.com", "2001:DB8:0::1"))
        self.assertEqual(ag.session.post.call_args[1]["json"],
                         {"domain": "app.example.com", "answer": "2001:db8::1"})

    def test_pihole_update_does_not_report_two_live_records_as_a_move(self):
        from app.providers.pihole import PiholeProvider

        p = PiholeProvider.__new__(PiholeProvider)
        from contextlib import nullcontext

        p._api_session = lambda: nullcontext(True)
        p.add_rewrite = MagicMock(return_value=True)
        p.delete_rewrite = MagicMock(side_effect=lambda d, ip: ip != "10.0.0.1")
        self.assertFalse(p.update_rewrite("a.example.com", "10.0.0.1", "a.example.com", "10.0.0.2"))
        # The new record was withdrawn so the name keeps exactly the record it had.
        p.delete_rewrite.assert_any_call("a.example.com", "10.0.0.2")

    def test_two_spellings_of_one_address_are_no_update(self):
        from app.providers.pihole import PiholeProvider

        p = PiholeProvider.__new__(PiholeProvider)
        p.add_rewrite = MagicMock()
        p.delete_rewrite = MagicMock()
        self.assertTrue(p.update_rewrite("a.example.com", "2001:db8::1", "A.example.com.", "2001:0db8::1"))
        p.add_rewrite.assert_not_called()
        p.delete_rewrite.assert_not_called()


# ------------------------------------------------------------------------------- B16


class B16ImportRoleFollowsTheStoredTypeTests(_ServiceRoutesBase):
    def _import(self, host: dict):
        body = _ms.sync_api.ServicesImportIn(proxy_hosts=[host], dns_rewrites=[])
        with patch.object(_ms.sync_api, "require_auth_or_setup", lambda _req, scope=None: None):
            return _ms.sync_api.import_services(_ms._request(), body)

    def _modes(self):
        conn = _ms.models.get_db()
        try:
            return [tuple(r) for r in conn.execute(
                "SELECT expose_mode, proxy_provider_id, tunnel_provider_id FROM services")]
        finally:
            conn.close()

    def test_a_tunnel_declared_as_npm_is_imported_as_a_tunnel(self):
        self._add_provider(5, "Tunnel", "cloudflare_tunnel")
        self._import({"id": "app.example.com", "domains": ["app.example.com"], "host": "10.0.0.2",
                      "port": 80, "_provider_id": 5, "_provider_type": "npm"})
        self.assertEqual(self._modes(), [("tunnel", None, 5)])

    def test_an_npm_declared_as_a_tunnel_still_has_its_route_checked(self):
        self._add_provider(1, "NPM", "npm")
        result = self._import({"id": 999, "domains": ["app.example.com"], "host": "10.0.0.2",
                               "port": 80, "_provider_id": 1, "_provider_type": "cloudflare_tunnel"})
        self.assertEqual(self._modes(), [])
        self.assertTrue(any("does not exist" in e for e in result["errors"]), result)


# ------------------------------------------------------------------------------- B10


class B10EachLoginHasItsOwnStreamBudgetTests(unittest.TestCase):
    def test_two_browsers_do_not_share_one_identity(self):
        from app.api import settings as settings_api

        class _Req:
            def __init__(self):
                self.session = {}

        a, b = _Req(), _Req()
        with patch.object(settings_api, "_get_auth_context",
                          lambda _r: {"kind": "session", "scopes": ["admin"]}):
            first_a = settings_api._stream_identity(a)
            again_a = settings_api._stream_identity(a)
            first_b = settings_api._stream_identity(b)
        self.assertEqual(first_a, again_a)
        self.assertNotEqual(first_a[0], first_b[0])
        self.assertTrue(first_a[0].startswith("session:"))


# ------------------------------------------------------------------------------- B14


class B14WanDetectionTests(unittest.TestCase):
    class _Resp:
        def __init__(self, body: str):
            self.body = body.encode()

        def read(self, _n):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def test_a_plain_http_source_is_never_asked(self):
        asked = []

        def fake(req, timeout):
            asked.append(req.full_url)
            return self._Resp("93.184.216.34")

        with patch.object(public_target, "urlopen", fake):
            ip = public_target._detect_server_public_ip(["http://ip.example", "https://ip.example"], 2.0)
        self.assertEqual(ip, "93.184.216.34")
        self.assertEqual(asked, ["https://ip.example"])
        self.assertEqual(public_target._parse_sources("http://a.example\nhttps://b.example"),
                         ["https://b.example"])

    def test_a_carrier_grade_nat_answer_is_not_the_wan(self):
        answers = iter(["100.64.12.7", "93.184.216.34"])

        def fake(req, timeout):
            return self._Resp(next(answers))

        with patch.object(public_target, "urlopen", fake):
            ip = public_target._detect_server_public_ip(["https://a.example", "https://b.example"], 2.0)
        self.assertEqual(ip, "93.184.216.34")
        self.assertTrue(public_target.is_unroutable_literal("100.127.255.254"))
        self.assertFalse(public_target.is_unroutable_literal("100.128.0.1"))

    def test_the_stale_http_hint_is_gone(self):
        from app.api.settings import _validate_setting

        value, reason = _validate_setting("public_target_sources", "http://ip.example")
        self.assertIsNone(value)
        self.assertNotIn("http://", reason)

    def test_the_detection_has_an_overall_deadline(self):
        clock = [0.0]
        asked = []

        def slow(req, timeout):
            asked.append(timeout)
            clock[0] += timeout
            raise OSError("timed out")

        with patch.object(public_target, "urlopen", slow), \
                patch.object(public_target.time, "monotonic", lambda: clock[0]):
            ip = public_target._detect_server_public_ip([f"https://r{i}.example" for i in range(5)], 2.0)
        self.assertEqual(ip, "")
        self.assertEqual(asked, [2.0, 2.0])

    def test_a_redirect_off_https_is_refused(self):
        handler = public_target._HttpsOnlyRedirects()
        req = public_target.Request("https://ip.example")
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "http://evil.example"))

    def test_the_setting_refuses_plain_http(self):
        from app.api.settings import _is_valid_public_target_sources

        self.assertFalse(_is_valid_public_target_sources("http://ip.example"))
        self.assertTrue(_is_valid_public_target_sources("https://ip.example"))


# ------------------------------------------------------------------------------- B24


class B24HealthTellsAnonymousCallersOnlyTheVerdictTests(_sec._WithKeys):
    def test_anonymous_gets_ok_and_db_only(self):
        body = self.client.get("/api/health").json()
        self.assertEqual(set(body), {"ok", "db"})

    def test_a_key_gets_the_details(self):
        body = self.client.get("/api/health", headers=self._h("ro")).json()
        self.assertIn("version", body)
        self.assertIn("disk_usage", body)

    def test_a_down_database_answers_503_even_with_a_key(self):
        """The auth check reads the database: it used to raise and turn the 503 into a 500."""
        from app.api import health as health_api

        def down():
            raise sqlite3.OperationalError("unable to open database file")

        with patch.object(health_api, "get_db_ctx", side_effect=down), \
             patch.object(health_api, "is_authenticated", side_effect=sqlite3.OperationalError("down")), \
             patch.object(health_api, "is_setup_incomplete", side_effect=sqlite3.OperationalError("down")):
            r = self.client.get("/api/health", headers=self._h("ro"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"ok": False, "db": False})


# ------------------------------------------------------------------------------- B19


class B19DockerIgnoreTests(unittest.TestCase):
    def test_nested_env_files_and_local_notes_stay_out_of_the_build_context(self):
        path = os.path.join(_here, "..", ".dockerignore")
        with open(path, encoding="utf-8") as fh:
            lines = {line.strip() for line in fh}
        for pattern in ("**/.env", "**/.env.*", ".local/"):
            self.assertIn(pattern, lines)


# ------------------------------------------------------------------------------- B17


class B17TemplateNamesAreUniqueTests(_sec._IsolatedDB):
    def test_old_duplicates_are_renamed_and_new_ones_refused(self):
        import sqlite3

        conn = _sec.models.get_db()
        try:
            conn.execute("DROP INDEX idx_service_templates_name")
            for name in ("Web", "Web", "Web (2)"):
                conn.execute("INSERT INTO service_templates (name) VALUES (?)", (name,))
            conn.commit()
            _sec.models._ensure_unique_template_names(conn)
            conn.commit()
            names = [r[0] for r in conn.execute("SELECT name FROM service_templates ORDER BY id")]
            self.assertEqual(names, ["Web", "Web (2-1)", "Web (2)"])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO service_templates (name) VALUES ('Web')")
        finally:
            conn.close()

    def test_a_restore_does_not_replace_a_template_with_its_namesake(self):
        from app.api.backup import _restorable_templates

        report = {"skipped": []}
        kept = _restorable_templates([{"id": 1, "name": "Web"}, {"id": 2, "name": "Web"}], report)
        self.assertEqual([t["id"] for t in kept], [1])
        self.assertTrue(any("duplicate name" in s for s in report["skipped"]))

    def test_a_restore_compares_and_stores_the_stripped_name(self):
        from app.api.backup import _restorable_templates

        report = {"skipped": []}
        kept = _restorable_templates([{"id": 1, "name": "Web "}, {"id": 2, "name": " Web"}], report)
        self.assertEqual([(t["id"], t["name"]) for t in kept], [(1, "Web")])

    def test_a_renamed_long_duplicate_stays_a_valid_template_name(self):
        from app.api.templates import TemplateIn

        long_name = "x" * 62
        conn = _sec.models.get_db()
        try:
            conn.execute("DROP INDEX idx_service_templates_name")
            for _ in range(2):
                conn.execute("INSERT INTO service_templates (name) VALUES (?)", (long_name,))
            conn.commit()
            _sec.models._ensure_unique_template_names(conn)
            conn.commit()
            names = [r[0] for r in conn.execute("SELECT name FROM service_templates ORDER BY id")]
        finally:
            conn.close()
        self.assertEqual(names[0], long_name)
        self.assertNotEqual(names[1], long_name)
        self.assertLessEqual(len(names[1]), 64)
        TemplateIn(name=names[1])  # still accepted by the API, so it survives a restore


# -------------------------------------------------------------------------------- B8


class B8LegacyLabelIsUniqueTests(unittest.TestCase):
    def test_a_taken_suffixed_name_gets_another_one(self):
        from app.models import legacy_label

        taken = {"a-b", "a-b-7", "a-b-7-1"}
        name = legacy_label("a:b", taken, 7)
        self.assertNotIn(name, taken)
        self.assertEqual(name, "a-b-7-2")

    def test_a_long_name_stays_within_the_limit(self):
        from app.models import LABEL_MAX_LENGTH, legacy_label

        base = "x" * 40
        taken = {"x" * LABEL_MAX_LENGTH, "x" * (LABEL_MAX_LENGTH - 2) + "-3"}
        name = legacy_label(base, taken, 3)
        self.assertNotIn(name, taken)
        self.assertLessEqual(len(name), LABEL_MAX_LENGTH)


# -------------------------------------------------------------------------------- B3


class B3GuardHoldsBehindAnHttpProxyTests(unittest.TestCase):
    def test_a_name_resolving_to_metadata_is_refused_through_a_proxy(self):
        from app.providers import base

        session = base.TimeoutSession()
        with patch.object(base, "_resolve", return_value=["169.254.169.254"]), \
                patch.object(base.HTTPAdapter, "send", side_effect=AssertionError("sent")):
            with self.assertRaises(base.BlockedDestinationError):
                session.get("http://rebind.example/", proxies={"http": "http://proxy.local:3128"})

    def test_without_a_proxy_the_socket_check_is_left_to_do_it(self):
        from app.providers import base

        session = base.TimeoutSession()
        resolve = MagicMock(return_value=["169.254.169.254"])
        with patch.object(base, "_resolve", resolve), \
                patch.object(base.HTTPAdapter, "send", return_value=MagicMock(is_redirect=False)), \
                patch.dict(os.environ, {}, clear=False):
            for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
                os.environ.pop(key, None)
            session.trust_env = False
            session.get("http://lan.example/")
        resolve.assert_not_called()


# -------------------------------------------------------------------------------- B2


class B2DraftValidationChecksTheCaBundleTests(_sec._WithKeys):
    def _draft(self, key: str, ca_bundle: str):
        return self.client.post(
            "/api/providers/validate-draft", headers=self._h(key),
            json={"type": "npm", "url": "http://npm.local", "username": "u", "password": "p",
                  "extra": {"ca_bundle": ca_bundle}},
        )

    def test_a_write_key_cannot_probe_server_paths(self):
        self.assertEqual(self._draft("rw", __file__).status_code, 403)
        self.assertEqual(self._draft("rw", "/no/such/file.pem").status_code, 403)

    def test_an_admin_gets_the_same_check_as_a_save(self):
        self.assertEqual(self._draft("adm", "/no/such/file.pem").status_code, 400)


# -------------------------------------------------------------------------------- B4


class B4NpmListHostsReadsOddAnswersAsUnreadableTests(unittest.TestCase):
    def _provider(self, payload):
        from app.providers.npm import NPMProvider

        p = NPMProvider("http://npm.local", "u", "p")
        p._ensure_auth = lambda: True
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        p.session = MagicMock()
        p.session.get.return_value = resp
        return p

    def test_unexpected_shapes_give_none(self):
        for payload in ({"data": []}, {}, "", [{"id": 1}], [None], 5):
            with self.subTest(payload=payload):
                self.assertIsNone(self._provider(payload).list_hosts())

    def test_a_well_formed_list_is_still_read(self):
        hosts = self._provider([{"id": 1, "domain_names": ["a.example.com"], "forward_scheme": "http",
                                 "forward_host": "10.0.0.2", "forward_port": 80}]).list_hosts()
        self.assertEqual(hosts[0]["target"], "http://10.0.0.2:80")

    def test_a_login_answer_that_is_not_an_object_is_a_failed_login(self):
        from app.providers.npm import NPMProvider

        p = NPMProvider("http://npm.local", "u", "p")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        p.session = MagicMock()
        p.session.headers = {}
        p.session.post.return_value = resp
        for payload in (["token"], "token", None):
            with self.subTest(payload=payload):
                resp.json.return_value = payload
                self.assertFalse(p._login())
        resp.json.side_effect = ValueError("not json")
        self.assertFalse(p._login())


# ------------------------------------------------------------------------ B5, B6, B7


class B5B6B7CloudflareTunnelTests(unittest.TestCase):
    def _tunnel(self, account="acc123", extra=None):
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        return CloudflareTunnelProvider("", account, "tok", {"tunnel_id": "tid"} if extra is None else extra)

    def test_identifiers_that_would_compose_another_path_are_refused(self):
        p = self._tunnel(extra={"tunnel_id": "tid/../../zones"})
        p._request = MagicMock(return_value=[])
        self.assertEqual(p._resolve_tunnel_id(), "")
        self.assertIsNone(p._get_configuration())
        self.assertEqual(self._tunnel(account="acc?x=1").account_id, "")
        self.assertEqual(self._tunnel(extra={"tunnel_id": "  6f2c-ab  "}).tunnel_id, "6f2c-ab")

    def test_an_answer_without_config_is_unreadable(self):
        p = self._tunnel()
        p._request = MagicMock(return_value={"tunnel_id": "tid", "source": "cloudflare"})
        self.assertIsNone(p._get_configuration())
        self.assertFalse(p._upsert_ingress_rule("app.example.com", "http://10.0.0.2:80"))
        self.assertNotIn("PUT", [c.args[0] for c in p._request.call_args_list])

    def test_config_null_is_still_an_empty_tunnel(self):
        p = self._tunnel()
        p._request = MagicMock(return_value={"tunnel_id": "tid", "source": "cloudflare", "config": None})
        self.assertEqual(p._get_configuration(), {})

    def _dns(self, listing):
        p = self._tunnel()
        p._find_zone = lambda _h: "z1"
        calls = []

        def request(method, path, **kw):
            calls.append((method, path, kw.get("json")))
            if method == "GET":
                return listing
            return {"id": "new"}

        p._request = request
        return p, calls

    def test_an_existing_cname_is_patched_not_replaced(self):
        p, calls = self._dns([{"id": "r1", "name": "app.example.com", "content": "old.example.net",
                               "proxied": False}])
        self.assertTrue(p._ensure_dns_record("app.example.com"))
        self.assertEqual(calls[-1][0], "PATCH")
        self.assertEqual(calls[-1][2], {"content": "tid.cfargotunnel.com", "proxied": True})

    def test_a_record_of_another_name_is_not_rewritten(self):
        p, calls = self._dns([{"id": "r1", "name": "other.example.com", "content": "x", "proxied": False}])
        self.assertTrue(p._ensure_dns_record("app.example.com"))
        self.assertEqual([c[0] for c in calls], ["GET", "POST"])

    def test_a_failed_listing_creates_nothing(self):
        p, calls = self._dns(None)
        self.assertFalse(p._ensure_dns_record("app.example.com"))
        self.assertEqual([c[0] for c in calls], ["GET"])

    def test_deleting_one_host_leaves_the_cnames_of_the_others(self):
        p, calls = self._dns([
            {"id": "r1", "name": "app.example.com", "content": "tid.cfargotunnel.com"},
            {"id": "r2", "name": "other.example.com", "content": "tid.cfargotunnel.com"},
        ])
        self.assertTrue(p._delete_dns_record("app.example.com"))
        deleted = [c[1] for c in calls if c[0] == "DELETE"]
        self.assertEqual(deleted, ["/zones/z1/dns_records/r1"])


# ------------------------------------------------------------------- B20 to B26 (files)


def _repo_text(*parts):
    with open(os.path.join(_here, "..", *parts), encoding="utf-8") as fh:
        return fh.read()


class B20ToB26RepositoryFilesTests(unittest.TestCase):
    def test_b26_entrypoint_chowns_only_directories_and_single_link_files(self):
        text = _repo_text("docker-entrypoint.sh")
        self.assertIn(r"\( -type d -o \( -type f -links 1 \) \)", text)
        self.assertIn("chown -h appuser:appuser", text)

    def test_b21_lab_never_puts_a_technitium_secret_in_a_url(self):
        text = _repo_text("lab", "bootstrap.sh")
        self.assertNotIn("pass=$PW&", text)
        self.assertNotIn("?user=admin", text)
        self.assertNotIn("?token=", text)
        self.assertIn('--data-urlencode "pass=$PW"', text)
        self.assertIn('--data-urlencode "token=$token"', text)

    def test_b20_compose_blanks_the_bridge_credentials_in_the_panel(self):
        text = _repo_text("docker-compose.yml")
        self.assertIn("- VAUXTRA_API_KEY=\n", text)
        self.assertIn("- VAUXTRA_MCP_TOKEN=\n", text)

    def test_b22_docs_point_at_real_anchors_and_describe_cors_as_it_is(self):
        readme = _repo_text("README.md")
        howto = _repo_text("docs", "HOWTO.md")
        self.assertIn("HOWTO.md#11-mcp-integration", readme)
        self.assertIn("## 11) MCP Integration", howto)
        self.assertIn("full origin in `CORS_ORIGINS`", howto)
        security = _repo_text("SECURITY.md")
        self.assertIn("same-origin only", security)
        self.assertNotIn("- Default: `http://localhost:5173", security)

    def test_b23_ci_audits_and_inventories_the_mcp_bridge(self):
        text = _repo_text(".github", "workflows", "security.yml")
        self.assertIn("pip_audit --requirement vauxtra_mcp/requirements.txt", text)
        self.assertIn("-r requirements.txt -r vauxtra_mcp/requirements.txt", text)


# ------------------------------------------------------------------------------ Info


class InfoBlockListCoversAzureAndTranslatedFormsTests(unittest.TestCase):
    def test_azure_wireserver_and_nat64_or_6to4_forms_of_metadata_are_blocked(self):
        from app.providers.base import is_blocked_address

        for addr in ("168.63.129.16", "64:ff9b::a9fe:a9fe", "64:ff9b::a83f:8110", "2002:a9fe:a9fe::1"):
            with self.subTest(addr=addr):
                self.assertTrue(is_blocked_address(addr))
        for addr in ("192.168.1.10", "64:ff9b::5db8:d822", "2002:5db8:d822::1"):
            with self.subTest(addr=addr):
                self.assertFalse(is_blocked_address(addr))


class InfoMetricsEnabledFamilyHasAHeaderTests(_sec._WithKeys):
    def test_providers_enabled_is_typed_and_grouped(self):
        conn = _sec.models.get_db()
        try:
            conn.execute(
                "INSERT INTO providers (name, type, url, enabled) VALUES ('p', 'npm', 'http://10.0.0.2', 1)"
            )
            conn.commit()
        finally:
            conn.close()
        r = self.client.get("/metrics", headers=self._h("ro"))
        self.assertEqual(r.status_code, 200, r.text)
        lines = r.text.splitlines()
        self.assertIn("# TYPE vauxtra_providers_enabled gauge", lines)
        header = lines.index("# TYPE vauxtra_providers_enabled gauge")
        samples = [i for i, ln in enumerate(lines) if ln.startswith("vauxtra_providers_enabled{")]
        self.assertTrue(samples)
        self.assertTrue(all(i > header for i in samples))


if __name__ == "__main__":
    unittest.main()
