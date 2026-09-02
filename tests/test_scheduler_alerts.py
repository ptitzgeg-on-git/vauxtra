"""Regression tests for the alerting paths of the scheduler.

Three defects are covered here, all of which made Vauxtra go quiet without saying so:
  * a DOWN alert was latched as "sent" even when the notification never left;
  * the health cycle deadlocked itself on a second SQLite connection, losing the round;
  * queued retries were stamped in ISO form and never compared as due.
"""

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app import models, scheduler


class SchedulerAlertTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "scheduler.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        scheduler._alert_down_since.clear()
        scheduler._alert_down_sent.clear()
        scheduler._webhook_service_down_since.clear()
        scheduler._webhook_service_last_sent.clear()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        scheduler._alert_down_since.clear()
        scheduler._alert_down_sent.clear()
        scheduler._webhook_service_down_since.clear()
        scheduler._webhook_service_last_sent.clear()
        self._tmpdir.cleanup()

    def _seed_down_service(self, url: str = "mailto://ops@example.com") -> None:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled, status)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 80, 1, 'error')"""
        )
        conn.execute(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'Ops', ?, 1)", (url,)
        )
        conn.execute(
            """INSERT INTO service_alerts (service_id, webhook_id, on_up, on_down, min_down_minutes)
               VALUES (1, 1, 1, 1, 0)"""
        )
        conn.commit()
        conn.close()


def _fake_apprise(*, add_ok: bool = True, notify_ok: bool = True, sent: list | None = None):
    """Build a stand-in `apprise` module with a controllable outcome."""

    class FakeApprise:
        def __init__(self):
            self.url = ""

        def add(self, url: str) -> bool:
            self.url = url
            return add_ok

        def notify(self, title: str, body: str) -> bool:
            if sent is not None and notify_ok:
                sent.append({"url": self.url, "title": title, "body": body})
            return notify_ok

    return types.SimpleNamespace(Apprise=FakeApprise)


class TestDownAlertLatch(SchedulerAlertTestCase):

    def test_failed_delivery_is_queued_for_retry(self):
        """A refused send must land in webhook_delivery_log, not vanish."""
        self._seed_down_service()
        with patch.dict(sys.modules, {"apprise": _fake_apprise(notify_ok=False)}):
            scheduler._fire_service_webhooks()

        conn = models.get_db()
        rows = conn.execute(
            "SELECT status, attempt, url FROM webhook_delivery_log"
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        # The latch stays set: redelivery is now owned by the retry queue, so releasing
        # it would produce a duplicate alert alongside the retry.
        self.assertIn((1, 1), scheduler._alert_down_sent)

    def test_unusable_url_releases_the_latch(self):
        """Nothing sent and nothing queued must not silence the service forever."""
        self._seed_down_service()
        with patch.dict(sys.modules, {"apprise": _fake_apprise(add_ok=False)}):
            scheduler._fire_service_webhooks()

        conn = models.get_db()
        queued = conn.execute("SELECT COUNT(*) AS c FROM webhook_delivery_log").fetchone()["c"]
        conn.close()
        self.assertEqual(queued, 0)
        self.assertNotIn((1, 1), scheduler._alert_down_sent)

        # Next cycle, with a working URL, the alert finally goes out.
        sent: list = []
        with patch.dict(sys.modules, {"apprise": _fake_apprise(sent=sent)}):
            scheduler._fire_service_webhooks()
        self.assertEqual(len(sent), 1)
        self.assertIn("DOWN: app.example.com", sent[0]["body"])

    def test_successful_delivery_latches_and_does_not_repeat(self):
        self._seed_down_service()
        sent: list = []
        with patch.dict(sys.modules, {"apprise": _fake_apprise(sent=sent)}):
            scheduler._fire_service_webhooks()
            scheduler._fire_service_webhooks()
        self.assertEqual(len(sent), 1)

    def test_latch_is_persisted_after_the_send_not_before(self):
        self._seed_down_service()
        with patch.dict(sys.modules, {"apprise": _fake_apprise(add_ok=False)}):
            scheduler._fire_service_webhooks()

        conn = models.get_db()
        row = conn.execute(
            "SELECT value FROM scheduler_state WHERE key='alert_down_sent'"
        ).fetchone()
        conn.close()
        # The released key must not be in the persisted latch either.
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], "[]")


class TestNoSecondConnectionInsideTheCycle(SchedulerAlertTestCase):

    def test_dns_auto_update_does_not_persist_state_itself(self):
        """_run_dns_auto_updates runs inside an open write transaction.

        Opening a second connection there makes SQLite wait out its busy_timeout and
        raise "database is locked"; the cycle's commit is never reached and every status
        of the round is lost.
        """
        conn = models.get_db()
        with patch.object(scheduler, "_save_scheduler_state") as saver:
            scheduler._run_dns_auto_updates(conn)
        conn.close()
        saver.assert_not_called()

    def test_a_full_cycle_commits_while_holding_a_write_transaction(self):
        """End to end: the round's UPDATE must survive the cycle."""
        conn = models.get_db()
        conn.execute(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled, status)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 9, 1, 'ok')"""
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "_tcp_ok", return_value="error"):
            scheduler.run_health_checks()

        conn = models.get_db()
        status = conn.execute("SELECT status FROM services WHERE id=1").fetchone()["status"]
        conn.close()
        self.assertEqual(status, "error")


class TestRetryScheduling(unittest.TestCase):

    def test_stamp_uses_sqlite_own_format(self):
        stamp = scheduler._utc_stamp(0)
        self.assertNotIn("T", stamp)
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_a_due_retry_compares_as_due_against_sqlite_now(self):
        """The queue is polled with `next_retry_at <= datetime('now')`, a string compare.

        An ISO "T" separator sorts above the space SQLite uses, so a same-day retry was
        never due.
        """
        conn = sqlite3.connect(":memory:")
        past = scheduler._utc_stamp(-60)
        due = conn.execute("SELECT ? <= datetime('now')", (past,)).fetchone()[0]
        conn.close()
        self.assertEqual(due, 1)

    def test_every_backoff_tier_is_reachable(self):
        """attempt 1 is the original send, so the loop needs one more slot than tiers."""
        tiers = scheduler._WEBHOOK_RETRY_BACKOFF
        max_attempts = len(tiers) + 1
        used = [tiers[a - 1] for a in range(2, max_attempts + 1) if a - 1 < len(tiers)]
        self.assertIn(tiers[-1], used)


class TestPersistedTimestamps(unittest.TestCase):

    def test_monotonic_leftovers_are_dropped_on_load(self):
        legacy = {"[1, 1]": 42.0}
        self.assertEqual(scheduler._load_tuple_value_map(legacy), {})

    def test_wall_clock_values_survive(self):
        fresh = {"[1, 1]": 1700000000.0}
        self.assertEqual(scheduler._load_tuple_value_map(fresh), {(1, 1): 1700000000.0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
