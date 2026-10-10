"""Regression tests for the scheduler findings of the second review pass.

Each class names the finding it covers (M2, M6, M7, M8, M22, B4, B6, B8, B9). They run
against an isolated SQLite database, the same way `test_scheduler_write_lock.py` does.
"""

import datetime
import inspect
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app import models
from app import scheduler as sched
from app.validators import is_valid_hostname


class _IsolatedDB(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "second_pass.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()
        self._reset_state()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._reset_state()
        self._tmpdir.cleanup()

    @staticmethod
    def _reset_state() -> None:
        sched._provider_last_status.clear()
        sched._dns_update_failures.clear()
        sched._cert_alert_state.clear()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _one(self, sql: str, params: tuple = ()):
        conn = models.get_db()
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()

    def _logs(self) -> list[str]:
        conn = models.get_db()
        try:
            return [r["message"] for r in conn.execute("SELECT message FROM logs ORDER BY id")]
        finally:
            conn.close()

    def _service(self, sid: int, *, target: str = "127.0.0.1", **cols) -> None:
        base = {
            "id": sid,
            "subdomain": f"app{sid}",
            "domain": "example.com",
            "target_ip": target,
            "target_port": 9000 + sid,
            "enabled": 1,
            "status": "ok",
        }
        base.update(cols)
        names = ", ".join(base)
        marks = ", ".join("?" * len(base))
        self._exec(f"INSERT INTO services ({names}) VALUES ({marks})", tuple(base.values()))  # noqa: S608

    def _dns_provider(self, pid: int = 10) -> None:
        self._exec(
            """INSERT INTO providers (id, name, type, url, username, password, enabled)
               VALUES (?, 'dns', 'adguard', 'http://127.0.0.1:3000', 'u', 'p', 1)""",
            (pid,),
        )

    def _auto_dns_service(self, sid: int, dns_ip: str = "1.1.1.1") -> None:
        self._service(
            sid,
            dns_provider_id=10,
            public_target_mode="auto",
            auto_update_dns=1,
            dns_ip=dns_ip,
        )


# ── M2 ───────────────────────────────────────────────────────────────────────

class TestM2HostnameValidation(unittest.TestCase):
    def test_empty_label_is_refused(self):
        self.assertFalse(is_valid_hostname("a..b"))
        self.assertFalse(is_valid_hostname("host.example.com."))
        self.assertFalse(is_valid_hostname(".example.com"))

    def test_label_over_63_characters_is_refused(self):
        self.assertFalse(is_valid_hostname("a" * 64 + ".example.com"))
        self.assertTrue(is_valid_hostname("a" * 63 + ".example.com"))

    def test_name_over_253_characters_is_refused(self):
        name = ".".join(["a" * 63] * 4)  # 255 characters
        self.assertFalse(is_valid_hostname(name))

    def test_label_hyphens_at_the_edges_are_refused(self):
        self.assertFalse(is_valid_hostname("-a.example.com"))
        self.assertFalse(is_valid_hostname("a-.example.com"))

    def test_ordinary_names_and_addresses_still_pass(self):
        for value in ("nas.lan", "my-host.example.com", "Host1.Example.COM", "10.0.0.5", "::1"):
            self.assertTrue(is_valid_hostname(value), value)


class TestM2ProbeSurvivesIdnaError(unittest.TestCase):
    def test_unicode_error_is_reported_as_down(self):
        with patch.object(sched.socket, "create_connection", side_effect=UnicodeError("label too long")):
            self.assertEqual(sched._tcp_ok("a" * 64 + ".example.com", 80), "error")

    def test_real_malformed_name_does_not_raise(self):
        # Fails IDNA encoding before any network traffic.
        self.assertEqual(sched._tcp_ok("a" * 64 + ".example.com", 80), "error")


class TestM2PhasesAreIsolated(_IsolatedDB):
    def _run_cycle(self, **overrides):
        patches = {
            "_sync_npm_once": MagicMock(),
            "_probe_services": MagicMock(return_value=[]),
            "_run_dns_auto_updates": MagicMock(return_value=False),
            "_run_provider_health_checks": MagicMock(return_value=[]),
            "_run_cert_expiry_alerts": MagicMock(),
            "_run_webhook_retry": MagicMock(),
            "_purge_history": MagicMock(),
            "_fire_global_webhook": MagicMock(),
            "_fire_integration_webhook": MagicMock(),
            "_fire_service_webhooks": MagicMock(),
        }
        patches.update(overrides)
        with patch.multiple(sched, **patches):
            sched.run_health_checks()
        return patches

    def test_a_failing_phase_does_not_skip_the_next_ones(self):
        mocks = self._run_cycle(
            _run_cert_expiry_alerts=MagicMock(side_effect=RuntimeError("boom-cert")),
        )
        mocks["_run_webhook_retry"].assert_called_once()
        mocks["_purge_history"].assert_called_once()
        mocks["_fire_service_webhooks"].assert_called_once()
        self.assertTrue(any("Certificate expiry failed" in m and "boom-cert" in m for m in self._logs()))

    def test_a_failing_probe_phase_does_not_stop_the_cycle(self):
        mocks = self._run_cycle(
            _probe_services=MagicMock(side_effect=UnicodeError("label empty or too long")),
        )
        mocks["_run_dns_auto_updates"].assert_called_once()
        mocks["_run_provider_health_checks"].assert_called_once()
        mocks["_purge_history"].assert_called_once()

    def test_uncommitted_writes_of_a_failed_phase_are_rolled_back(self):
        def half_written(conn):
            conn.execute("INSERT INTO logs (level, message) VALUES ('info', 'half-written')")
            raise RuntimeError("after a write")

        mocks = self._run_cycle(_run_dns_auto_updates=half_written)
        mocks["_run_provider_health_checks"].assert_called_once()
        self.assertNotIn("half-written", self._logs())

    def test_malformed_target_in_the_database_does_not_stop_the_cycle(self):
        self._service(1, target="a" * 64 + ".example.com")
        self._service(2, target="a..b")
        mocks = self._run_cycle(_probe_services=sched._probe_services)
        mocks["_purge_history"].assert_called_once()
        row = self._one("SELECT status FROM services WHERE id=1")
        self.assertEqual(row["status"], "error")


# ── M6 ───────────────────────────────────────────────────────────────────────

class TestM6WanFailureIsRememberedForTheCycle(_IsolatedDB):
    def test_failed_wan_lookup_runs_once_per_cycle(self):
        self._dns_provider()
        for sid in (1, 2, 3, 4):
            self._auto_dns_service(sid, dns_ip="")

        detect = MagicMock(return_value="")
        conn = models.get_db()
        try:
            with patch.object(sched, "detect_server_public_ip", detect), \
                 patch("app.public_target.detect_server_public_ip", detect), \
                 patch.object(sched, "create_provider") as cp:
                sched._run_dns_auto_updates(conn)
        finally:
            conn.close()

        self.assertEqual(detect.call_count, 1)
        cp.assert_not_called()

    def _run_with_failed_wan(self, priority: str, proxy_url: str, dns_type: str = "adguard",
                             dns_ip: str = "") -> MagicMock:
        self._exec(
            """INSERT INTO providers (id, name, type, url, username, password, enabled)
               VALUES (20, 'proxy', 'npm', ?, 'u', 'p', 1)""",
            (proxy_url,),
        )
        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES ('public_target_priority', ?)",
                   (priority,))
        self._dns_provider()
        self._exec("UPDATE providers SET type=? WHERE id=10", (dns_type,))
        self._auto_dns_service(1, dns_ip=dns_ip)
        self._exec("UPDATE services SET proxy_provider_id=20 WHERE id=1")

        detect = MagicMock(return_value="")
        fake = MagicMock()
        fake.add_rewrite.return_value = True
        conn = models.get_db()
        try:
            with patch.object(sched, "detect_server_public_ip", detect), \
                 patch("app.public_target.detect_server_public_ip", detect), \
                 patch.object(sched, "create_provider", return_value=fake):
                sched._run_dns_auto_updates(conn)
                conn.commit()
        finally:
            conn.close()
        # B15: the WAN is only looked up when the priority can use it.
        self.assertEqual(detect.call_count, 1 if "server_public_ip" in priority else 0)
        return fake

    def test_the_current_target_is_kept_when_the_preferred_wan_lookup_failed(self):
        """C6: the next candidate (the proxy host) used to be published until the WAN came back."""
        fake = self._run_with_failed_wan("server_public_ip,proxy_provider_host,current",
                                         "http://192.168.1.20:81", dns_ip="203.0.113.9")
        fake.add_rewrite.assert_not_called()
        self.assertEqual(self._one("SELECT dns_ip FROM services WHERE id=1")["dns_ip"], "203.0.113.9")

    def test_other_candidates_still_apply_when_the_wan_is_not_preferred(self):
        fake = self._run_with_failed_wan("proxy_provider_host,current", "http://proxy.example.net:81")
        fake.add_rewrite.assert_called_once_with("app1.example.com", "proxy.example.net")
        self.assertEqual(self._one("SELECT dns_ip FROM services WHERE id=1")["dns_ip"], "proxy.example.net")

    def test_a_lan_address_is_never_written_to_a_public_dns(self):
        """C6: a 192.168.x proxy host is a fine answer for AdGuard, not for Cloudflare."""
        fake = self._run_with_failed_wan("proxy_provider_host,current", "http://192.168.1.20:81",
                                         dns_type="cloudflare")
        fake.add_rewrite.assert_not_called()

    def test_a_lan_address_still_suits_a_local_dns(self):
        fake = self._run_with_failed_wan("proxy_provider_host,current", "http://192.168.1.20:81")
        fake.add_rewrite.assert_called_once_with("app1.example.com", "192.168.1.20")


