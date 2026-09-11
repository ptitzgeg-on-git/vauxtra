"""The health cycle must never hold a write transaction across a network call.

SQLite admits one writer at a time. `run_health_checks()` used to open a write
transaction on its first UPDATE and keep it open through every slow phase of the cycle:
a TCP probe per service at three seconds each, a DNS provider's API, an HTTPS fetch of
every certificate, up to twenty webhook POSTs. Anything else trying to write -- an
operator saving a service, the API disabling a host -- waited out `busy_timeout` (15 s)
and then failed with "database is locked". `_sync_npm_statuses` had grown a branch that
silently drops exactly that message, which is how long this had been happening in the
open.

Each test below installs a *witness* in the middle of a slow phase: a second connection,
with a deliberately short busy timeout, that tries one small write and records whether
it got in. A witness that is refused is the bug, reproduced.
"""

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app import models, scheduler

# Short on purpose. The point is to fail fast when the database is locked, not to sit
# through the fifteen seconds a real caller would have waited before failing anyway.
WITNESS_TIMEOUT_SECONDS = 0.3


def _witness_can_write(label: str, log: list) -> None:
    """Try one small write from a second connection and record what happened."""
    conn = sqlite3.connect(models.DB_PATH, timeout=WITNESS_TIMEOUT_SECONDS)
    try:
        conn.execute("INSERT INTO logs (level, message) VALUES ('info', ?)", (label,))
        conn.commit()
        log.append((label, None))
    except Exception as exc:  # noqa: BLE001 - the message is the whole assertion
        log.append((label, str(exc)))
    finally:
        conn.close()


def _refused(log: list) -> list:
    return [(label, err) for label, err in log if err is not None]


class WriteLockTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "writelock.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        scheduler._provider_last_status.clear()
        scheduler._dns_update_failures.clear()
        scheduler._cert_alert_state.clear()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        scheduler._provider_last_status.clear()
        scheduler._dns_update_failures.clear()
        scheduler._cert_alert_state.clear()
        self._tmpdir.cleanup()

    def _seed_services(self, count: int) -> None:
        conn = models.get_db()
        for i in range(1, count + 1):
            conn.execute(
                """INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled, status)
                   VALUES (?, ?, 'example.com', '127.0.0.1', ?, 1, 'ok')""",
                (i, f"app{i}", 9000 + i),
            )
        conn.commit()
        conn.close()

    def _seed_providers(self, count: int) -> None:
        conn = models.get_db()
        for i in range(1, count + 1):
            conn.execute(
                """INSERT INTO providers (id, name, type, url, username, password, enabled)
                   VALUES (?, ?, 'npm', 'http://127.0.0.1:81', 'u', 'p', 1)""",
                (i, f"proxy{i}"),
            )
        conn.commit()
        conn.close()


class TestProbesDoNotHoldTheLock(WriteLockTestCase):

    def test_every_probe_runs_against_a_writable_database(self):
        """Probing is the slowest phase, and it must own nothing while it runs.

        A TCP probe against a host that is gone costs the full three-second timeout. Run
        between two UPDATEs on one open transaction, three of them held the write lock
        for nine seconds -- long enough to push a concurrent save past its own timeout.
        """
        self._seed_services(3)
        seen: list = []

        def probe(ip, port):
            _witness_can_write(f"probe:{port}", seen)
            return "error"

        with patch.object(scheduler, "_tcp_ok", side_effect=probe), \
             patch.object(scheduler, "_fire_global_webhook"), \
             patch.object(scheduler, "_fire_service_webhooks"):
            scheduler.run_health_checks()

        self.assertEqual(len(seen), 3, seen)
        self.assertEqual(_refused(seen), [], "a probe ran with the write lock held")

    def test_the_round_is_still_recorded(self):
        """Shortening the transaction must not lose what it was there to save."""
        self._seed_services(2)

        with patch.object(scheduler, "_tcp_ok", return_value="error"), \
             patch.object(scheduler, "_fire_global_webhook"), \
             patch.object(scheduler, "_fire_service_webhooks"):
            scheduler.run_health_checks()

        conn = models.get_db()
        statuses = [r["status"] for r in conn.execute("SELECT status FROM services ORDER BY id")]
        events = conn.execute("SELECT COUNT(*) AS n FROM uptime_events").fetchone()["n"]
        conn.close()
        self.assertEqual(statuses, ["error", "error"])
        self.assertEqual(events, 2)

    def test_a_tunnel_service_is_still_skipped(self):
        """Tunnels are checked through their provider's API; TCP against them always fails."""
        conn = models.get_db()
        conn.execute(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled, status, expose_mode)
               VALUES (1, 'tun', 'example.com', 'x.cfargotunnel.com', 443, 1, 'ok', 'tunnel')"""
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "_tcp_ok", return_value="error") as probe, \
             patch.object(scheduler, "_fire_global_webhook"), \
             patch.object(scheduler, "_fire_service_webhooks"):
            scheduler.run_health_checks()

        probe.assert_not_called()
        conn = models.get_db()
        status = conn.execute("SELECT status FROM services WHERE id=1").fetchone()["status"]
        events = conn.execute("SELECT COUNT(*) AS n FROM uptime_events").fetchone()["n"]
        conn.close()
        self.assertEqual(status, "ok")
        self.assertEqual(events, 0)


class TestProviderHealthDoesNotHoldTheLock(WriteLockTestCase):

    def test_every_provider_is_reached_with_nothing_open(self):
        """Each provider is an HTTP round trip to somebody else's box."""
        self._seed_providers(3)
        seen: list = []

        class Witness:
            def __init__(self, name):
                self.name = name

            def health_status(self):
                _witness_can_write(f"provider:{self.name}", seen)
                return {"ok": False, "status": "down"}

        conn = models.get_db()
        with patch.object(scheduler, "create_provider", side_effect=lambda row: Witness(row["name"])):
            # Twice: the first round seeds "unknown" and writes nothing, the second one
            # logs a transition per provider and so actually opens a transaction.
            scheduler._run_provider_health_checks(conn)
            conn.commit()
            scheduler._provider_last_status.update({1: "ok", 2: "ok", 3: "ok"})
            scheduler._run_provider_health_checks(conn)
            conn.commit()
        conn.close()

        self.assertEqual(len(seen), 6, seen)
        self.assertEqual(_refused(seen), [], "a provider was polled with the write lock held")


