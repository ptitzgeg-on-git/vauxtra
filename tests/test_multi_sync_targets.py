"""A second DNS server, or a second proxy, has to actually receive the service.

Vauxtra lets a service name extra targets beside the two provider columns -- a second DNS
server, a second proxy -- and records them in `service_push_targets`. The push route and the
drift check have always read that table. The two routes that write a service never did: they
called the primary proxy and the primary DNS provider, wrote the rows, and answered.

Measured on the integration lab before the fix, against real AdGuard and Technitium
containers: creating a service with `extra_dns_provider_ids: [technitium]` answered
`201 {"errors": []}`, AdGuard held the record, Technitium held nothing, and the drift check
on that same service answered `missing_dns_rewrite` one call later. Adding the second server
through `PUT` behaved the same way. The record only appeared on the next scheduler cycle, or
on an explicit push nobody had a reason to make.

The mirror case is the removal. Dropping a target from the list, renaming the service, or
deleting the provider outright all left the record live on a provider Vauxtra stopped
addressing -- and, for the deletion, stopped being able to see at all. The 409 that asks
before deleting a provider said those services "stop being pushed anywhere", which is false
for a multi-sync service: the other targets keep publishing it. What actually happens is the
orphan, and that is what the operator now reads.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import providers as providers_api
from app.api import services as services_api
from app.api import sync as sync_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _FakeProvider:
    """A provider that keeps its hosts and rewrites in memory and records every call.

    Real enough for the push and the withdrawal to run end to end: what it holds after the
    route returns is the assertion, not the order of the calls.
    """

    def __init__(self, name: str, calls: list, *, ok: bool = True):
        self.name = name
        self.calls = calls
        self.ok = ok
        self.hosts: list[dict] = []
        self.rewrites: list[dict] = []
        self._next_id = 100

    # -- proxy ------------------------------------------------------------------------
    def find_best_certificate(self, _domain):
        return None

    def list_hosts(self):
        return self.hosts

    def create_host(self, domain, ip, port, scheme, websocket, cert_id):
        self.calls.append((self.name, "create_host", domain))
        if not self.ok:
            return None
        self._next_id += 1
        host = {"id": self._next_id, "domains": [domain], "host": ip, "port": port}
        self.hosts.append(host)
        return host

    def update_host(self, host_id, domain, ip, port, scheme, websocket, cert_id):
        self.calls.append((self.name, "update_host", host_id, domain))
        if not self.ok:
            return False
        for host in self.hosts:
            if host["id"] == host_id or domain in host["domains"]:
                host["domains"] = [domain]
                host["host"], host["port"] = ip, port
                return True
        return False

    def delete_host(self, host_id):
        self.calls.append((self.name, "delete_host", host_id))
        if not self.ok:
            return False
        before = len(self.hosts)
        self.hosts = [h for h in self.hosts if h["id"] != host_id and host_id not in h["domains"]]
        return len(self.hosts) < before

    def toggle_host(self, host_id, enabled):
        self.calls.append((self.name, "toggle_host", host_id, enabled))
        return True

    # -- dns --------------------------------------------------------------------------
    def list_rewrites(self):
        return self.rewrites

    def add_rewrite(self, domain, ip):
        self.calls.append((self.name, "add_rewrite", domain, ip))
        if not self.ok:
            return False
        self.rewrites.append({"domain": domain, "answer": ip})
        return True

    def delete_rewrite(self, domain, ip):
        self.calls.append((self.name, "delete_rewrite", domain, ip))
        if not self.ok:
            return False
        before = len(self.rewrites)
        self.rewrites = [r for r in self.rewrites if r["domain"] != domain]
        return len(self.rewrites) < before

    def update_rewrite(self, old_domain, old_ip, domain, ip):
        self.calls.append((self.name, "update_rewrite", old_domain, domain))
        self.rewrites = [r for r in self.rewrites if r["domain"] != old_domain]
        self.rewrites.append({"domain": domain, "answer": ip})
        return True

    # -- reading back -----------------------------------------------------------------
    def holds(self, domain: str) -> bool:
        return any(r["domain"] == domain for r in self.rewrites) or any(
            domain in h["domains"] for h in self.hosts
        )


class _MultiSyncTestCase(unittest.TestCase):
    IP = "198.51.100.7"

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "multisync.test.db")
        models.init_db()

        self.calls: list = []
        self.providers: dict[int, _FakeProvider] = {}

        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(providers_api, "require_auth_or_setup", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", self._provider_for),
            patch.object(sync_api, "create_provider", self._provider_for),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        import app.db as _app_db

        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures -------------------------------------------------------------------------
    def _provider_for(self, row):
        return self.providers[row["id"]]

    def _add_provider(self, pid: int, name: str, ptype: str, *, ok: bool = True) -> _FakeProvider:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?,?,?,'http://x','u','p','{}',1)""",
            (pid, name, ptype),
        )
        conn.commit()
        conn.close()
        self.providers[pid] = _FakeProvider(name, self.calls, ok=ok)
        return self.providers[pid]

    def _body(self, **overrides) -> services_api.ServiceIn:
        fields = {
            "subdomain": "vault",
            "domain": "example.com",
            "target_ip": "10.0.0.9",
            "target_port": 8080,
            "forward_scheme": "http",
            "dns_ip": self.IP,
        }
        fields.update(overrides)
        return services_api.ServiceIn(**fields)

    def _create(self, **overrides):
        response = services_api.add_service(_request(), self._body(**overrides))
        import json

        return response.status_code, json.loads(bytes(response.body))

    def _push_targets(self, sid: int) -> set[int]:
        conn = models.get_db()
        rows = {
            r["provider_id"]
            for r in conn.execute(
                "SELECT provider_id FROM service_push_targets WHERE service_id=?", (sid,)
            )
        }
        conn.close()
        return rows

    def _messages(self) -> list[str]:
        conn = models.get_db()
        rows = [r[0] for r in conn.execute("SELECT message FROM logs ORDER BY id")]
        conn.close()
        return rows


