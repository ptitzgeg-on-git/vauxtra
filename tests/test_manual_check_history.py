"""A manual health check must leave the same trace a scheduled one leaves.

Only `scheduler.py` inserted into `uptime_events`. The two manual endpoints wrote
`services.status` and `services.last_checked` and nothing else, so /monitoring could print
"no check in the last 24 hours" in the 24 h column of a row whose status cell, one column
to the left, read "OK, checked just now". Pressing the button harder never helped: the
history the column reads from was never written.

Both endpoints now write that row. `check-all`, named in the same finding, also returns
the latency it was already measuring and throwing away, and logs once for the run instead
of leaving no trace at all in "Recent activity".
"""

import hashlib
import json
import os
import socket
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models

_REAL_GETADDRINFO = socket.getaddrinfo


def _offline_getaddrinfo(host, *args, **kwargs):
    """Loopback resolves for real, every name fails: the suite must not touch a resolver.

    `_check_one` resolves the public hostname of the service, and `socket.create_connection`
    resolves its target through the same function, so this cannot simply raise for
    everything: the connection whose latency the test reads goes through here too.
    """
    if host in ("127.0.0.1", "localhost", "", None):
        return _REAL_GETADDRINFO(host, *args, **kwargs)
    raise socket.gaierror(socket.EAI_NONAME, "offline test")


def _free_port() -> int:
    """A port nothing listens on, so a connection to it is refused rather than timing out."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ManualCheckHistoryTests(unittest.TestCase):
    HEADERS = {"Authorization": "Bearer key-rw"}

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "checks.test.db")
        models.init_db()

        # A real listener, so "ok" means a connection actually completed and the latency
        # reported is a measurement rather than a constant.
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.live_port = self._listener.getsockname()[1]
        self.dead_port = _free_port()

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            patch.object(auth, "APP_PASSWORD", "test-password"),
            patch.object(socket, "getaddrinfo", _offline_getaddrinfo),
        ]
        for p in self._patchers:
            p.start()

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

        conn = models.get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                   prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                   created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
        )
        conn.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
            ("rw", hashlib.sha256(b"key-rw").hexdigest(), "rw", "write"),
        )
        conn.executemany(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port,
                                     expose_mode, tunnel_hostname, enabled)
               VALUES (?,?,?,?,?,?,?,1)""",
            [
                (1, "live", "example.com", "127.0.0.1", self.live_port, "proxy_dns", ""),
                (2, "dead", "example.com", "127.0.0.1", self.dead_port, "proxy_dns", ""),
                (3, "tunnel", "example.com", "127.0.0.1", self.live_port, "tunnel",
                 "tunnel.example.com"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        self._listener.close()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _history(self) -> list:
        conn = models.get_db()
        rows = conn.execute(
            "SELECT service_id, status FROM uptime_events ORDER BY service_id, id"
        ).fetchall()
        conn.close()
        return [(row["service_id"], row["status"]) for row in rows]

    def _logs(self) -> list:
        conn = models.get_db()
        rows = conn.execute("SELECT level, message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [(row["level"], row["message"]) for row in rows]

    # -- one service --------------------------------------------------------------------

    def test_a_single_check_writes_the_history_row_the_24h_column_reads(self):
        resp = self.client.post("/api/services/1/check", headers=self.HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "ok")
        self.assertIsInstance(resp.json()["latency_ms"], float)
        self.assertEqual(self._history(), [(1, "ok")])

    def test_a_failed_check_is_recorded_as_well(self):
        """An outage found by hand is still an outage the graph has to show."""
        resp = self.client.post("/api/services/2/check", headers=self.HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "error")
        self.assertIsNone(resp.json()["latency_ms"])
        self.assertEqual(self._history(), [(2, "error")])

    def test_the_row_a_manual_check_writes_is_what_the_history_route_returns(self):
        self.client.post("/api/services/1/check", headers=self.HEADERS)
        resp = self.client.get("/api/services/history", headers=self.HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([event["status"] for event in resp.json()["1"]], ["ok"])

    def test_the_deprecated_get_alias_records_the_same_row(self):
        resp = self.client.get("/api/services/1/check", headers=self.HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._history(), [(1, "ok")])

    # -- the whole fleet ----------------------------------------------------------------

    def test_the_fleet_check_writes_one_row_per_probed_service(self):
        resp = self.client.post("/api/services/check-all", headers=self.HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._history(), [(1, "ok"), (2, "error")])

    def test_a_tunnel_service_is_neither_probed_nor_recorded(self):
        """Its target is the live port, so a row for id 3 would mean it had been probed."""
        body = self.client.post("/api/services/check-all", headers=self.HEADERS).json()
        self.assertNotIn(3, [entry["id"] for entry in body["results"]])
        self.assertNotIn(3, [service_id for service_id, _ in self._history()])
        self.assertEqual((body["ok"], body["error"]), (1, 1))

    def test_the_fleet_check_reports_the_latency_it_measured(self):
        body = self.client.post("/api/services/check-all", headers=self.HEADERS).json()
        measured = {entry["id"]: entry for entry in body["results"]}

        self.assertEqual(measured[1]["status"], "ok")
        self.assertIsInstance(measured[1]["latency_ms"], float)
        self.assertGreaterEqual(measured[1]["latency_ms"], 0)

        # Nothing was measured on a refused connection, and a zero would read as "instant".
        self.assertEqual(measured[2]["status"], "error")
        self.assertIsNone(measured[2]["latency_ms"])

    def test_the_fleet_check_logs_once_for_the_run_not_once_per_service(self):
        self.client.post("/api/services/check-all", headers=self.HEADERS)
        logs = self._logs()
        self.assertEqual(len(logs), 1, logs)
        self.assertEqual(logs[0][0], "error")  # one service was down during the run
        self.assertIn("2 services", logs[0][1])
        self.assertIn("1 ok", logs[0][1])
        self.assertIn("1 error", logs[0][1])

    def test_every_result_carries_the_three_fields_the_client_reads(self):
        """The front end folds `results` straight into its latency store, keyed by id."""
        body = self.client.post("/api/services/check-all", headers=self.HEADERS).json()
        for entry in json.loads(json.dumps(body["results"])):
            self.assertEqual(sorted(entry.keys()), ["id", "latency_ms", "status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