class TestCertScanDoesNotHoldTheLock(WriteLockTestCase):

    def test_every_certificate_list_is_fetched_with_nothing_open(self):
        self._seed_providers(3)
        seen: list = []

        class Witness:
            def __init__(self, name):
                self.name = name

            def get_certificates(self):
                _witness_can_write(f"cert:{self.name}", seen)
                # Expires tomorrow: guarantees an alert, hence a write, on every provider.
                return [{"id": 1, "nice_name": self.name, "expires_on": "2000-01-02T00:00:00"}]

        conn = models.get_db()
        with patch.object(scheduler, "certificate_provider_types", return_value=["npm"]), \
             patch.object(scheduler, "create_provider", side_effect=lambda row: Witness(row["name"])):
            scheduler._run_cert_expiry_alerts(conn)
            conn.commit()
        conn.close()

        self.assertEqual(len(seen), 3, seen)
        self.assertEqual(_refused(seen), [], "a certificate list was fetched with the write lock held")


class TestWebhookRetryDoesNotHoldTheLock(WriteLockTestCase):

    def _queue(self, count: int) -> None:
        conn = models.get_db()
        for i in range(1, count + 1):
            conn.execute(
                """INSERT INTO webhook_delivery_log
                   (id, webhook_id, url, title, body, status, attempt, next_retry_at)
                   VALUES (?, NULL, ?, 't', 'b', 'pending', 1, '2000-01-01 00:00:00')""",
                (i, f"mailto://ops{i}@example.com"),
            )
        conn.commit()
        conn.close()

    def test_every_post_leaves_and_is_committed_on_its_own(self):
        """Twenty POSTs can run in one cycle; one transaction around the lot is a lock.

        Committing per row is also what keeps the outcomes: under a single transaction a
        process that went down on row nineteen re-sent the eighteen before it.
        """
        self._queue(3)
        seen: list = []

        class FakeApprise:
            def __init__(self):
                self.url = ""

            def add(self, url):
                self.url = url
                return True

            def notify(self, title, body):
                _witness_can_write(f"webhook:{self.url}", seen)
                return True

        conn = models.get_db()
        with patch.dict(sys.modules, {"apprise": types.SimpleNamespace(Apprise=FakeApprise)}):
            scheduler._run_webhook_retry(conn)
            conn.commit()
        conn.close()

        self.assertEqual(len(seen), 3, seen)
        self.assertEqual(_refused(seen), [], "a webhook was posted with the write lock held")

        conn = models.get_db()
        statuses = {
            r["id"]: r["status"]
            for r in conn.execute("SELECT id, status FROM webhook_delivery_log")
        }
        conn.close()
        self.assertEqual(statuses, {1: "delivered", 2: "delivered", 3: "delivered"})


class TestNpmSyncDoesNotHoldTheLock(WriteLockTestCase):

    def test_one_listing_per_proxy_not_one_per_service(self):
        """Ten services behind one NPM asked it for the same list ten times a cycle."""
        from app.api.services import _sync_npm_statuses

        self._seed_providers(1)
        conn = models.get_db()
        for i in range(1, 4):
            conn.execute(
                """INSERT INTO services
                   (id, subdomain, domain, target_ip, target_port, enabled, expose_mode,
                    npm_host_id, proxy_provider_id)
                   VALUES (?, ?, 'example.com', '127.0.0.1', 80, 1, 'proxy_dns', ?, 1)""",
                (i, f"app{i}", i),
            )
        conn.commit()

        seen: list = []
        calls = {"n": 0}

        class Witness:
            def list_hosts(self):
                calls["n"] += 1
                _witness_can_write(f"npm:{calls['n']}", seen)
                # Every host disabled: forces an UPDATE + a log line per service.
                return [{"id": i, "enabled": False} for i in range(1, 4)]

        with patch("app.api.services.create_provider", side_effect=lambda row: Witness()):
            _sync_npm_statuses(conn)
            conn.commit()
        conn.close()

        self.assertEqual(calls["n"], 1, "the host list was fetched once per service")
        self.assertEqual(_refused(seen), [], "NPM was polled with the write lock held")

        conn = models.get_db()
        enabled = [r["enabled"] for r in conn.execute("SELECT enabled FROM services ORDER BY id")]
        conn.close()
        self.assertEqual(enabled, [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