class CreationReachesEveryTargetTests(_MultiSyncTestCase):
    def test_the_second_dns_server_receives_the_record(self):
        self._add_provider(1, "NPM", "npm")
        primary = self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium")

        status, body = self._create(
            proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3]
        )

        self.assertEqual(status, 201)
        self.assertEqual(body["errors"], [])
        self.assertTrue(primary.holds("vault.example.com"))
        self.assertTrue(second.holds("vault.example.com"), "the extra DNS target received nothing")

    def test_the_second_proxy_receives_the_route(self):
        primary = self._add_provider(1, "NPM", "npm")
        second = self._add_provider(2, "NPM bis", "npm")
        self._add_provider(3, "AdGuard", "adguard")

        status, _ = self._create(
            proxy_provider_id=1, dns_provider_id=3, extra_proxy_provider_ids=[2]
        )

        self.assertEqual(status, 201)
        self.assertTrue(primary.holds("vault.example.com"))
        self.assertTrue(second.holds("vault.example.com"), "the extra proxy target received nothing")

    def test_a_disabled_service_is_published_nowhere(self):
        """The primary push is withheld for a disabled service; the extras follow."""
        self._add_provider(1, "NPM", "npm")
        primary = self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium")

        self._create(
            proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3], enabled=False
        )

        self.assertFalse(primary.holds("vault.example.com"))
        self.assertFalse(second.holds("vault.example.com"))

    def test_a_refusal_on_the_extra_target_is_reported(self):
        """Silence was the whole defect: the answer has to carry what did not happen."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium", ok=False)

        status, body = self._create(
            proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3]
        )

        self.assertEqual(status, 207)
        self.assertTrue(body["errors"], "a target that refused the record answered 201 errors: []")
        self.assertTrue(any("Technitium" in e for e in body["errors"]))
        self.assertFalse(second.holds("vault.example.com"))

    def test_the_primary_is_not_pushed_twice(self):
        """The extra pass must not redo the work the route just did."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")

        self._create(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3])

        adguard_writes = [c for c in self.calls if c[0] == "AdGuard" and c[1] == "add_rewrite"]
        self.assertEqual(len(adguard_writes), 1, self.calls)


