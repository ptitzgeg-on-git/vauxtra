import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import models
import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler


class HttpAuthIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = models.DB_PATH
        self._orig_data_dir = models.DATA_DIR
        self._orig_db_db_path = db.DB_PATH
        self._orig_db_data_dir = db.DATA_DIR

        models.DATA_DIR = self._tmpdir.name
        models.DB_PATH = os.path.join(self._tmpdir.name, "dns_manager.http.auth.test.db")
        db.DATA_DIR = self._tmpdir.name
        db.DB_PATH = models.DB_PATH
        models.init_db()

        self._start_patch = patch.object(scheduler, "start", lambda interval_minutes=0: None)
        self._configure_patch = patch.object(scheduler, "configure", lambda interval_minutes=0: None)

        self._start_patch.start()
        self._configure_patch.start()

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

        self._configure_patch.stop()
        self._start_patch.stop()

        models.DB_PATH = self._orig_db_path
        models.DATA_DIR = self._orig_data_dir
        db.DB_PATH = self._orig_db_db_path
        db.DATA_DIR = self._orig_db_data_dir
        self._tmpdir.cleanup()

    def test_api_requires_auth_when_password_is_set(self) -> None:
        with patch.object(auth, "APP_PASSWORD", "test-password"):
            resp = self.client.get("/api/settings")

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json().get("detail"), "Unauthorized")

    def test_api_is_open_when_password_is_disabled(self) -> None:
        with patch.object(auth, "APP_PASSWORD", ""):
            resp = self.client.get("/api/settings")

        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), dict)

    def test_frontend_routes_are_not_redirected_to_legacy_login(self) -> None:
        with patch.object(auth, "APP_PASSWORD", "test-password"):
            resp = self.client.get("/dashboard", follow_redirects=False)

        self.assertNotEqual(resp.status_code, 302)
        self.assertNotIn("/login", (resp.headers.get("location") or ""))

    def test_legacy_login_endpoint_is_absent(self) -> None:
        with patch.object(auth, "APP_PASSWORD", "test-password"):
            resp = self.client.post(
                "/login",
                data={"password": "wrong-password", "next": "/"},
                follow_redirects=False,
            )

        self.assertIn(resp.status_code, (404, 405))


if __name__ == "__main__":
    unittest.main(verbosity=2)