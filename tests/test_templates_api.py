"""Integration tests for the service templates API.

Uses FastAPI TestClient against the real app so the SQLite DB path is patched.
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestTemplatesAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Redirect DB to a temp file so tests are isolated
        cls._tmpdir = tempfile.mkdtemp()
        cls._db_path = os.path.join(cls._tmpdir, "test.db")
        os.environ["DATA_DIR"] = cls._tmpdir
        os.environ["DB_PATH"] = cls._db_path
        os.environ["SECRET_KEY"] = "test-secret-key-for-templates"
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

    def setUp(self):
        """Clean templates table before each test."""
        from app.models import get_db
        conn = get_db()
        conn.execute("DELETE FROM service_templates")
        conn.commit()
        conn.close()

    # ── List ─────────────────────────────────────────────────────────────

    def test_list_templates_empty(self):
        r = self.client.get("/api/templates")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_list_templates_returns_created(self):
        self.client.post("/api/templates", json={"name": "Test1", "description": "d1"})
        r = self.client.get("/api/templates")
        self.assertEqual(r.status_code, 200)
        names = [t["name"] for t in r.json()]
        self.assertIn("Test1", names)

    # ── Create ──────────────────────────────────────────────────────────

    def test_create_minimal_template(self):
        r = self.client.post("/api/templates", json={"name": "Minimal"})
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["name"], "Minimal")
        self.assertEqual(data["forward_scheme"], "http")
        self.assertEqual(data["expose_mode"], "proxy_dns")
        self.assertFalse(data["websocket"])
        self.assertIsInstance(data["id"], int)

    def test_create_full_template(self):
        payload = {
            "name": "HTTPS App",
            "description": "Standard HTTPS application",
            "forward_scheme": "https",
            "target_port": 443,
            "websocket": True,
            "expose_mode": "proxy_dns",
            "proxy_provider_id": None,
            "dns_provider_id": None,
            "public_target_mode": "manual",
            "domain": "home.local",
            "dns_ip": "192.168.1.1",
            "tag_ids": [],
        }
        r = self.client.post("/api/templates", json=payload)
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["forward_scheme"], "https")
        self.assertEqual(data["target_port"], 443)
        self.assertTrue(data["websocket"])
        self.assertEqual(data["domain"], "home.local")

    def test_create_duplicate_name_returns_409(self):
        self.client.post("/api/templates", json={"name": "Dup"})
        r = self.client.post("/api/templates", json={"name": "Dup"})
        self.assertEqual(r.status_code, 409)

    def test_create_empty_name_returns_422(self):
        r = self.client.post("/api/templates", json={"name": ""})
        self.assertEqual(r.status_code, 422)

    def test_create_invalid_scheme_returns_422(self):
        r = self.client.post("/api/templates", json={"name": "X", "forward_scheme": "ftp"})
        self.assertEqual(r.status_code, 422)

    def test_create_invalid_port_returns_422(self):
        r = self.client.post("/api/templates", json={"name": "X", "target_port": 99999})
        self.assertEqual(r.status_code, 422)

    # ── Get ─────────────────────────────────────────────────────────────

    def test_get_template_by_id(self):
        r = self.client.post("/api/templates", json={"name": "GetMe", "description": "hello"})
        tid = r.json()["id"]
        r2 = self.client.get(f"/api/templates/{tid}")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["name"], "GetMe")

    def test_get_nonexistent_returns_404(self):
        r = self.client.get("/api/templates/99999")
        self.assertEqual(r.status_code, 404)

    # ── Update ──────────────────────────────────────────────────────────

    def test_update_template(self):
        r = self.client.post("/api/templates", json={"name": "Original"})
        tid = r.json()["id"]
        r2 = self.client.put(
            f"/api/templates/{tid}",
            json={"name": "Updated", "description": "changed", "forward_scheme": "https"},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["name"], "Updated")
        self.assertEqual(r2.json()["forward_scheme"], "https")

    def test_update_nonexistent_returns_404(self):
        r = self.client.put("/api/templates/99999", json={"name": "X"})
        self.assertEqual(r.status_code, 404)

    def test_update_name_conflict_returns_409(self):
        self.client.post("/api/templates", json={"name": "A"})
        r = self.client.post("/api/templates", json={"name": "B"})
        tid = r.json()["id"]
        r2 = self.client.put(f"/api/templates/{tid}", json={"name": "A"})
        self.assertEqual(r2.status_code, 409)

    # ── Delete ──────────────────────────────────────────────────────────

    def test_delete_template(self):
        r = self.client.post("/api/templates", json={"name": "ToDelete"})
        tid = r.json()["id"]
        r2 = self.client.delete(f"/api/templates/{tid}")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["ok"])
        r3 = self.client.get(f"/api/templates/{tid}")
        self.assertEqual(r3.status_code, 404)

    def test_delete_nonexistent_returns_404(self):
        r = self.client.delete("/api/templates/99999")
        self.assertEqual(r.status_code, 404)

    # ── Apply ────────────────────────────────────────────────────────────

    def test_apply_template_returns_defaults(self):
        payload = {
            "name": "Apply Me",
            "forward_scheme": "https",
            "target_port": 8443,
            "websocket": True,
            "domain": "home.local",
        }
        r = self.client.post("/api/templates", json=payload)
        tid = r.json()["id"]
        r2 = self.client.get(f"/api/templates/{tid}/apply")
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertEqual(data["forward_scheme"], "https")
        self.assertEqual(data["target_port"], 8443)
        self.assertTrue(data["websocket"])
        self.assertEqual(data["domain"], "home.local")
        self.assertEqual(data["_template_id"], tid)
        self.assertEqual(data["_template_name"], "Apply Me")

    def test_apply_nonexistent_returns_404(self):
        r = self.client.get("/api/templates/99999/apply")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
