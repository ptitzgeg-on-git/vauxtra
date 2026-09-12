"""Saving a setting and applying it are two steps, and the answer has to tell them apart.

`POST /api/settings` writes the row, commits, and only then hands the value to the running
scheduler. That order is the right one -- the database is the record, the scheduler is a
consequence of it -- but it leaves a window the old code handled by pretending it did not
exist: `except (ImportError, TypeError, ValueError): pass`.

Two of those three could not happen. `_validate_setting` stores `str(number)` for every key
in `_SETTING_RANGES` or refuses the whole payload with a 400, so the `int()` that follows
cannot raise `TypeError` or `ValueError` on a value that reached it. What the clause did
catch was `ImportError`, which is the one case where the value is stored and the health
checks keep running at the old interval -- answered with `{"ok": true, "saved":
["check_interval"]}`, and confirmed by a settings page that reads the stored value back and
shows the number the operator typed. Everything else APScheduler can raise went straight
out of the route as a `500`, for a setting that was already written, so the obvious reaction
-- try again -- repeated a write that had succeeded.

So the tests here hold three things: the value stands whatever the scheduler does, the
answer names the key that did not take effect, and the journal carries the reason. They are
about the *reporting*, not about the scheduler, which is why the failures are injected.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import settings as settings_api


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/settings", "headers": []})


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "applied.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()
        patcher = patch.object(settings_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _save(self, body: dict):
        return settings_api.save_settings(_request(), body)

    def _stored(self, key: str) -> str | None:
        conn = models.get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else None

    def _logs(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT level, message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [f"{r['level']}: {r['message']}" for r in rows]


class AnIntervalThatReachesTheSchedulerTests(_IsolatedDB):
    """The nominal path, so the class below is measuring a difference and not a constant."""

    def test_the_answer_carries_an_empty_not_applied_list(self) -> None:
        with patch("app.scheduler.configure", lambda _m: None):
            result = self._save({"check_interval": "5"})
        self.assertEqual(result["not_applied"], [])
        self.assertEqual(result["saved"], ["check_interval"])
        self.assertEqual(self._stored("check_interval"), "5")

    def test_the_scheduler_is_handed_the_number_that_was_stored(self) -> None:
        seen: list[int] = []
        with patch("app.scheduler.configure", seen.append):
            self._save({"check_interval": " 12 "})
        self.assertEqual(seen, [12])

    def test_nothing_is_written_to_the_journal_when_it_worked(self) -> None:
        with patch("app.scheduler.configure", lambda _m: None):
            self._save({"check_interval": "5"})
        self.assertEqual([line for line in self._logs() if "not be applied" in line], [])


class AnIntervalTheSchedulerRefusesTests(_IsolatedDB):
    """`configure` raises. The row is already committed, so the question is what is said."""

    def _failing(self, exc: Exception):
        def _raise(_minutes):
            raise exc

        return patch("app.scheduler.configure", _raise)

    def test_the_stored_value_stands(self) -> None:
        """It was written before `configure` was called, and it is the record. Rolling it
        back to match a scheduler that is itself broken would lose the operator's input."""
        with self._failing(RuntimeError("scheduler is shutting down")):
            self._save({"check_interval": "7"})
        self.assertEqual(self._stored("check_interval"), "7")

    def test_the_key_is_named_in_the_answer(self) -> None:
        with self._failing(RuntimeError("scheduler is shutting down")):
            result = self._save({"check_interval": "7"})
        self.assertEqual(result["not_applied"], ["check_interval"])

    def test_the_answer_still_says_it_was_saved(self) -> None:
        """`saved` and `not_applied` answer two different questions and both are true here:
        the row is in the database, and the running scheduler has not got it."""
        with self._failing(RuntimeError("scheduler is shutting down")):
            result = self._save({"check_interval": "7"})
        self.assertEqual(result["saved"], ["check_interval"])
        self.assertTrue(result["ok"])

    def test_the_reason_reaches_the_journal(self) -> None:
        with self._failing(RuntimeError("scheduler is shutting down")):
            self._save({"check_interval": "7"})
        lines = [line for line in self._logs() if "check_interval" in line]
        self.assertEqual(len(lines), 1, self._logs())
        self.assertIn("warning", lines[0])
        self.assertIn("RuntimeError", lines[0])
        self.assertIn("scheduler is shutting down", lines[0])

    def test_a_missing_scheduler_module_is_reported_like_any_other_failure(self) -> None:
        """This is the case the old `except (ImportError, ...): pass` caught and hid, and
        the only one of the three it could ever have caught."""
        with self._failing(ImportError("No module named 'apscheduler'")):
            result = self._save({"check_interval": "7"})
        self.assertEqual(result["not_applied"], ["check_interval"])
        self.assertEqual(self._stored("check_interval"), "7")

    def test_an_unrelated_key_in_the_same_payload_is_not_blamed(self) -> None:
        with self._failing(RuntimeError("nope")):
            result = self._save({"check_interval": "7", "theme": "dark"})
        self.assertEqual(result["not_applied"], ["check_interval"])
        self.assertEqual(sorted(result["saved"]), ["check_interval", "theme"])
        self.assertEqual(self._stored("theme"), "dark")


class AutoReconcileIsReportedTheSameWayTests(_IsolatedDB):
    """The second apply step, which reads its values back out of the database.

    Both keys are named under one entry because either of them can be the one that moved and
    the call configures the job from the pair, not from the payload.
    """

    def test_a_refusal_names_the_pair_and_keeps_the_values(self) -> None:
        def _raise(_enabled, _minutes):
            raise RuntimeError("job store is gone")

        with patch("app.scheduler.configure_reconcile", _raise):
            result = self._save(
                {"auto_reconcile_enabled": "true", "auto_reconcile_interval": "30"}
            )
        self.assertEqual(result["not_applied"], ["auto_reconcile"])
        self.assertEqual(self._stored("auto_reconcile_enabled"), "true")
        self.assertEqual(self._stored("auto_reconcile_interval"), "30")

    def test_a_legacy_row_that_is_not_a_number_is_reported_rather_than_ignored(self) -> None:
        """`auto_reconcile_interval` is read back from the database, not from the payload, so
        unlike `check_interval` this `int()` can still meet a row written before
        `_SETTING_RANGES` existed. That is why its conversion stays inside the guard."""
        conn = models.get_db()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('auto_reconcile_interval', 'later')"
        )
        conn.commit()
        conn.close()
        with patch("app.scheduler.configure_reconcile", lambda _e, _m: None):
            result = self._save({"auto_reconcile_enabled": "true"})
        self.assertEqual(result["not_applied"], ["auto_reconcile"])
        self.assertEqual(self._stored("auto_reconcile_enabled"), "true")
        self.assertTrue([line for line in self._logs() if "ValueError" in line], self._logs())

    def test_the_nominal_path_applies_and_reports_nothing(self) -> None:
        seen: list[tuple] = []
        with patch(
            "app.scheduler.configure_reconcile", lambda enabled, minutes: seen.append((enabled, minutes))
        ):
            result = self._save(
                {"auto_reconcile_enabled": "true", "auto_reconcile_interval": "30"}
            )
        self.assertEqual(seen, [(True, 30)])
        self.assertEqual(result["not_applied"], [])


if __name__ == "__main__":
    unittest.main()