# ── M7 ───────────────────────────────────────────────────────────────────────

class TestM7DnsAutoUpdateRereadsTheService(_IsolatedDB):
    def _run(self, resolve_side_effect=None, provider=None):
        conn = models.get_db()
        try:
            with patch.object(sched, "load_public_target_policy",
                              return_value={"sources": [], "timeout_seconds": 1}), \
                 patch.object(sched, "detect_server_public_ip", return_value="2.2.2.2"), \
                 patch.object(sched, "resolve_public_target",
                              side_effect=resolve_side_effect) as resolve, \
                 patch.object(sched, "create_provider", return_value=provider or MagicMock()) as cp:
                sched._run_dns_auto_updates(conn)
                conn.commit()
        finally:
            conn.close()
        return resolve, cp

    def test_service_disabled_during_the_cycle_is_not_pushed(self):
        self._dns_provider()
        self._auto_dns_service(1)

        def disable_then_resolve(*_a, **_k):
            self._exec("UPDATE services SET enabled=0 WHERE id=1")
            return "2.2.2.2", "server_public_ip"

        _resolve, cp = self._run(disable_then_resolve)
        cp.assert_not_called()
        self.assertEqual(self._one("SELECT dns_ip FROM services WHERE id=1")["dns_ip"], "1.1.1.1")

    def test_auto_update_turned_off_during_the_cycle_is_not_pushed(self):
        self._dns_provider()
        self._auto_dns_service(1)

        def turn_off_then_resolve(*_a, **_k):
            self._exec("UPDATE services SET auto_update_dns=0 WHERE id=1")
            return "2.2.2.2", "server_public_ip"

        _resolve, cp = self._run(turn_off_then_resolve)
        cp.assert_not_called()

    def test_edit_saved_during_the_provider_call_is_not_overwritten(self):
        self._dns_provider()
        self._auto_dns_service(1)

        fake = MagicMock()

        def update_and_race(*_a, **_k):
            self._exec("UPDATE services SET dns_ip='9.9.9.9' WHERE id=1")
            return True

        fake.update_rewrite.side_effect = update_and_race
        self._run(lambda *_a, **_k: ("2.2.2.2", "server_public_ip"), provider=fake)

        fake.update_rewrite.assert_called_once()
        self.assertEqual(self._one("SELECT dns_ip FROM services WHERE id=1")["dns_ip"], "9.9.9.9")
        self.assertTrue(any("changed while its record was being updated" in m for m in self._logs()))

    def test_untouched_service_is_still_updated(self):
        self._dns_provider()
        self._auto_dns_service(1)
        fake = MagicMock()
        fake.update_rewrite.return_value = True
        self._run(lambda *_a, **_k: ("2.2.2.2", "server_public_ip"), provider=fake)
        self.assertEqual(self._one("SELECT dns_ip FROM services WHERE id=1")["dns_ip"], "2.2.2.2")


