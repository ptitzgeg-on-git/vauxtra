import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models, scheduler
import app.auth as auth
from app.api import backup as backup_api
from app.api.backup import RestoreRequest
from app.api import settings as settings_api


def _request(method: str = "GET", path: str = "/") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
    }
    return Request(scope)


class IsolatedDBTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = models.DB_PATH
        self._orig_data_dir = models.DATA_DIR

        import app.db as _app_db
        self._orig_db_db_path = _app_db.DB_PATH
        self._orig_db_data_dir = _app_db.DATA_DIR

        test_db_path = os.path.join(self._tmpdir.name, "dns_manager.test.db")
        models.DATA_DIR = self._tmpdir.name
        models.DB_PATH = test_db_path
        _app_db.DATA_DIR = self._tmpdir.name
        _app_db.DB_PATH = test_db_path
        models.init_db()

        # Make tests independent from host environment auth configuration.
        self._auth_patch = patch.object(auth, "APP_PASSWORD", "")
        self._auth_patch.start()

        scheduler._alert_down_since.clear()
        scheduler._alert_down_sent.clear()
        scheduler._tunnel_last_status.clear()

    def tearDown(self) -> None:
        scheduler._alert_down_since.clear()
        scheduler._alert_down_sent.clear()
        scheduler._tunnel_last_status.clear()

        self._auth_patch.stop()

        import app.db as _app_db
        models.DB_PATH = self._orig_db_path
        models.DATA_DIR = self._orig_data_dir
        _app_db.DB_PATH = self._orig_db_db_path
        _app_db.DATA_DIR = self._orig_db_data_dir
        self._tmpdir.cleanup()

    def _count(self, table: str) -> int:
        conn = models.get_db()
        value = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return value


class BackupRestoreRegressionTests(IsolatedDBTestCase):
    def test_restore_and_export_keep_domains_and_icon_url(self) -> None:
        payload = {
            "version": "5",
            "providers": [
                {
                    "id": 7,
                    "name": "NPM Main",
                    "type": "npm",
                    "url": "http://npm.local:81",
                    "username": "",
                    "password": "",
                    "extra": "{}",
                    "enabled": 1,
                }
            ],
            "services": [
                {
                    "id": 101,
                    "subdomain": "app",
                    "domain": "example.com",
                    "target_ip": "127.0.0.1",
                    "target_port": 8080,
                    "forward_scheme": "http",
                    "websocket": 0,
                    "dns_provider_id": None,
                    "proxy_provider_id": None,
                    "dns_ip": "",
                    "npm_host_id": None,
                    "enabled": 1,
                    "status": "unknown",
                    "last_checked": None,
                    "created_at": "2026-01-01 00:00:00",
                    "icon_url": "https://icons.local/app.png",
                }
            ],
            "tags": [],
            "service_tags": [],
            "service_push_targets": [
                {
                    "service_id": 101,
                    "provider_id": 7,
                    "role": "proxy",
                }
            ],
            "environments": [],
            "service_environments": [],
            "domains": [
                {"name": "example.com"},
                {"name": "internal.local"},
            ],
            "webhooks": [],
            "service_alerts": [],
            "settings": [],
        }

        result = backup_api.import_backup(_request("POST", "/api/restore"), RestoreRequest(backup=payload))
        self.assertTrue(result["ok"])

        conn = models.get_db()
        icon = conn.execute("SELECT icon_url FROM services WHERE id=101").fetchone()[0]
        push_target = conn.execute(
            "SELECT provider_id, role FROM service_push_targets WHERE service_id=101"
        ).fetchone()
        domains = [
            r["name"]
            for r in conn.execute("SELECT name FROM domains ORDER BY name").fetchall()
        ]
        conn.close()

        self.assertEqual(icon, "https://icons.local/app.png")
        self.assertIsNotNone(push_target)
        self.assertEqual(push_target["provider_id"], 7)
        self.assertEqual(push_target["role"], "proxy")
        self.assertEqual(domains, ["example.com", "internal.local"])

        exported = backup_api.export_backup(_request("GET", "/api/backup"))
        data = json.loads(exported.body.decode("utf-8"))

        self.assertEqual(data["version"], "7")
        self.assertEqual(
            sorted(item["name"] for item in data["domains"]),
            ["example.com", "internal.local"],
        )
        exported_service = next(item for item in data["services"] if item["id"] == 101)
        self.assertEqual(exported_service["icon_url"], "https://icons.local/app.png")
        exported_targets = [
            item for item in data.get("service_push_targets", [])
            if item.get("service_id") == 101
        ]
        self.assertEqual(len(exported_targets), 1)
        self.assertEqual(exported_targets[0].get("provider_id"), 7)


