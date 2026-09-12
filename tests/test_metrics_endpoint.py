"""Tests for the Prometheus metrics endpoint.

These only read, so pointing at the wrong database cost nothing here beyond counting the
caller's rows instead of a known set. It was still the wrong database: `DATA_DIR` and
`DB_PATH` were set in `os.environ`, and `app/config.py` builds both from its own location
without ever reading the environment. Rebinding the module attributes is what the rest of
this suite does and what actually redirects the connection.
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestMetricsEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import app.db as _app_db
        from app import models

        cls._tmpdir = tempfile.mkdtemp()
        cls._db_path = os.path.join(cls._tmpdir, "test.db")

        cls._orig = (models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH)
        models.DATA_DIR = _app_db.DATA_DIR = cls._tmpdir
        models.DB_PATH = _app_db.DB_PATH = cls._db_path

        # See the note in `test_templates_api.py`: read when `app.config` is first imported.
        os.environ["SECRET_KEY"] = "test-secret-key-for-metrics"

        models.init_db()

        from app.main import app
        cls.client = TestClient(app, raise_server_exceptions=True)

    @classmethod
    def tearDownClass(cls):
        import shutil

        import app.db as _app_db
        from app import models

        models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH = cls._orig
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_metrics_endpoint_returns_200(self):
        r = self.client.get("/metrics")
        self.assertEqual(r.status_code, 200)

    def test_metrics_content_type_is_text(self):
        r = self.client.get("/metrics")
        self.assertIn("text/plain", r.headers.get("content-type", ""))

    def test_metrics_contains_service_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_services_total", r.text)

    def test_metrics_contains_provider_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_providers_total", r.text)

    def test_metrics_contains_log_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_logs_24h", r.text)

    def test_metrics_contains_template_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_templates_total", r.text)

    def test_metrics_contains_schema_version(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_schema_version", r.text)

    def test_metrics_format_valid_prometheus_lines(self):
        r = self.client.get("/metrics")
        for line in r.text.splitlines():
            if not line or line.startswith("#"):
                continue
            # Each data line must have a numeric value as last token
            parts = line.rsplit(" ", 1)
            self.assertEqual(len(parts), 2, f"Invalid line: {line!r}")
            try:
                float(parts[1])
            except ValueError:
                self.fail(f"Non-numeric value in metrics line: {line!r}")

    def test_metrics_status_labels(self):
        r = self.client.get("/metrics")
        text = r.text
        self.assertIn('status="ok"', text)
        self.assertIn('status="error"', text)
        self.assertIn('status="unknown"', text)

    def test_metrics_enabled_state_labels(self):
        r = self.client.get("/metrics")
        text = r.text
        self.assertIn('state="enabled"', text)
        self.assertIn('state="disabled"', text)

    def test_metrics_webhook_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_webhooks_total", r.text)

    def test_metrics_no_auth_required(self):
        """Prometheus scrape path must be accessible without auth."""
        from fastapi.testclient import TestClient

        from app.main import app
        # Remove auth header entirely
        with TestClient(app) as plain_client:
            r = plain_client.get("/metrics")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
