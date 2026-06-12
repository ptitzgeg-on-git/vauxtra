"""Unit tests for scheduler logic — health checks, DNS auto-update, circuit breaker."""

import time
import unittest
from unittest.mock import MagicMock, patch

from app import scheduler as sched


def _make_conn(services=None, providers=None, alert_rows=None, settings=None):
    """Build a mock SQLite connection that returns canned query results."""
    conn = MagicMock()

    def _execute(sql, params=()):
        mock_cursor = MagicMock()

        def _fetchall():
            sql_lower = sql.strip().lower()
            if "services" in sql_lower and "enabled=1" in sql_lower:
                return services or []
            if "providers" in sql_lower and "enabled=1" in sql_lower:
                return providers or []
            if "service_alerts" in sql_lower:
                return alert_rows or []
            if "settings" in sql_lower and "key in" in sql_lower:
                return settings or []
            return []

        def _fetchone():
            sql_lower = sql.strip().lower()
            if "scheduler_state" in sql_lower:
                return None
            if "settings" in sql_lower:
                return None
            return None

        mock_cursor.fetchall = _fetchall
        mock_cursor.fetchone = _fetchone
        return mock_cursor

    conn.execute = _execute
    conn.commit = MagicMock()
    conn.close = MagicMock()
    return conn


def _make_service(**kwargs) -> MagicMock:
    defaults = {
        "id": 1,
        "subdomain": "app",
        "domain": "home.local",
        "target_ip": "192.168.1.10",
        "target_port": 3000,
        "expose_mode": "proxy_dns",
        "status": "unknown",
        "dns_provider_id": None,
        "proxy_provider_id": None,
        "tunnel_provider_id": None,
        "dns_ip": "",
        "public_target_mode": "manual",
        "auto_update_dns": 0,
    }
    defaults.update(kwargs)
    row = MagicMock()
    row.__getitem__ = lambda self, key: defaults.get(key)
    row.get = lambda key, default=None: defaults.get(key, default)
    return row


class TestTCPHealthCheck(unittest.TestCase):

    def test_tcp_ok_returns_ok(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock(return_value=None)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            result = sched._tcp_ok("127.0.0.1", 80)
        self.assertEqual(result, "ok")

    def test_tcp_error_returns_error(self):
        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = sched._tcp_ok("10.0.0.1", 9999)
        self.assertEqual(result, "error")


class TestProviderHealthChecks(unittest.TestCase):

    def setUp(self):
        sched._provider_last_status.clear()

    def _make_provider_row(self, pid: int, name: str, ptype: str = "npm") -> MagicMock:
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": pid, "name": name, "type": ptype,
            "url": "http://provider", "username": "u", "password": "p", "extra": "{}",
            "enabled": 1,
        }[key]
        return row

    def _mock_provider_down(self):
        mock_provider = MagicMock()
        mock_provider.health_status.return_value = {"ok": False, "status": "down"}
        mock_provider.test_connection.return_value = False
        return mock_provider

    def _mock_provider_up(self):
        mock_provider = MagicMock()
        mock_provider.health_status.return_value = {"ok": True, "status": "healthy"}
        mock_provider.test_connection.return_value = True
        return mock_provider

    def test_provider_down_transition_logged(self):
        sched._provider_last_status[1] = "ok"  # Was up
        row = self._make_provider_row(1, "NPM")

        conn = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row])))

        with patch("app.scheduler.create_provider") as mock_cp:
            mock_cp.return_value = self._mock_provider_down()
            changed = sched._run_provider_health_checks(conn)

        self.assertTrue(any(c["new"] == "error" for c in changed))
        self.assertEqual(sched._provider_last_status.get(1), "error")

    def test_provider_up_transition_logged(self):
        sched._provider_last_status[2] = "error"  # Was down
        row = self._make_provider_row(2, "AdGuard")

        conn = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row])))

        with patch("app.scheduler.create_provider") as mock_cp:
            mock_cp.return_value = self._mock_provider_up()
            changed = sched._run_provider_health_checks(conn)

        self.assertTrue(any(c["new"] == "ok" for c in changed))

    def test_new_provider_not_logged_as_changed(self):
        """First time a provider is seen → no transition logged (status was 'unknown')."""
        sched._provider_last_status.clear()
        row = self._make_provider_row(3, "NewProvider")

        conn = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row])))

        with patch("app.scheduler.create_provider") as mock_cp:
            mock_cp.return_value = self._mock_provider_up()
            changed = sched._run_provider_health_checks(conn)

        self.assertEqual(changed, [])

    def test_removed_provider_cleaned_from_state(self):
        """Provider that disappears from DB should be removed from state map."""
        sched._provider_last_status[99] = "ok"

        conn = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))

        sched._run_provider_health_checks(conn)
        self.assertNotIn(99, sched._provider_last_status)


class TestSchedulerStatePersistence(unittest.TestCase):

    def test_dump_and_load_tuple_value_map(self):
        original = {(1, 2): 100.5, (3, 4): 200.0}
        dumped = sched._dump_tuple_value_map(original)
        restored = sched._load_tuple_value_map(__import__("json").loads(dumped))
        self.assertEqual(restored, original)

    def test_dump_and_load_tuple_set(self):
        original = {(1, 2), (3, 4), (5, 6)}
        dumped = sched._dump_tuple_set(original)
        restored = sched._load_tuple_set(__import__("json").loads(dumped))
        self.assertEqual(restored, original)

    def test_load_tuple_value_map_from_invalid_input(self):
        self.assertEqual(sched._load_tuple_value_map(None), {})
        self.assertEqual(sched._load_tuple_value_map("not a dict"), {})

    def test_load_tuple_set_from_invalid_input(self):
        self.assertEqual(sched._load_tuple_set(None), set())
        self.assertEqual(sched._load_tuple_set({}), set())

    def test_decode_tuple_key_from_list(self):
        result = sched._decode_tuple_key([1, 2])
        self.assertEqual(result, (1, 2))

    def test_decode_tuple_key_from_json_string(self):
        result = sched._decode_tuple_key("[1, 2]")
        self.assertEqual(result, (1, 2))

    def test_decode_tuple_key_invalid(self):
        self.assertIsNone(sched._decode_tuple_key("invalid"))
        self.assertIsNone(sched._decode_tuple_key([1]))  # Wrong length