class ResetRegressionTests(IsolatedDBTestCase):
    def test_reset_clears_all_operational_tables(self) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO providers (id, name, type, url) VALUES (1, 'NPM', 'npm', 'http://npm.local')"
        )
        conn.execute(
            """INSERT INTO services
               (id, subdomain, domain, target_ip, target_port, proxy_provider_id, enabled, status)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 80, 1, 1, 'ok')"""
        )
        conn.execute("INSERT INTO tags (id, name, color) VALUES (1, 'prod', 'red')")
        conn.execute("INSERT INTO service_tags (service_id, tag_id) VALUES (1, 1)")
        conn.execute("INSERT INTO service_push_targets (service_id, provider_id, role) VALUES (1, 1, 'proxy')")
        conn.execute("INSERT INTO environments (id, name, color) VALUES (1, 'production', 'green')")
        conn.execute(
            "INSERT INTO service_environments (service_id, environment_id) VALUES (1, 1)"
        )
        conn.execute(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'Ops', 'mailto://ops@example.com', 1)"
        )
        conn.execute(
            """INSERT INTO service_alerts
               (service_id, webhook_id, on_up, on_down, min_down_minutes)
               VALUES (1, 1, 1, 1, 0)"""
        )
        conn.execute("INSERT INTO uptime_events (service_id, status) VALUES (1, 'ok')")
        conn.execute("INSERT INTO domains (name) VALUES ('example.com')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('check_interval', '1')")
        conn.execute("INSERT INTO logs (level, message) VALUES ('info', 'seed log')")
        conn.commit()
        conn.close()

        result = settings_api.reset_all(_request("POST", "/api/reset"))
        self.assertTrue(result["ok"])

        for table in [
            "service_alerts",
            "service_tags",
            "service_push_targets",
            "service_environments",
            "uptime_events",
            "services",
            "webhooks",
            "providers",
            "tags",
            "environments",
            "domains",
            "logs",
            "settings",
        ]:
            self.assertEqual(self._count(table), 0, f"Table should be empty after reset: {table}")


class MigrationRegressionTests(IsolatedDBTestCase):
    def test_init_db_adds_providers_extra_column_for_legacy_schema(self) -> None:
        conn = models.get_db()
        conn.execute("DROP TABLE IF EXISTS providers")
        conn.execute(
            """
            CREATE TABLE providers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                type        TEXT    NOT NULL,
                url         TEXT    NOT NULL,
                username    TEXT    NOT NULL DEFAULT '',
                password    TEXT    NOT NULL DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO providers (name, type, url, username, password, enabled) VALUES (?,?,?,?,?,?)",
            ("Legacy", "npm", "http://npm.local", "", "", 1),
        )
        conn.commit()
        conn.close()

        models.init_db()

        conn = models.get_db()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(providers)").fetchall()}
        row = conn.execute("SELECT extra FROM providers WHERE name='Legacy'").fetchone()
        conn.close()

        self.assertIn("extra", columns)
        self.assertIsNotNone(row)
        self.assertEqual(row["extra"], "{}")


class SchedulerRegressionTests(IsolatedDBTestCase):
    def test_service_alert_threshold_and_recovery_are_one_shot(self) -> None:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO services
               (id, subdomain, domain, target_ip, target_port, enabled, status)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 80, 1, 'error')"""
        )
        conn.execute(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'Ops', 'mailto://ops@example.com', 1)"
        )
        conn.execute(
            """INSERT INTO service_alerts
               (service_id, webhook_id, on_up, on_down, min_down_minutes)
               VALUES (1, 1, 1, 1, 1)"""
        )
        conn.commit()
        conn.close()

        sent_messages = []

        class FakeApprise:
            def __init__(self):
                self.url = ""

            def add(self, url: str) -> bool:
                self.url = url
                return True

            def notify(self, title: str, body: str) -> bool:
                sent_messages.append({"url": self.url, "title": title, "body": body})
                return True

        fake_apprise_module = types.SimpleNamespace(Apprise=FakeApprise)

        with patch.dict(sys.modules, {"apprise": fake_apprise_module}):
            with patch.object(scheduler.time, "monotonic", return_value=0.0):
                scheduler._fire_service_webhooks()
            self.assertEqual(len(sent_messages), 0)

            with patch.object(scheduler.time, "monotonic", return_value=61.0):
                scheduler._fire_service_webhooks()
            self.assertEqual(len(sent_messages), 1)
            self.assertIn("DOWN: app.example.com", sent_messages[-1]["body"])

            with patch.object(scheduler.time, "monotonic", return_value=120.0):
                scheduler._fire_service_webhooks()
            self.assertEqual(len(sent_messages), 1)

            conn = models.get_db()
            conn.execute("UPDATE services SET status='ok' WHERE id=1")
            conn.commit()
            conn.close()

            with patch.object(scheduler.time, "monotonic", return_value=130.0):
                scheduler._fire_service_webhooks()
            self.assertEqual(len(sent_messages), 2)
            self.assertIn("RECOVERED: app.example.com", sent_messages[-1]["body"])

            with patch.object(scheduler.time, "monotonic", return_value=140.0):
                scheduler._fire_service_webhooks()
            self.assertEqual(len(sent_messages), 2)

    def test_tunnel_health_change_triggers_single_transition_event(self) -> None:
        conn = models.get_db()
        conn.execute(
            """
            INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
            VALUES (1, 'Tunnel Main', 'cloudflare_tunnel', 'https://api.cloudflare.com/client/v4', 'acc', 'token', '{"tunnel_id":"abc"}', 1)
            """
        )
        conn.commit()

        state = {"status": "healthy"}

        class _FakeTunnelProvider:
            def health_status(self):
                status = state["status"]
                return {"ok": status == "healthy", "status": status}

        with patch.object(scheduler, "create_provider", lambda _row: _FakeTunnelProvider()):
            first = scheduler._run_tunnel_health_checks(conn)
            self.assertEqual(first, [])

            state["status"] = "down"
            second = scheduler._run_tunnel_health_checks(conn)
            self.assertEqual(len(second), 1)
            self.assertEqual(second[0]["old"], "ok")
            self.assertEqual(second[0]["new"], "error")

            third = scheduler._run_tunnel_health_checks(conn)
            self.assertEqual(third, [])

        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)