# ── M8 ───────────────────────────────────────────────────────────────────────

class TestM8ServiceDeletedDuringProbe(_IsolatedDB):
    def test_deleted_service_does_not_roll_back_the_round(self):
        self._service(1, status="unknown")
        self._service(2, status="unknown")

        def probe_then_delete(services):
            result = [(svc, "ok") for svc in services]
            self._exec("DELETE FROM services WHERE id=1")
            return result

        with patch.multiple(
            sched,
            _sync_npm_once=MagicMock(),
            _probe_services=probe_then_delete,
            _run_dns_auto_updates=MagicMock(return_value=False),
            _run_provider_health_checks=MagicMock(return_value=[]),
            _run_cert_expiry_alerts=MagicMock(),
            _run_webhook_retry=MagicMock(),
            _fire_global_webhook=MagicMock(),
            _fire_integration_webhook=MagicMock(),
            _fire_service_webhooks=MagicMock(),
        ):
            sched.run_health_checks()

        self.assertEqual(self._one("SELECT status FROM services WHERE id=2")["status"], "ok")
        events = self._one("SELECT COUNT(*) AS n FROM uptime_events WHERE service_id=2")["n"]
        self.assertEqual(events, 1)
        self.assertEqual(self._one("SELECT COUNT(*) AS n FROM uptime_events WHERE service_id=1")["n"], 0)
        self.assertFalse(any("Service status update failed" in m for m in self._logs()))


