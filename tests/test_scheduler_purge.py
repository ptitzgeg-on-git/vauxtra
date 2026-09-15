"""Retention sweeps must bound their tables, say so when they cannot, and cost nothing else.

`_purge_history` is the only thing in the product that bounds `uptime_events`, `logs` and
`webhook_delivery_log`. It ran three DELETEs, and it handled their failures two different
ways, neither of them right. The webhook sweep sat in `except Exception: pass`, so it could
stop working for good without leaving a trace -- the retry queue growing without bound while
the panel reported nothing. The other two sat in nothing at all, so a failure there left the
function altogether, and what it took with it was the rest of the cycle.

That second half is the expensive one, and it is not obvious from the call site.
`run_health_checks` dispatches this cycle's alerts *after* the purge, and
`_provider_last_status` has already been advanced to the status those alerts describe. So a
purge that raises does not postpone an integration alert to the next cycle: the next cycle
compares the new status against itself, finds no transition, and says nothing. The alert is
gone. APScheduler keeps the job -- the cycle itself survives -- which is precisely why this
was invisible.

The tests below fix both ends: the sweeps still sweep, a failure is recorded rather than
swallowed, one broken sweep does not stop its siblings, and no failure reaches the caller.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from app import models, scheduler


class PurgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "purge.test.db")
        models.init_db()
        scheduler._provider_last_status.clear()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        scheduler._provider_last_status.clear()
        self._tmpdir.cleanup()

    # ── Seeding ───────────────────────────────────────────────────────────────────
    #
    # Every row is written with an explicit `created_at`/`updated_at`, because the whole
    # question these tests ask is which side of a retention boundary a row falls on.
    # `days` counts backwards from now, so 400 is old under any of the three defaults and
    # 0 is today under all of them.

    def _seed(self, conn, *, uptime_days, log_days, delivery, service_id=1, webhook_id=1):
        conn.execute(
            """INSERT OR IGNORE INTO services (id, subdomain, domain, target_ip, target_port,
               enabled, status) VALUES (?, 'app', 'example.com', '127.0.0.1', 8080, 1, 'ok')""",
            (service_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO webhooks (id, name, url, enabled) VALUES (?, 'w', 'u', 1)",
            (webhook_id,),
        )
        for days in uptime_days:
            conn.execute(
                """INSERT INTO uptime_events (service_id, status, created_at)
                   VALUES (?, 'ok', datetime('now', ?))""",
                (service_id, f"-{days} days"),
            )
        for days in log_days:
            conn.execute(
                "INSERT INTO logs (level, message, created_at) VALUES ('info', 'x', datetime('now', ?))",
                (f"-{days} days",),
            )
        for status, days in delivery:
            conn.execute(
                """INSERT INTO webhook_delivery_log (webhook_id, url, title, body, status,
                   attempt, updated_at) VALUES (?, 'u', 't', 'b', ?, 1, datetime('now', ?))""",
                (webhook_id, status, f"-{days} days"),
            )
        conn.commit()

    def _counts(self, conn):
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("uptime_events", "logs", "webhook_delivery_log")
        }

    def _count(self, conn, table):
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # ── The sweeps still sweep ────────────────────────────────────────────────────

    def test_each_table_is_swept_at_its_own_retention(self):
        """The three sweeps keep their separate settings, and their separate defaults."""
        conn = models.get_db()
        try:
            self._seed(
                conn,
                uptime_days=[400, 0],
                log_days=[400, 0],
                delivery=[("delivered", 400), ("delivered", 0)],
            )
            self.assertEqual(self._counts(conn), {"uptime_events": 2, "logs": 2, "webhook_delivery_log": 2})
            scheduler._purge_history(conn)
            conn.commit()
            self.assertEqual(self._counts(conn), {"uptime_events": 1, "logs": 1, "webhook_delivery_log": 1})
        finally:
            conn.close()

    def test_a_pending_delivery_is_kept_however_old_the_attempt_is(self):
        """`pending` is the retry queue's own work. Age is not a reason to drop it."""
        conn = models.get_db()
        try:
            self._seed(
                conn,
                uptime_days=[],
                log_days=[],
                delivery=[("pending", 400), ("delivered", 400), ("failed", 400)],
            )
            scheduler._purge_history(conn)
            conn.commit()
            rows = conn.execute("SELECT status FROM webhook_delivery_log").fetchall()
            self.assertEqual([r["status"] for r in rows], ["pending"])
        finally:
            conn.close()

    def test_a_configured_retention_is_read_per_sweep(self):
        """Each sweep reads its own setting, so a tightened one takes effect on its own."""
        conn = models.get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('monitoring_retention_days', '1')"
            )
            self._seed(conn, uptime_days=[3], log_days=[3], delivery=[])
            scheduler._purge_history(conn)
            conn.commit()
            # Three days old: past the monitoring retention now set to one day, and well
            # inside the log retention still at its default of thirty.
            self.assertEqual(self._count(conn, "uptime_events"), 0)
            self.assertEqual(self._count(conn, "logs"), 1)
        finally:
            conn.close()

    # ── A sweep that fails ────────────────────────────────────────────────────────

    def test_one_broken_sweep_does_not_stop_its_siblings(self):
        """Three independent tables. One of them refusing is not a reason to keep the rest."""
        conn = models.get_db()
        try:
            self._seed(conn, uptime_days=[400], log_days=[400], delivery=[("delivered", 400)])
            conn.execute("DROP TABLE uptime_events")
            conn.commit()

            scheduler._purge_history(conn)
            conn.commit()

            # The two sweeps after the broken one still ran.
            self.assertEqual(self._count(conn, "webhook_delivery_log"), 0)
            seeded = conn.execute("SELECT COUNT(*) FROM logs WHERE message='x'").fetchone()[0]
            self.assertEqual(seeded, 0)
        finally:
            conn.close()

    def test_a_broken_sweep_is_recorded_rather_than_swallowed(self):
        """The sweep that used to fail in silence now names itself in the log the panel shows."""
        conn = models.get_db()
        try:
            self._seed(conn, uptime_days=[], log_days=[], delivery=[])
            conn.execute("DROP TABLE webhook_delivery_log")
            conn.commit()

            scheduler._purge_history(conn)
            conn.commit()

            rows = conn.execute(
                "SELECT level, message FROM logs WHERE message LIKE '[Purge]%'"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["level"], "error")
            self.assertIn("webhook_delivery_log", rows[0]["message"])
            # The traceback, not just a sentence: the operator needs to see what refused.
            self.assertIn("Traceback", rows[0]["message"])
        finally:
            conn.close()

    def test_nothing_escapes_even_when_recording_the_failure_is_impossible(self):
        """The states that break a sweep hardest break the record of it too.

        A locked base or a full disk refuses the DELETE and then refuses the `add_log`
        that came to report it. If that second failure were allowed out, the handler
        would hand the cycle the very fate it exists to prevent. Here the `logs` table
        is gone, which breaks both halves at once.
        """
        conn = models.get_db()
        try:
            self._seed(conn, uptime_days=[400], log_days=[], delivery=[("delivered", 400)])
            conn.execute("DROP TABLE logs")
            conn.commit()

            scheduler._purge_history(conn)  # must not raise
            conn.commit()

            # And the sweeps on either side of the broken one still did their work.
            self.assertEqual(self._count(conn, "uptime_events"), 0)
            self.assertEqual(self._count(conn, "webhook_delivery_log"), 0)
        finally:
            conn.close()

    # ── What the failure used to cost ─────────────────────────────────────────────

    def test_a_failed_purge_does_not_cost_the_cycle_its_integration_alert(self):
        """The reason the handler lives inside `_purge_history` and not around its call.

        `run_health_checks` purges and *then* dispatches. A purge that raised left the
        function, skipped the dispatch, and the alert was not postponed to the next cycle
        but lost: `_provider_last_status` had already been advanced to the new status, so
        the next cycle compared it against itself and found nothing to report.
        """
        conn = models.get_db()
        try:
            conn.execute(
                """INSERT INTO providers (id, name, type, url, username, password, enabled)
                   VALUES (1, 'proxy', 'npm', 'http://127.0.0.1:1', 'u', 'p', 1)"""
            )
            conn.execute(
                """INSERT INTO uptime_events (service_id, status, created_at)
                   SELECT 1, 'ok', datetime('now') WHERE 0"""
            )
            conn.commit()
            conn.execute("DROP TABLE uptime_events")
            conn.commit()
        finally:
            conn.close()

        # The provider was healthy last cycle, so this cycle's failure is a transition.
        scheduler._provider_last_status[1] = "ok"

        fired = []
        with (
            patch.object(scheduler, "_fire_integration_webhook", lambda changed: fired.append(changed)),
            patch.object(scheduler, "_fire_global_webhook", lambda: None),
            patch.object(scheduler, "_fire_service_webhooks", lambda: None),
        ):
            scheduler.run_health_checks()

        self.assertEqual(len(fired), 1, "the purge failure swallowed the cycle's alert")
        self.assertEqual(
            [(c["provider_id"], c["old"], c["new"]) for c in fired[0]],
            [(1, "ok", "error")],
        )


if __name__ == "__main__":
    unittest.main()
