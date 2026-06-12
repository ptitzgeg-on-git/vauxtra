"""Tests for the Prometheus metrics endpoint."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestMetricsEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp()
        cls._db_path = os.path.join(cls._tmpdir, "test.db")
        os.environ["DATA_DIR"] = cls._tmpdir
        os.environ["DB_PATH"] = cls._db_path
        os.environ["SECRET_KEY"] = "test-secret-key-for-metrics"
        os.environ["DISABLE_AUTH"] = "1"

        from app.models import init_db
        init_db()

        from app.main import app
        cls.client = TestClient(app, raise_server_exceptions=True)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.pop("DISABLE_AUTH", None)

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