# ── M22 ──────────────────────────────────────────────────────────────────────

class TestM22ProbesRunConcurrently(unittest.TestCase):
    def test_slow_probes_overlap_and_keep_their_order(self):
        services = [
            {"id": i, "target_ip": f"10.0.0.{i}", "target_port": 80} for i in range(1, 13)
        ]
        threads: set[int] = set()

        def slow_probe(ip, _port):
            threads.add(threading.get_ident())
            time.sleep(0.3)
            return "ok" if ip.endswith(("1", "3", "5", "7", "9")) else "error"

        start = time.monotonic()
        with patch.object(sched, "_tcp_ok", side_effect=slow_probe):
            result = sched._probe_services(services)
        elapsed = time.monotonic() - start

        # Sequential would be 12 x 0.3 = 3.6 s.
        self.assertLess(elapsed, 1.5)
        self.assertGreater(len(threads), 1)
        self.assertEqual([svc["id"] for svc, _ in result], list(range(1, 13)))
        for svc, status in result:
            expected = "ok" if svc["target_ip"].endswith(("1", "3", "5", "7", "9")) else "error"
            self.assertEqual(status, expected)

    def test_pool_size_is_bounded(self):
        self.assertLessEqual(sched._PROBE_MAX_WORKERS, 32)
        self.assertEqual(sched._probe_services([]), [])


