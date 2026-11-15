"""An auto-reconcile round acts on the service as it is now, not as it was when the round began."""

import os
import tempfile
import unittest
from unittest.mock import patch

import app.db as app_db
import app.scheduler as scheduler
from app import models
from app.api import sync as sync_api


class _Round(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "reconcile.test.db")
        models.init_db()
        self._exec(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')"
        )
        for sid in (1, 2):
            self._exec(
                "INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled) "
                "VALUES (?, ?, 'example.com', '192.168.1.9', 8080, 1)",
                (sid, f"app{sid}"),
            )

    def tearDown(self) -> None:
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _run(self, drift, push_result=None):
        pushed: list[int] = []

        def push(svc, sid):
            pushed.append(sid)
            return push_result or {"ok": True, "errors": []}

        with patch.object(sync_api, "_compute_service_drift", drift), \
             patch.object(sync_api, "_execute_push", push), \
             patch.object(scheduler, "_fire_reconcile_webhook") as fired:
            scheduler.run_auto_reconcile()
        return pushed, fired


class AServiceChangedDuringTheRoundTests(_Round):
    def test_one_disabled_during_the_drift_check_is_not_pushed(self):
        def drift(conn, svc, sid, look_elsewhere=False):
            self._exec("UPDATE services SET enabled=0 WHERE id=?", (sid,))
            return {"ok": False}

        pushed, _ = self._run(drift)
        self.assertEqual(pushed, [])

    def test_one_deleted_during_the_drift_check_is_not_pushed(self):
        def drift(conn, svc, sid, look_elsewhere=False):
            self._exec("DELETE FROM services WHERE id=?", (sid,))
            return {"ok": False}

        pushed, _ = self._run(drift)
        self.assertEqual(pushed, [])

    def test_one_disabled_before_its_turn_is_not_even_checked(self):
        checked: list[int] = []

        def drift(conn, svc, sid, look_elsewhere=False):
            checked.append(sid)
            if sid == 1:
                self._exec("UPDATE services SET enabled=0 WHERE id=2")
            return {"ok": True}

        self._run(drift)
        self.assertEqual(checked, [1])

    def test_a_drifting_service_is_still_corrected(self):
        pushed, fired = self._run(lambda conn, svc, sid, look_elsewhere=False: {"ok": False})
        self.assertEqual(pushed, [1, 2])
        fired.assert_called_once()


class TheWebhookNeverCarriesATokenTests(_Round):
    def test_an_error_quoting_a_url_is_masked(self):
        def drift(conn, svc, sid, look_elsewhere=False):
            if sid == 2:
                raise RuntimeError("refused for url: /api/zones?token=s3cr3t-value")
            return {"ok": False}

        _, fired = self._run(drift)
        corrected, errors = fired.call_args.args
        self.assertEqual(corrected, ["app1.example.com"])
        self.assertTrue(errors)
        self.assertNotIn("s3cr3t-value", " ".join(errors))


if __name__ == "__main__":
    unittest.main()
