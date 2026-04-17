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


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = models.DB_PATH
        self._orig_data_dir = models.DATA_DIR
        self._orig_db_db_path = db.DB_PATH
        self._orig_db_data_dir = db.DATA_DIR

        models.DATA_DIR = self._tmpdir.name
        models.DB_PATH = os.path.join(self._tmpdir.name, "dns_manager.http.test.db")
        db.DATA_DIR = self._tmpdir.name
        db.DB_PATH = models.DB_PATH
        models.init_db()

        self._auth_patch = patch.object(auth, "APP_PASSWORD", "")
        self._start_patch = patch.object(scheduler, "start", lambda interval_minutes=0: None)
        self._configure_patch = patch.object(scheduler, "configure", lambda interval_minutes=0: None)

        self._auth_patch.start()
        self._start_patch.start()
        self._configure_patch.start()

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

        self._configure_patch.stop()
        self._start_patch.stop()
        self._auth_patch.stop()

        models.DB_PATH = self._orig_db_path
        models.DATA_DIR = self._orig_data_dir
        db.DB_PATH = self._orig_db_db_path
        db.DATA_DIR = self._orig_db_data_dir
        self._tmpdir.cleanup()

    def test_health_endpoint(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertIn("latency_ms", body)
        self.assertIn("db", body)

    def test_spa_routes_do_not_redirect_to_login(self) -> None:
        for path in ["/", "/services", "/providers", "/monitoring", "/settings"]:
            resp = self.client.get(path, follow_redirects=False)
            self.assertIn(resp.status_code, (200, 404))
            self.assertNotEqual(resp.status_code, 302)
            if resp.status_code == 200:
                self.assertIn("<div id=\"root\"></div>", resp.text)

    def test_settings_and_domains_roundtrip(self) -> None:
        save_settings = self.client.post(
            "/api/settings",
            json={"check_interval": "5"},
        )
        self.assertEqual(save_settings.status_code, 200)
        self.assertTrue(save_settings.json().get("ok"))

        settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200)
        payload = settings.json()
        self.assertEqual(payload.get("check_interval"), "5")

        create_domain = self.client.post("/api/domains", json={"name": "Example.COM"})
        self.assertEqual(create_domain.status_code, 201)
        self.assertEqual(create_domain.json().get("name"), "example.com")

        list_domains = self.client.get("/api/domains")
        self.assertEqual(list_domains.status_code, 200)
        self.assertIn("example.com", list_domains.json())

        delete_domain = self.client.delete("/api/domains/example.com")
        self.assertEqual(delete_domain.status_code, 200)
        self.assertTrue(delete_domain.json().get("ok"))

    def test_provider_types_endpoint(self) -> None:
        resp = self.client.get("/api/providers/types")
        self.assertEqual(resp.status_code, 200)

        types_map = resp.json()
        self.assertIn("npm", types_map)
        self.assertIn("adguard", types_map)
        self.assertIn("traefik", types_map)
        self.assertIn("available", types_map["traefik"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