# ── B4 ───────────────────────────────────────────────────────────────────────

class TestB4SchedulerUsesUtc(unittest.TestCase):
    def test_scheduler_timezone_is_utc(self):
        self.assertEqual(str(sched._scheduler.timezone), "UTC")

    def test_no_deprecated_utcnow(self):
        self.assertNotIn("utcnow(", inspect.getsource(sched))

    def test_retry_stamp_keeps_the_sqlite_format(self):
        stamp = sched._utc_stamp(0)
        parsed = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        self.assertLess(abs((now - parsed).total_seconds()), 5)


# ── B6 ───────────────────────────────────────────────────────────────────────

class TestB6AutoReconcileClosesItsConnection(_IsolatedDB):
    def test_connection_is_closed_when_drift_computation_raises(self):
        self._service(1)
        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')")

        opened: list = []

        class _Tracked:
            def __init__(self, inner):
                self._inner = inner
                self.closed = False

            def close(self):
                self.closed = True
                self._inner.close()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        def tracked_get_db():
            conn = _Tracked(models.get_db())
            opened.append(conn)
            return conn

        push = MagicMock()
        with patch.object(sched, "get_db", tracked_get_db), \
             patch("app.api.sync._compute_service_drift", side_effect=RuntimeError("drift boom")), \
             patch("app.api.sync._execute_push", push):
            sched.run_auto_reconcile()

        self.assertTrue(opened)
        self.assertTrue(all(c.closed for c in opened), "a connection was left open")
        push.assert_not_called()
        self.assertTrue(any("drift boom" in m for m in self._logs()))

    def test_service_disabled_before_its_turn_is_not_pushed(self):
        self._service(1)
        self._service(2)
        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')")

        def drift(_conn, _svc, sid):
            if sid == 1:
                # Service 2 is switched off while service 1 is being handled.
                self._exec("UPDATE services SET enabled=0 WHERE id=2")
            return {"ok": False}

        push = MagicMock(return_value={"ok": True})
        with patch("app.api.sync._compute_service_drift", side_effect=drift), \
             patch("app.api.sync._execute_push", push), \
             patch.object(sched, "_fire_reconcile_webhook"):
            sched.run_auto_reconcile()

        self.assertEqual([c.args[1] for c in push.call_args_list], [1])


# ── B8 ───────────────────────────────────────────────────────────────────────

class TestB8CertificateExpiryTimezones(_IsolatedDB):
    def _run_with_certs(self, certs):
        self._exec(
            """INSERT INTO providers (id, name, type, url, username, password, enabled)
               VALUES (1, 'npm', 'npm', 'http://127.0.0.1:81', 'u', 'p', 1)"""
        )
        fake = MagicMock()
        fake.get_certificates.return_value = certs
        conn = models.get_db()
        try:
            with patch.object(sched, "certificate_provider_types", return_value=["npm"]), \
                 patch.object(sched, "create_provider", return_value=fake):
                sched._run_cert_expiry_alerts(conn)
                conn.commit()
        finally:
            conn.close()
        return self._logs()

    def test_offset_and_naive_expiries_are_both_read_in_utc(self):
        soon = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3, hours=1)
        aware = soon.astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat()
        naive = soon.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        zulu = soon.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logs = self._run_with_certs([
            {"id": 1, "nice_name": "aware", "expires_on": aware},
            {"id": 2, "nice_name": "naive", "expires_on": naive},
            {"id": 3, "nice_name": "zulu", "expires_on": zulu},
        ])
        self.assertFalse(any("Check failed" in m for m in logs), logs)
        for name in ("aware", "naive", "zulu"):
            self.assertTrue(
                any(f"'{name}'" in m and "expires in 3 day(s)" in m for m in logs),
                f"{name}: {logs}",
            )

    def test_offset_is_applied_not_cut_off(self):
        # 23:30 at UTC-05:00 is 04:30 UTC the next day: cutting the offset off read it
        # five hours early.
        base = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)
        local = base.astimezone(datetime.timezone(datetime.timedelta(hours=-5)))
        logs = self._run_with_certs([{"id": 7, "nice_name": "west", "expires_on": local.isoformat()}])
        self.assertTrue(any("'west'" in m and "expires in 9 day(s)" in m for m in logs), logs)


