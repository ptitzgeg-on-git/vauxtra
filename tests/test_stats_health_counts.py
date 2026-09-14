"""`GET /api/stats` counts health the way the rest of the product defines it.

`services_ok` and `services_error` used to count every row in `services`, enabled or not.
Nothing else in the product answers that question that way:

  * `serviceStatus()` (frontend/src/components/features/monitoring/uptime.ts) opens with
    `if (!service.enabled) return 'disabled'` -- a state of its own, in neither bucket. It is
    what the Monitoring page filters its ok/error/unknown chips on.
  * The Dashboard fallback counts `enabledServices.filter(...)`, and the triage list beneath
    it ("N endpoints are in error") is read off that same enabled-only list.

The counters mattered because they win: the tile is written `stats?.services_ok ??
servicesOk`, so the figure an operator reads comes from this route whenever it answers, and
from the enabled-only list only when it does not. The same tile showed two different numbers
for the same estate depending on which.

The column makes it permanent rather than transient. The scheduler reads
`WHERE enabled=1`, and the only writer of the enabled flag is
`UPDATE services SET enabled=? WHERE id=?`, which never touches `status`. A service disabled
while failing keeps `status` 'error' for as long as it exists: the tile stayed red, with a
count, pointing at a fault nobody was checking and nobody could clear -- while the triage
list directly below it, counting the same estate the other way, showed nothing to do.
"""

import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import settings as settings_api

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class StatsHealthCountsTests(unittest.TestCase):
    """The 2x2 of enabled against status, measured through the real route."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        path = os.path.join(self._tmpdir.name, "stats.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = path
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _seed(self, rows) -> None:
        conn = models.get_db()
        for subdomain, enabled, status in rows:
            conn.execute(
                "INSERT INTO services (subdomain, domain, target_ip, target_port, enabled, status) "
                "VALUES (?,?,?,?,?,?)",
                (subdomain, "example.test", "10.0.0.1", 80, enabled, status),
            )
        conn.commit()
        conn.close()

    def _stats(self) -> dict:
        with patch.object(settings_api, "require_auth", lambda _req, scope=None: None):
            return settings_api.get_stats(_request("GET", "/api/stats"))

    def test_disabled_services_are_neither_ok_nor_error(self) -> None:
        self._seed(
            [
                ("a", 1, "ok"),
                ("b", 1, "error"),
                ("c", 0, "ok"),       # disabled, frozen at ok
                ("d", 0, "error"),    # disabled, frozen at error
                ("e", 1, "unknown"),  # enabled, never checked
            ]
        )
        stats = self._stats()
        self.assertEqual(stats["services"], 5, "the total is the size of the estate, all of it")
        self.assertEqual(stats["services_ok"], 1, "only the enabled ok row is a measurement")
        self.assertEqual(stats["services_error"], 1, "the disabled failure is not a live fault")

    def test_health_never_outruns_what_is_watched(self) -> None:
        """ok + error cannot exceed the enabled count the tile prints beside them.

        The Dashboard draws `services_ok` and `services_error` next to a hint reading
        "{count} enabled", counted off the services list. With every row counted, four
        disabled services made a card that read 2 enabled, 2 ok, 2 error.
        """
        rows = [(f"s{i}", 0, "error") for i in range(4)] + [("live", 1, "ok")]
        self._seed(rows)
        stats = self._stats()
        conn = models.get_db()
        enabled = conn.execute("SELECT COUNT(*) FROM services WHERE enabled=1").fetchone()[0]
        conn.close()
        self.assertLessEqual(
            stats["services_ok"] + stats["services_error"],
            enabled,
            "a health count larger than the number of watched services is impossible",
        )

    def test_an_estate_of_only_disabled_services_reports_nothing_failing(self) -> None:
        self._seed([("x", 0, "error"), ("y", 0, "error")])
        stats = self._stats()
        self.assertEqual(stats["services_error"], 0)
        self.assertEqual(stats["services_ok"], 0)


class OneDefinitionOfHealthTests(unittest.TestCase):
    """Read the other writers as text, so a second definition of the rule is a red test.

    Each of these reads a file this route has to agree with. They are deliberately literal:
    the point is not that the string exists but that the rule still lives in exactly one
    place per surface, and that moving it has to come past this file.
    """

    def test_the_route_scopes_both_health_counters_to_enabled(self) -> None:
        src = (REPO_ROOT / "app" / "api" / "settings.py").read_text(encoding="utf-8")
        for counter in ("services_ok", "services_error"):
            line = next(
                ln for ln in src.splitlines() if ln.strip().startswith('"' + counter + '"')
            )
            self.assertIn(
                "enabled=1",
                line,
                counter + " counts every row again; see the module docstring of this file",
            )

    def test_the_scheduler_still_checks_only_enabled_services(self) -> None:
        """If it ever checked disabled ones, their status would stop being frozen."""
        src = (REPO_ROOT / "app" / "scheduler.py").read_text(encoding="utf-8")
        self.assertIn("FROM services WHERE enabled=1", src)

    def test_disabling_a_service_still_leaves_its_status_untouched(self) -> None:
        """The other half of why a frozen error lasts forever."""
        src = (REPO_ROOT / "app" / "api" / "services.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            if "UPDATE services SET enabled" in line:
                self.assertNotIn("status", line, "this would change the premise above")

    def test_the_panel_calls_a_disabled_service_neither_ok_nor_error(self) -> None:
        src = (
            REPO_ROOT / "frontend" / "src" / "components" / "features" / "monitoring" / "uptime.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("if (!service.enabled) return 'disabled'", src)

    def test_the_dashboard_fallback_counts_enabled_services_only(self) -> None:
        src = (REPO_ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
        self.assertIn("const servicesOk = enabledServices.filter", src)
        self.assertIn("const servicesInError = enabledServices.filter", src)
        # And that the route answer is what the tile prefers, which is why it has to match.
        self.assertIn("stats?.services_ok ?? servicesOk", src)
        self.assertIn("stats?.services_error ?? servicesInError", src)


if __name__ == "__main__":
    unittest.main()
