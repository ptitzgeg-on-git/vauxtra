"""Deleting a service has to take its whole public exposure down with it.

Two defects, both silent. The delete walked `proxy_provider_id` and `dns_provider_id` and
stopped there: the extra targets in `service_push_targets` -- the second proxy, the second
DNS server, the ones every push writes to -- kept their routes. The hostname went on
resolving and the proxy went on forwarding, with nothing left in Vauxtra pointing at
either. The bulk delete had the same gap.

And the log purge matched `'%service {id}%'`, which is a prefix match on a decimal number:
deleting service 1 also erased the monitoring history of services 10 to 19 and 100 to 199.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import services as services_api
from app.api import sync as sync_api


def _request(method: str = "DELETE", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _RecordingProvider:
    """Records every call instead of talking to anything.

    `hosts` and `rewrites` are what this provider claims to hold, so a test can make a
    lookup succeed, fail, or return an address that has drifted from the stored one.
    """

    def __init__(self, name: str, calls: list, hosts=None, rewrites=None, ok=True):
        self._name = name
        self._calls = calls
        self._hosts = hosts if hosts is not None else []
        self._rewrites = rewrites if rewrites is not None else []
        self._ok = ok

    def list_hosts(self):
        return self._hosts

    def list_rewrites(self):
        return self._rewrites

    def delete_host(self, host_id):
        self._calls.append((self._name, "delete_host", host_id))
        return self._ok

    def delete_rewrite(self, domain, ip):
        self._calls.append((self._name, "delete_rewrite", domain, ip))
        return self._ok

    def add_rewrite(self, domain, ip):
        self._calls.append((self._name, "add_rewrite", domain, ip))
        return True

    def toggle_host(self, host_id, enabled):
        self._calls.append((self._name, "toggle_host", host_id, enabled))
        return True

    def find_best_certificate(self, _domain):
        return None


class _DeleteTestCase(unittest.TestCase):
    """One service on `vault.example.com`, published in more places than the two columns."""

    HOST = "vault.example.com"

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "delete.test.db")
        models.init_db()

        self.calls: list = []
        self.providers: dict[int, _RecordingProvider] = {}

        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            # The withdrawal runs inside `app.api.sync`, so that is where the factory has
            # to be intercepted; patching it on `services` alone would let real HTTP out.
            patch.object(sync_api, "create_provider", self._provider_for),
            patch.object(services_api, "create_provider", self._provider_for),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        import app.db as _app_db

        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures ---------------------------------------------------------------------

    def _provider_for(self, row):
        return self.providers[row["id"]]

    def _add_provider(self, pid, name, ptype, *, enabled=1, hosts=None, rewrites=None, ok=True):
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?,?,?,'http://x','u','p','{}',?)""",
            (pid, name, ptype, enabled),
        )
        conn.commit()
        conn.close()
        self.providers[pid] = _RecordingProvider(name, self.calls, hosts, rewrites, ok)

    def _add_service(self, **overrides) -> int:
        fields = {
            "subdomain": "vault",
            "domain": "example.com",
            "target_ip": "10.0.0.9",
            "target_port": 8080,
            "forward_scheme": "http",
            "websocket": 0,
            "enabled": 1,
            "expose_mode": "proxy_dns",
            "proxy_provider_id": None,
            "tunnel_provider_id": None,
            "tunnel_hostname": "",
            "npm_host_id": None,
            "dns_provider_id": None,
            "dns_ip": "",
            "public_target_mode": "manual",
        }
        fields.update(overrides)
        columns = ",".join(fields)
        marks = ",".join(["?"] * len(fields))
        conn = models.get_db()
        cur = conn.execute(f"INSERT INTO services ({columns}) VALUES ({marks})", list(fields.values()))
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def _add_push_target(self, sid: int, provider_id: int, role: str) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO service_push_targets (service_id, provider_id, role) VALUES (?,?,?)",
            (sid, provider_id, role),
        )
        conn.commit()
        conn.close()

    def _log(self, message: str) -> None:
        conn = models.get_db()
        conn.execute("INSERT INTO logs (level, message) VALUES ('info', ?)", (message,))
        conn.commit()
        conn.close()

    def _messages(self) -> list[str]:
        conn = models.get_db()
        rows = [r[0] for r in conn.execute("SELECT message FROM logs ORDER BY id")]
        conn.close()
        return rows

    def _service_exists(self, sid: int) -> bool:
        conn = models.get_db()
        row = conn.execute("SELECT 1 FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return row is not None


class ExtraTargetsAreWithdrawnTests(_DeleteTestCase):
    def test_the_second_proxy_does_not_keep_serving_the_deleted_service(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "NPM bis", "npm", hosts=[{"id": 99, "domains": [self.HOST]}])
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42)
        self._add_push_target(sid, 2, "proxy")

        result = services_api.delete_service(sid, _request())

        self.assertIn(("NPM", "delete_host", 42), self.calls)
        self.assertIn(("NPM bis", "delete_host", 99), self.calls)
        self.assertEqual(result["errors"], [])

    def test_the_second_dns_server_does_not_keep_resolving_it(self):
        self._add_provider(1, "AdGuard", "adguard")
        self._add_provider(2, "Technitium", "technitium")
        sid = self._add_service(dns_provider_id=1, dns_ip="198.51.100.7")
        self._add_push_target(sid, 2, "dns")

        services_api.delete_service(sid, _request())

        self.assertIn(("AdGuard", "delete_rewrite", self.HOST, "198.51.100.7"), self.calls)
        self.assertIn(("Technitium", "delete_rewrite", self.HOST, "198.51.100.7"), self.calls)

    def test_a_provider_disabled_in_vauxtra_is_still_asked_to_withdraw(self):
        """Turning a provider off inside Vauxtra does not take its routes off the internet."""
        self._add_provider(1, "NPM", "npm", enabled=0)
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42)

        services_api.delete_service(sid, _request())

        self.assertIn(("NPM", "delete_host", 42), self.calls)

    def test_a_read_only_provider_is_left_alone(self):
        """Nothing was ever pushed to Traefik, so there is nothing to take back."""
        self._add_provider(1, "Traefik", "traefik")
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42)
        self._add_push_target(sid, 1, "proxy")

        services_api.delete_service(sid, _request())

        self.assertEqual(self.calls, [])

    def test_the_route_is_found_by_hostname_when_no_id_was_stored(self):
        """`npm_host_id` is only ever filled for the primary proxy, and not always."""
        self._add_provider(1, "NPM", "npm", hosts=[{"id": 7, "domains": [self.HOST]}])
        sid = self._add_service(proxy_provider_id=1, npm_host_id=None)

        services_api.delete_service(sid, _request())

        self.assertIn(("NPM", "delete_host", 7), self.calls)

    def test_a_provider_holding_nothing_is_not_called(self):
        self._add_provider(1, "NPM", "npm", hosts=[{"id": 7, "domains": ["other.example.com"]}])
        sid = self._add_service(proxy_provider_id=1, npm_host_id=None)

        services_api.delete_service(sid, _request())

        self.assertEqual(self.calls, [])

    def test_the_record_actually_present_is_the_one_deleted(self):
        """The stored `dns_ip` can have drifted; the record on the server is what must go."""
        self._add_provider(
            1, "AdGuard", "adguard", rewrites=[{"domain": self.HOST, "ip": "203.0.113.9"}]
        )
        sid = self._add_service(dns_provider_id=1, dns_ip="198.51.100.7")

        services_api.delete_service(sid, _request())

        self.assertIn(("AdGuard", "delete_rewrite", self.HOST, "203.0.113.9"), self.calls)

    def test_a_tunnel_route_is_addressed_by_its_hostname(self):
        self._add_provider(1, "CF Tunnel", "cloudflare_tunnel")
        sid = self._add_service(
            expose_mode="tunnel", tunnel_provider_id=1, tunnel_hostname="vault.example.com"
        )

        services_api.delete_service(sid, _request())

        self.assertIn(("CF Tunnel", "delete_host", self.HOST), self.calls)

    def test_a_proxy_route_left_over_from_before_a_switch_to_tunnel_is_withdrawn(self):
        """Switching to tunnel mode keeps `proxy_provider_id`, and the old host is live."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "CF Tunnel", "cloudflare_tunnel")
        sid = self._add_service(
            expose_mode="tunnel",
            tunnel_provider_id=2,
            tunnel_hostname="vault.example.com",
            proxy_provider_id=1,
            npm_host_id=42,
        )

        services_api.delete_service(sid, _request())

        self.assertIn(("NPM", "delete_host", 42), self.calls)
        self.assertIn(("CF Tunnel", "delete_host", self.HOST), self.calls)

    def test_a_refusal_is_reported_and_the_service_still_goes(self):
        self._add_provider(1, "NPM", "npm", ok=False)
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42)

        result = services_api.delete_service(sid, _request())

        self.assertEqual(result["errors"], ["Failed to delete proxy host on NPM"])
        self.assertTrue(result["ok"])
        self.assertFalse(self._service_exists(sid))

    def test_an_unreachable_provider_is_reported_by_name(self):
        self._add_provider(1, "NPM", "npm")
        self.providers[1].delete_host = lambda _h: (_ for _ in ()).throw(RuntimeError("connection refused"))
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42)

        result = services_api.delete_service(sid, _request())

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("NPM", result["errors"][0])
        self.assertIn("connection refused", result["errors"][0])


class LogPurgeStaysInsideTheServiceTests(_DeleteTestCase):
    def test_deleting_service_1_keeps_the_history_of_services_10_and_100(self):
        sid = self._add_service()  # id 1 on a fresh database
        self.assertEqual(sid, 1)
        self._log("Synced service 1 status from NPM: up")
        self._log("Synced service 10 status from NPM: up")
        self._log("Synced service 100 status from NPM: up")
        self._log("Failed to sync NPM status for service 19: boom")

        services_api.delete_service(sid, _request())

        remaining = [m for m in self._messages() if m.startswith(("Synced", "Failed"))]
        self.assertEqual(
            remaining,
            [
                "Synced service 10 status from NPM: up",
                "Synced service 100 status from NPM: up",
                "Failed to sync NPM status for service 19: boom",
            ],
        )

    def test_the_two_message_shapes_the_scheduler_writes_are_purged(self):
        sid = self._add_service()
        self._log(f"Synced service {sid} status from NPM: up")
        self._log(f"Failed to sync NPM status for service {sid}: boom")
        self._log(f"a message ending on the id: service {sid}")

        services_api.delete_service(sid, _request())

        self.assertNotIn("service 1", " ".join(self._messages()))

    def test_the_purge_ignores_the_case_the_message_was_written_in(self):
        sid = self._add_service()
        self._log(f"Service {sid} went down")

        services_api.delete_service(sid, _request())

        self.assertNotIn("went down", " ".join(self._messages()))


class BulkDeleteMatchesTheSingleDeleteTests(_DeleteTestCase):
    def _bulk_delete(self, ids):
        body = services_api._BulkActionBody(ids=ids, action="delete")
        return services_api.bulk_action(body, _request("POST"))

    def test_it_withdraws_the_extra_targets_too(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "Technitium", "technitium")
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42, dns_ip="198.51.100.7")
        self._add_push_target(sid, 2, "dns")

        result = self._bulk_delete([sid])

        self.assertIn(("NPM", "delete_host", 42), self.calls)
        self.assertIn(("Technitium", "delete_rewrite", self.HOST, "198.51.100.7"), self.calls)
        self.assertEqual(result["errors"], [])
        self.assertFalse(self._service_exists(sid))

    def test_it_purges_the_logs_it_used_to_leave_behind(self):
        sid = self._add_service()
        self._log(f"Synced service {sid} status from NPM: up")
        self._log("Synced service 10 status from NPM: up")

        self._bulk_delete([sid])

        remaining = [m for m in self._messages() if m.startswith("Synced")]
        self.assertEqual(remaining, ["Synced service 10 status from NPM: up"])

    def test_a_failure_names_the_service_it_belongs_to(self):
        self._add_provider(1, "NPM", "npm", ok=False)
        sid = self._add_service(proxy_provider_id=1, npm_host_id=42)

        result = self._bulk_delete([sid])

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn(self.HOST, result["errors"][0])
        self.assertIn("NPM", result["errors"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