class TestDNSAutoUpdate(unittest.TestCase):

    def setUp(self):
        sched._dns_update_failures.clear()

    def _make_dns_service(self, sid: int = 1):
        row = MagicMock()
        data = {
            "id": sid,
            "subdomain": "app",
            "domain": "home.local",
            "dns_ip": "1.1.1.1",
            "dns_provider_id": 10,
            "proxy_provider_id": None,
            "public_target_mode": "auto",
            "auto_update_dns": 1,
            "enabled": 1,
        }
        row.__getitem__ = lambda s, k: data[k]
        row.get = lambda k, d=None: data.get(k, d)
        return row

    def test_circuit_breaker_triggers_after_threshold(self):
        svc = self._make_dns_service(1)

        conn = MagicMock()
        provider_row = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(
            fetchall=MagicMock(return_value=[svc]),
            fetchone=MagicMock(return_value=provider_row),
        ))

        with patch("app.scheduler.load_public_target_policy", return_value={"sources": ["detect"], "timeout_seconds": 5}), \
             patch("app.scheduler.detect_server_public_ip", return_value="2.2.2.2"), \
             patch("app.scheduler.resolve_public_target", return_value=("2.2.2.2", "detect")), \
             patch("app.scheduler.create_provider") as mock_cp:

            mock_provider = MagicMock()
            mock_provider.update_rewrite.side_effect = Exception("DNS failed")
            mock_provider.add_rewrite.side_effect = Exception("DNS failed")
            mock_cp.return_value = mock_provider

            # Simulate threshold-many failures
            for _ in range(sched._DNS_FAILURE_THRESHOLD):
                sched._dns_update_failures.clear()  # Reset between runs for isolation
                sched._dns_update_failures[1] = sched._DNS_FAILURE_THRESHOLD - 1
                sched._run_dns_auto_updates(conn)

        # After threshold, auto_update_dns should be disabled via UPDATE call
        update_calls = [str(call) for call in conn.execute.call_args_list]
        self.assertTrue(any("auto_update_dns=0" in c for c in update_calls))

    def test_success_resets_failure_count(self):
        svc = self._make_dns_service(1)
        sched._dns_update_failures[1] = 2  # Had 2 failures

        conn = MagicMock()
        provider_row = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(
            fetchall=MagicMock(return_value=[svc]),
            fetchone=MagicMock(return_value=provider_row),
        ))

        with patch("app.scheduler.load_public_target_policy", return_value={"sources": ["detect"], "timeout_seconds": 5}), \
             patch("app.scheduler.detect_server_public_ip", return_value="2.2.2.2"), \
             patch("app.scheduler.resolve_public_target", return_value=("2.2.2.2", "detect")), \
             patch("app.scheduler.create_provider") as mock_cp:

            mock_provider = MagicMock()
            mock_provider.update_rewrite.return_value = True
            mock_cp.return_value = mock_provider

            sched._run_dns_auto_updates(conn)

        self.assertNotIn(1, sched._dns_update_failures)

    def test_no_update_when_ip_unchanged(self):
        svc = self._make_dns_service(1)

        conn = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(
            fetchall=MagicMock(return_value=[svc]),
        ))

        with patch("app.scheduler.load_public_target_policy", return_value={"sources": [], "timeout_seconds": 5}), \
             patch("app.scheduler.detect_server_public_ip", return_value="1.1.1.1"), \
             patch("app.scheduler.resolve_public_target", return_value=("1.1.1.1", "detect")), \
             patch("app.scheduler.create_provider") as mock_cp:

            mock_provider = MagicMock()
            mock_cp.return_value = mock_provider

            sched._run_dns_auto_updates(conn)

        # No DNS write should happen when IP didn't change
        mock_provider.update_rewrite.assert_not_called()
        mock_provider.add_rewrite.assert_not_called()


class TestReadRetentionDays(unittest.TestCase):

    def _conn_with_value(self, val: str):
        conn = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda s, k: val
        row.get = lambda k, d=None: val
        conn.execute = MagicMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
        return conn

    def test_reads_valid_value(self):
        conn = self._conn_with_value("7")
        result = sched._read_retention_days(conn, "log_retention_days", 30)
        self.assertEqual(result, 7)

    def test_clamps_to_minimum(self):
        conn = self._conn_with_value("0")
        result = sched._read_retention_days(conn, "log_retention_days", 30, min_days=1)
        self.assertEqual(result, 1)

    def test_clamps_to_maximum(self):
        conn = self._conn_with_value("9999")
        result = sched._read_retention_days(conn, "log_retention_days", 30, max_days=365)
        self.assertEqual(result, 365)

    def test_returns_default_when_key_missing(self):
        conn = MagicMock()
        conn.execute = MagicMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
        result = sched._read_retention_days(conn, "missing_key", 42)
        self.assertEqual(result, 42)

    def test_returns_default_on_invalid_value(self):
        conn = self._conn_with_value("not_a_number")
        result = sched._read_retention_days(conn, "key", 14)
        self.assertEqual(result, 14)


if __name__ == "__main__":
    unittest.main()