# ── B9 ───────────────────────────────────────────────────────────────────────

class TestB9OrphanedRetriesAreCancelled(_IsolatedDB):
    def _queue(self, webhook_id, url="discord://1/abc", *, due=True, status="pending", age_days=0):
        when = "datetime('now','-1 hour')" if due else "datetime('now','+1 day')"
        self._exec(
            f"""INSERT INTO webhook_delivery_log
                   (webhook_id, url, title, body, status, attempt, next_retry_at, updated_at)
               VALUES (?, ?, 't', 'b', ?, 1, {when}, datetime('now', ?))""",  # noqa: S608
            (webhook_id, url, status, f"-{age_days} days"),
        )

    def test_retries_of_a_disabled_webhook_are_cancelled(self):
        self._exec("INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'off', 'discord://1/abc', 0)")
        self._exec("INSERT INTO webhooks (id, name, url, enabled) VALUES (2, 'on', 'discord://2/abc', 1)")
        self._queue(1)
        self._queue(1, due=False)
        self._queue(2, "discord://2/abc", due=False)

        conn = models.get_db()
        try:
            with patch("apprise.Apprise") as apprise_cls:
                sched._run_webhook_retry(conn)
                conn.commit()
        finally:
            conn.close()

        apprise_cls.assert_not_called()
        conn = models.get_db()
        try:
            rows = conn.execute(
                "SELECT webhook_id, status FROM webhook_delivery_log ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [(r["webhook_id"], r["status"]) for r in rows],
            [(1, "cancelled"), (1, "cancelled"), (2, "pending")],
        )

    def test_ad_hoc_send_is_neither_cancelled_nor_dropped(self):
        self._queue(None, "mailto://ops@example.com")
        conn = models.get_db()
        try:
            with patch("apprise.Apprise") as apprise_cls:
                apprise_cls.return_value.add.return_value = True
                apprise_cls.return_value.notify.return_value = True
                sched._run_webhook_retry(conn)
                conn.commit()
        finally:
            conn.close()
        apprise_cls.return_value.add.assert_called_once_with("mailto://ops@example.com")
        self.assertEqual(self._one("SELECT status FROM webhook_delivery_log")["status"], "delivered")

    def test_cancelled_rows_are_purged_after_retention(self):
        self._exec("INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'off', 'discord://1/abc', 0)")
        self._queue(1, status="cancelled", age_days=30)
        self._queue(1, status="cancelled", age_days=0)
        conn = models.get_db()
        try:
            sched._purge_history(conn)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._one("SELECT COUNT(*) AS n FROM webhook_delivery_log")["n"], 1)

    def test_recovered_alert_is_queued_under_its_webhook(self):
        self._exec(
            """INSERT INTO webhooks (id, name, url, enabled, alert_on_any_down, alert_on_any_up)
               VALUES (5, 'w', 'discord://5/abc', 1, 1, 1)"""
        )
        self._service(1, status="ok")
        key = (5, 1)
        sched._webhook_service_down_since[key] = time.time() - 600
        sched._webhook_service_last_sent[key] = time.time() - 600
        try:
            with patch.object(sched, "_try_send_apprise", return_value=True) as send, \
                 patch.object(sched, "_save_scheduler_state"):
                sched._fire_global_webhook()
        finally:
            sched._webhook_service_down_since.clear()
            sched._webhook_service_last_sent.clear()
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["webhook_id"], 5)


if __name__ == "__main__":
    unittest.main()