class EditingReachesEveryTargetTests(_MultiSyncTestCase):
    def _setup_service(self) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        return body["id"]

    def test_adding_a_second_dns_server_publishes_on_it(self):
        sid = self._setup_service()
        second = self.providers[3]

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2,
                                            extra_dns_provider_ids=[3])
        )

        self.assertEqual(self._push_targets(sid), {3})
        self.assertTrue(second.holds("vault.example.com"), "the added DNS target received nothing")

    def test_dropping_a_target_takes_its_record_down(self):
        sid = self._setup_service()
        second = self.providers[3]
        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2,
                                            extra_dns_provider_ids=[3])
        )
        self.assertTrue(second.holds("vault.example.com"))

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2)
        )

        self.assertEqual(self._push_targets(sid), set())
        self.assertFalse(
            second.holds("vault.example.com"),
            "a target dropped from the list kept serving the hostname",
        )

    def test_renaming_moves_the_record_on_the_extra_target(self):
        sid = self._setup_service()
        second = self.providers[3]
        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2,
                                            extra_dns_provider_ids=[3])
        )

        services_api.update_service(
            sid, _request("PUT"), self._body(subdomain="coffre", proxy_provider_id=1,
                                             dns_provider_id=2, extra_dns_provider_ids=[3])
        )

        self.assertTrue(second.holds("coffre.example.com"))
        self.assertFalse(
            second.holds("vault.example.com"),
            "the old hostname stayed published on the extra target beside the new one",
        )

    def test_an_untouched_edit_does_not_rewrite_anything(self):
        """A plain edit must not churn the extra target's record for nothing."""
        sid = self._setup_service()
        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2,
                                            extra_dns_provider_ids=[3])
        )
        self.calls.clear()

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2,
                                            extra_dns_provider_ids=[3])
        )

        self.assertEqual(
            [c for c in self.calls if c[0] == "Technitium" and c[1] != "list_rewrites"],
            [],
            self.calls,
        )


class RemovingAProviderTellsTheTruthTests(_MultiSyncTestCase):
    """The 409 that asks before deleting a provider, and what `?force=true` really leaves."""

    def _multi_sync_service(self) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        _, body = self._create(
            proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3]
        )
        return body["id"]

    def _conflict(self, pid: int):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as raised:
            providers_api.delete_provider(pid, _request("DELETE"))
        self.assertEqual(raised.exception.status_code, 409)
        return raised.exception.detail

    def test_a_multi_sync_service_is_not_described_as_going_dark(self):
        self._multi_sync_service()

        detail = self._conflict(3)

        self.assertNotIn("stop being pushed anywhere", detail["message"])
        self.assertIn("go on being published by their other targets", detail["message"])
        self.assertTrue(detail["services"][0]["still_published"])

    def test_a_service_left_with_nothing_still_says_so(self):
        """The original sentence was right for this case, and has to survive."""
        self._add_provider(2, "AdGuard", "adguard")
        self._create(dns_provider_id=2)

        detail = self._conflict(2)

        self.assertIn("stop being published anywhere", detail["message"])
        self.assertFalse(detail["services"][0]["still_published"])

    def test_the_message_names_what_stays_live_on_the_provider(self):
        self._multi_sync_service()

        detail = self._conflict(3)

        self.assertIn("stays live on it", detail["message"])
        self.assertIn("withdraw=true", detail["message"])

    def test_withdraw_takes_the_record_off_the_removed_provider(self):
        self._multi_sync_service()
        removed = self.providers[3]
        kept = self.providers[2]

        result = providers_api.delete_provider(3, _request("DELETE"), force=True, withdraw=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["withdrawn"])
        self.assertFalse(
            removed.holds("vault.example.com"),
            "the record survived on a provider Vauxtra can no longer see",
        )
        self.assertTrue(kept.holds("vault.example.com"), "the other target must be left alone")

    def test_force_alone_leaves_the_record_and_the_log_says_so(self):
        """Deliberate: the operator can keep the records. The journal is then the only trace."""
        self._multi_sync_service()
        removed = self.providers[3]

        result = providers_api.delete_provider(3, _request("DELETE"), force=True)

        self.assertFalse(result["withdrawn"])
        self.assertTrue(removed.holds("vault.example.com"))
        self.assertTrue(
            any("still served by it" in m and "vault.example.com" in m for m in self._messages()),
            self._messages(),
        )

    def test_a_withdrawal_that_fails_is_reported_and_the_provider_still_goes(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._create(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[])
        self._add_provider(3, "Technitium", "technitium")
        conn = models.get_db()
        conn.execute(
            "INSERT INTO service_push_targets (service_id, provider_id, role) VALUES (1,3,'dns')"
        )
        conn.commit()
        conn.close()
        self.providers[3].rewrites.append({"domain": "vault.example.com", "answer": self.IP})
        self.providers[3].ok = False

        result = providers_api.delete_provider(3, _request("DELETE"), force=True, withdraw=True)

        self.assertFalse(result["ok"])
        self.assertTrue(any("vault.example.com" in e for e in result["errors"]))
        conn = models.get_db()
        self.assertIsNone(conn.execute("SELECT 1 FROM providers WHERE id=3").fetchone())
        conn.close()


if __name__ == "__main__":
    unittest.main()
