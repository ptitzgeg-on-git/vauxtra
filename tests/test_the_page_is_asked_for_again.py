"""The page that names the bundles is asked for again, never reused from the browser's cache.

index.html names the hashed bundles of the build it belongs to, under a name that never
changes. Served with a Last-Modified and no Cache-Control, a browser may reuse it without
asking for a fraction of its age (heuristic freshness). Measured in production on
2026-09-23: after an upgrade, a page load came out of the browser's cache with the previous
build's bundle, while /api/health answered the new version. The screen showed a defect the
running version had fixed.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models


class ThePageIsAskedForAgainTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        os.makedirs(os.path.join(root, "assets"))
        for name, body in (
            ("index.html", "<!doctype html><script src=/assets/index-abc123.js></script>"),
            ("favicon.svg", "<svg/>"),
            (os.path.join("assets", "index-abc123.js"), ""),
        ):
            with open(os.path.join(root, name), "w", encoding="utf-8") as f:
                f.write(body)
        for p in (
            patch.object(app_main, "frontend_dist", root),
            patch.object(app_main, "_FRONTEND_ROOT", root),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _serve(self, path: str):
        return asyncio.run(app_main.serve_frontend(path))

    def test_every_file_of_this_route_is_asked_for_again(self) -> None:
        for path in ("", "providers", "services/12", "index.html", "favicon.svg"):
            with self.subTest(path=path):
                response = self._serve(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_a_route_of_the_application_is_the_page_itself(self) -> None:
        self.assertEqual(os.path.basename(self._serve("providers").path), "index.html")

    def test_the_header_reaches_the_browser(self) -> None:
        """Through the whole application, its middleware included."""
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)
        self.addCleanup(self._restore, orig)
        models.DATA_DIR = db.DATA_DIR = data.name
        models.DB_PATH = db.DB_PATH = os.path.join(data.name, "page.test.db")
        models.init_db()
        with (
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            TestClient(app_main.app) as client,
        ):
            response = client.get("/providers")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/assets/index-abc123.js", response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    @staticmethod
    def _restore(orig) -> None:
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = orig


if __name__ == "__main__":
    unittest.main()
