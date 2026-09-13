"""A restore that drops a row has to say which row -- these tests hold two silences shut.

Two `continue` statements decided the fate of rows in the backup file and said nothing. A
domain with no name vanished, and so did every setting this version does not accept. The
answer was `{"ok": true}` either way, so the operator found out the next time they opened
the page that needed the setting, with no reason to connect it to the restore.

The other half of the problem is the opposite failure, and it is the one these tests are
mostly here to guard. Six keys travel in every backup file and are dropped on purpose by
every restore -- the admin hash, the setup marker, the schema version, the auth mode, the
session epoch, and a one-shot migration marker. A report that named those would fire on
every single restore and teach the operator to skip the line, which costs more than the
silence it replaced. `TheDropsMadeOnPurposeStaySilentTests` is what fails if that
calibration is ever lost.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import backup as backup_api
from app.api.backup import RestoreRequest
from app.api.settings import _PROTECTED_SETTINGS
from app.limiter import limiter as _app_limiter

# Dropped on every restore, by design. Held here as a literal rather than imported from
# `_RESTORE_DROPS_ON_PURPOSE`, so that a key quietly added to that set fails this file
# instead of being blessed by it.
DESIGNED_DROPS = (
    "app_password_hash",
    "setup_completed",
    "schema_version",
    "auth_mode",
    "session_epoch",
    "webhook_log_purge_done",
)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/restore", "headers": []})


def _file(domains=None, settings=None, **extra) -> dict:
    """The smallest thing `import_backup` accepts, plus whatever the test is about."""
    data = {"version": "1.4.0"}
    if domains is not None:
        data["domains"] = domains
    if settings is not None:
        data["settings"] = [{"key": k, "value": v} for k, v in settings]
    data.update(extra)
    return data


class _RestoreBench(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)

        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "restore.test.db")
        models.init_db()

        self._auth_patch = patch.object(backup_api, "require_auth", lambda *a, **k: None)
        self._auth_patch.start()

        # The route is capped at 3/minute. These tests restore a dozen files in a second.
        def _no_rate_limit(request, *_args, **_kwargs):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(_app_limiter, "_check_request_limit", _no_rate_limit)
        self._limiter_patch.start()

    def tearDown(self) -> None:
        self._limiter_patch.stop()
        self._auth_patch.stop()
        import app.db as _app_db
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -------------------------------------------------------------- measuring instruments
    def _restore(self, data: dict) -> dict:
        return backup_api.import_backup(_request(), RestoreRequest(backup=data, passphrase=""))

    def _logs(self) -> list:
        conn = models.get_db()
        try:
            rows = conn.execute("SELECT level, message FROM logs ORDER BY id").fetchall()
        finally:
            conn.close()
        return [(r["level"], r["message"]) for r in rows]

    def _warnings(self) -> list:
        return [m for level, m in self._logs() if level == "warning"]

    def _domains(self) -> list:
        conn = models.get_db()
        try:
            rows = conn.execute("SELECT name FROM domains ORDER BY name").fetchall()
        finally:
            conn.close()
        return [r["name"] for r in rows]

    def _setting(self, key: str):
        conn = models.get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
        return row["value"] if row else None


class RestoreNamesTheSettingsItCannotTakeTests(_RestoreBench):
    def test_a_file_this_version_fully_accepts_restores_without_a_word_of_complaint(self) -> None:
        """The witness. Everything below is worthless if the quiet case is not quiet."""
        result = self._restore(
            _file(domains=[{"name": "vxlab.test"}], settings=[("theme", "dark"), ("timezone", "UTC")])
        )

        self.assertEqual(result["settings_not_restored"], [], result)
        self.assertEqual(result["domains_without_name"], 0, result)
        self.assertEqual(self._warnings(), [])
        self.assertEqual(self._setting("theme"), "dark")

    def test_a_setting_this_version_cannot_take_is_named_in_the_answer(self) -> None:
        result = self._restore(_file(settings=[("theme", "dark"), ("notify_on_drift", "1")]))

        self.assertEqual(result["settings_not_restored"], ["notify_on_drift"], result)
        # The rest of the file still lands: one key this version retired does not cost the
        # operator the restore.
        self.assertEqual(self._setting("theme"), "dark")

    def test_the_same_setting_is_named_in_the_journal(self) -> None:
        """The answer is read once, by the panel that asked. The journal is read later."""
        self._restore(_file(settings=[("smtp_host", "mail.example.test")]))

        warnings = self._warnings()
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("smtp_host", warnings[0])
        self.assertIn("dropped rather than restored", warnings[0])

    def test_a_setting_row_with_no_key_is_not_reported_as_a_lost_setting(self) -> None:
        """A malformed row is not a setting the operator configured and lost.

        `{"value": "x"}` with no key names nothing that could be put back, so naming it in
        the report would only ask the operator to go looking for something that was never
        there.
        """
        # Built by hand: `_file` cannot express a row that has no key at all.
        data = _file(settings=[("theme", "dark")])
        data["settings"].append({"value": "orphan"})
        result = self._restore(data)

        self.assertEqual(result["settings_not_restored"], [], result)
        self.assertEqual(self._warnings(), [])

    def test_the_names_come_back_in_a_stable_order(self) -> None:
        """Two restores of the same file must not produce two different-looking reports."""
        result = self._restore(
            _file(settings=[("smtp_host", "a"), ("notify_on_drift", "b"), ("alpha_key", "c")])
        )

        self.assertEqual(
            result["settings_not_restored"], ["alpha_key", "notify_on_drift", "smtp_host"], result
        )


class TheDropsMadeOnPurposeStaySilentTests(_RestoreBench):
    """The calibration test. A detector that fires on every restore reports nothing."""

    def test_the_keys_the_restore_drops_by_design_stay_silent(self) -> None:
        result = self._restore(_file(settings=[(key, "1") for key in DESIGNED_DROPS]))

        self.assertEqual(result["settings_not_restored"], [], result)
        self.assertEqual(self._warnings(), [])

    def test_an_unknown_key_is_still_named_when_the_designed_drops_travel_with_it(self) -> None:
        """The one that proves the silence above is a filter and not a switched-off check."""
        settings = [(key, "1") for key in DESIGNED_DROPS] + [("smtp_host", "mail.example.test")]

        result = self._restore(_file(settings=settings))

        self.assertEqual(result["settings_not_restored"], ["smtp_host"], result)
        self.assertEqual(len(self._warnings()), 1, self._warnings())

    def test_the_protected_settings_are_the_ones_the_wipe_itself_keeps(self) -> None:
        """The two lists have to agree, and nothing else in the app makes them agree.

        `_RESTORE_DROPS_ON_PURPOSE` is built from `_PROTECTED_SETTINGS` for exactly this
        reason: a key added to the protected tuple is preserved by the wipe, ignored by
        `_VALID_SETTINGS`, and would start being reported as lost on every restore if the
        two had to be kept in step by hand.
        """
        for key in _PROTECTED_SETTINGS:
            self.assertIn(key, backup_api._RESTORE_DROPS_ON_PURPOSE, key)


class RestoreCountsTheDomainsItCannotWriteTests(_RestoreBench):
    def test_a_domain_row_with_no_name_is_counted_not_swallowed(self) -> None:
        """Two shapes of the same defect: an empty name, and no name field at all."""
        result = self._restore(
            _file(domains=[{"name": "vxlab.test"}, {"name": ""}, {"created_at": "2026-01-01"}])
        )

        self.assertEqual(result["domains_without_name"], 2, result)
        # The nameless rows cost nothing to the row that had a name.
        self.assertEqual(self._domains(), ["vxlab.test"])

    def test_the_count_reaches_the_journal(self) -> None:
        self._restore(_file(domains=[{"name": ""}, {"name": ""}]))

        warnings = self._warnings()
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("2 domains", warnings[0])
        self.assertIn("carried no name", warnings[0])

    def test_a_file_whose_domains_all_have_names_says_nothing(self) -> None:
        result = self._restore(_file(domains=[{"name": "a.vxlab.test"}, {"name": "b.vxlab.test"}]))

        self.assertEqual(result["domains_without_name"], 0, result)
        self.assertEqual(self._warnings(), [])


class TheJournalGetsOneLinePerCategoryTests(_RestoreBench):
    """`check_all` in `app/api/services.py` settled this: one line for the run, not one per
    row. A restore already writes a "Backup restored" line, and a file with forty retired
    keys in it must not bury it under forty more."""

    def test_five_lost_settings_make_one_line_that_names_all_five(self) -> None:
        lost = ["alpha_key", "beta_key", "gamma_key", "delta_key", "epsilon_key"]
        self._restore(_file(settings=[(k, "1") for k in lost]))

        warnings = self._warnings()
        self.assertEqual(len(warnings), 1, warnings)
        for key in lost:
            self.assertIn(key, warnings[0])

    def test_one_lost_setting_reads_as_one(self) -> None:
        self._restore(_file(settings=[("smtp_host", "mail.example.test")]))

        self.assertIn(
            "1 setting in the file is not accepted by this version, and was dropped",
            self._warnings()[0],
        )

    def test_two_lost_settings_read_as_two(self) -> None:
        """The noun, the verb and the auxiliary all have to move, not just the noun."""
        self._restore(_file(settings=[("smtp_host", "a"), ("notify_on_drift", "b")]))

        self.assertIn(
            "2 settings in the file are not accepted by this version, and were dropped",
            self._warnings()[0],
        )

    def test_one_nameless_domain_reads_as_one(self) -> None:
        self._restore(_file(domains=[{"name": ""}]))

        self.assertIn("1 domain in the file carried no name", self._warnings()[0])

    def test_a_restore_with_nothing_to_report_writes_only_the_line_it_always_wrote(self) -> None:
        self._restore(_file(domains=[{"name": "vxlab.test"}], settings=[("theme", "dark")]))

        self.assertEqual(self._logs(), [("info", "Backup restored (version 1.4.0)")])


class TheAnswerKeepsTheShapeThePanelReadsTests(_RestoreBench):
    def test_the_fields_the_panel_already_reads_are_untouched(self) -> None:
        """`RestoreSection.tsx` reads these four. A field added later must not move them."""
        result = self._restore(
            _file(
                domains=[{"name": "vxlab.test"}],
                services=[
                    {
                        "id": 1,
                        "subdomain": "app",
                        "domain": "vxlab.test",
                        "target_ip": "10.0.0.9",
                        "target_port": 8080,
                    }
                ],
                providers=[],
            )
        )

        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["services"], 1, result)
        self.assertEqual(result["providers"], 0, result)
        self.assertEqual(result["webhooks_needing_url"], 0, result)

    def test_the_answer_carries_exactly_seven_fields(self) -> None:
        """`RestoreResult` in `frontend/src/types/api.ts` declares these seven and no more.

        A field added on this side and not there is one the panel cannot read; a field
        dropped here is one the panel reads as `undefined` and renders as a blank.
        """
        result = self._restore(_file(domains=[{"name": "vxlab.test"}]))

        self.assertEqual(
            sorted(result),
            [
                "domains_without_name",
                "ok",
                "providers",
                "services",
                "settings_not_restored",
                "templates",
                "webhooks_needing_url",
            ],
            result,
        )

    def test_the_two_new_fields_are_a_list_and_a_count(self) -> None:
        """A panel that renders the names needs a list, and a badge needs a number.

        Nothing stops a future edit from returning the count of lost settings instead of
        their names, which would compile, pass every test above that only looks at the
        length, and leave the operator with "2" and no way to find out which two.
        """
        result = self._restore(_file(settings=[("smtp_host", "a"), ("notify_on_drift", "b")]))

        self.assertIsInstance(result["settings_not_restored"], list, result)
        self.assertTrue(all(isinstance(k, str) for k in result["settings_not_restored"]), result)
        self.assertIsInstance(result["domains_without_name"], int, result)


class RestoreDeletesTheHistoryNoFileCarriesTests(_RestoreBench):
    """A restore calls itself a replacement. For two tables it is a plain delete.

    `POST /api/restore` empties the same sixteen tables `POST /api/reset` does, then refills
    them from the file. Four of the sixteen are carried by no export on purpose
    (`_NOT_EXPORTED_ON_PURPOSE` in `backup.py`), so for those four the second half never
    happens. Two of the four are plumbing nobody looks at -- the webhook send queue and the
    scheduler's alert cursor -- and two are screens in the interface: the action log and the
    uptime history.

    The dialog said "This will replace all current data with the backup contents", listed
    seven counts of what was coming in, and said nothing about the two going out. Replace
    implies conservation, and the operator reading it is usually not trying to lose anything:
    a restore is how you roll back a bad change, or move an instance onto a new host.

    So these tests measure it end to end on a live database instead of reading table lists,
    and `test_the_configuration_beside_them_does_come_back` is the control that keeps the
    other three honest -- without it, a restore that simply failed and left the instance
    empty would pass all three.
    """

    def _export(self) -> dict:
        """The file the operator is told to take before anything destructive."""
        get = Request({"type": "http", "method": "GET", "path": "/api/backup", "headers": []})
        return json.loads(backup_api.export_backup(get).body)

    def _count(self, table: str) -> int:
        conn = models.get_db()
        try:
            return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
        finally:
            conn.close()

    def _seed_an_instance_that_has_been_running(self) -> None:
        """Two domains, two services, a few weeks of checks, and a journal somebody can read."""
        conn = models.get_db()
        try:
            for name in ("example.test", "other.test"):
                conn.execute("INSERT INTO domains (name) VALUES (?)", (name,))
            for sub in ("nas", "media"):
                conn.execute(
                    "INSERT INTO services (subdomain, domain, target_ip, target_port) "
                    "VALUES (?, ?, ?, ?)",
                    (sub, "example.test", "10.0.0.9", 443),
                )
            service_id = conn.execute("SELECT id FROM services ORDER BY id").fetchone()["id"]
            for i in range(40):
                conn.execute(
                    "INSERT INTO uptime_events (service_id, status) VALUES (?, ?)",
                    (service_id, "down" if i % 7 == 0 else "up"),
                )
            for i in range(25):
                conn.execute(
                    "INSERT INTO logs (level, message) VALUES ('info', ?)",
                    (f"operator did thing {i}",),
                )
            conn.commit()
        finally:
            conn.close()

    def test_the_archive_carries_neither_the_journal_nor_the_uptime_history(self) -> None:
        """The premise. The file the dialogs point at does not hold either one."""
        self._seed_an_instance_that_has_been_running()

        archive = self._export()

        self.assertGreater(len(archive), 10, "the export came back with nothing to look at")
        self.assertTrue(archive["services"], "the export skipped the services too; wrong subject")
        for table in ("logs", "uptime_events"):
            with self.subTest(table=table):
                self.assertNotIn(table, sorted(archive))

    def test_restoring_that_archive_deletes_both(self) -> None:
        """Export an instance, restore that same file onto it, and its history is gone."""
        self._seed_an_instance_that_has_been_running()
        self.assertEqual(self._count("uptime_events"), 40)
        archive = self._export()

        self._restore(archive)

        self.assertEqual(
            self._count("uptime_events"),
            0,
            "the uptime history survived; the dialogs no longer need to warn about it",
        )
        self.assertEqual(
            [m for _level, m in self._logs() if m.startswith("operator did thing")],
            [],
            "journal lines written before the restore survived it",
        )

    def test_what_is_left_of_the_journal_reads_like_a_quiet_instance(self) -> None:
        """Emptied is not what the screen shows, and that is why the dialog had to say it.

        The restore writes its own account of itself into the table it has just emptied, so
        Settings > Action Logs afterwards is not blank: it holds a line or two, all of them
        about the restore. An operator who never counted the rows beforehand cannot tell that
        apart from an instance where nothing has happened lately.
        """
        self._seed_an_instance_that_has_been_running()
        archive = self._export()

        self._restore(archive)

        remaining = self._logs()
        self.assertLess(
            len(remaining), 5, f"25 lines became {len(remaining)}; this test reads what is left"
        )
        self.assertTrue(
            any("restor" in message.lower() for _level, message in remaining),
            f"the journal says nothing about the restore that emptied it: {remaining}",
        )

    def test_the_configuration_beside_them_does_come_back(self) -> None:
        """The control. The three tests above are worthless if the restore wiped everything.

        The two services and the two domains travel in the same file, through the same wipe,
        and they are back afterwards -- so the two tables that are not back are missing for
        the reason this class is about, and not because the restore failed.
        """
        self._seed_an_instance_that_has_been_running()
        archive = self._export()

        self._restore(archive)

        self.assertEqual(self._count("services"), 2)
        self.assertEqual(self._domains(), ["example.test", "other.test"])

if __name__ == "__main__":
    unittest.main()
