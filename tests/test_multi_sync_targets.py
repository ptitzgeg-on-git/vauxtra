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

And once the push reached every target, the preflight was left promising for one. It read
the extra ids only to answer "is at least one target set", so a second DNS server that was
dead, disabled, or simply gone read as six green checks naming the primary alone; the save
that followed pushed to it for real and came back 207 with a refusal the panel had had every
chance to foresee. Worse for the id that no longer exists: `_unknown_references` turns that
one into a 400, so the preflight said "0 blocking" about a body the API refuses outright.
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
from app.providers.base import ProxyProvider


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _FakeProvider:
    """A provider that keeps its hosts and rewrites in memory and records every call.

    Real enough for the push and the withdrawal to run end to end: what it holds after the
    route returns is the assertion, not the order of the calls.
    """

    def __init__(self, name: str, calls: list, *, ok: bool = True, reachable: bool = True):
        self.name = name
        self.calls = calls
        self.ok = ok
        self.reachable = reachable
        self.hosts: list[dict] = []
        self.rewrites: list[dict] = []
        self._next_id = 100

    # -- preflight --------------------------------------------------------------------
    def test_connection(self) -> bool:
        """The probe, kept apart from `ok`: answering the door and accepting a record are
        two different permissions, and a read-only token passes the first and fails the
        second."""
        return self.reachable

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
        # `enabled` is in the shape because NPM puts it there: a suspended host stays in the
        # listing and only that flag says it has stopped answering. Without it the fake could
        # not tell "suspended" from "serving", which is the whole question a disabled service
        # asks of its proxy.
        host = {"id": self._next_id, "domains": [domain], "host": ip, "port": port, "enabled": True}
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
        for host in self.hosts:
            if host["id"] == host_id or host_id in host["domains"]:
                host["enabled"] = enabled
                return True
        return False  # nothing under that id: NPM answers 404, which reads as False

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

    def serves(self, domain: str) -> bool:
        """Still answering for the name, which is not the same as still holding a row.

        A suspended proxy host is held and not served. `holds` is the right question for a
        deletion, this one for a service that was switched off.
        """
        return any(r["domain"] == domain for r in self.rewrites) or any(
            domain in h["domains"] and h.get("enabled", True) for h in self.hosts
        )



class _ProviderWithoutSuspension(_FakeProvider):
    """A proxy that never implemented the toggle, which most of them never did.

    `toggle_host` exists on three providers; everywhere else the base's `return False` is
    what answers, so the suspension can only fail and the route has to be deleted instead.
    Taking that method verbatim is what makes this a fake of such a provider rather than a
    fake of a provider that refuses one particular host.
    """

    toggle_host = ProxyProvider.toggle_host


class _ProviderRefusingTheToggle(_FakeProvider):
    """A proxy that implements the suspension and refused this particular call.

    NPM answers a non-200 to `/enable` on an expired token, Zoraxy a failure on its own
    toggle endpoint, and both come back as the same `False` a provider with no suspension
    at all returns. The difference between the two is the class, never the wire.
    """

    def toggle_host(self, host_id, enabled):
        self.calls.append((self.name, "toggle_host", host_id, enabled))
        return False


class _ProviderEchoingMixedCase(_FakeProvider):
    """A provider that hands back the hostname in the case it was typed, as Zoraxy does.

    Only the listings are affected: what it holds stays spelled the way the route wrote it,
    so `holds` and `serves` go on asking the question they ask everywhere else, and the test
    is about the reading back -- which is the half Vauxtra got wrong.
    """

    @staticmethod
    def _as_typed(name: str) -> str:
        return ".".join(part.capitalize() for part in str(name).split("."))

    def list_hosts(self):
        return [dict(h, domains=[self._as_typed(d) for d in h["domains"]]) for h in self.hosts]

    def list_rewrites(self):
        return [dict(r, domain=self._as_typed(r["domain"])) for r in self.rewrites]


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
            # The push, drift and reconcile routes live in `sync`, and they check their own
            # scope. Only the two write routes were patched, because only they were called.
            patch.object(sync_api, "require_auth", lambda _req, scope=None: None),
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

    def _add_provider(
        self,
        pid: int,
        name: str,
        ptype: str,
        *,
        ok: bool = True,
        reachable: bool = True,
        enabled: bool = True,
    ) -> _FakeProvider:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?,?,?,'http://x','u','p','{}',?)""",
            (pid, name, ptype, int(enabled)),
        )
        conn.commit()
        conn.close()
        self.providers[pid] = _FakeProvider(name, self.calls, ok=ok, reachable=reachable)
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

    def _preflight(self, **overrides) -> dict:
        """The preflight on the same body `_create` would send.

        The socket to the target is answered here so the run stays offline: what these tests
        weigh is the providers the checks name, not a backend nobody started.
        """
        body = services_api.ServicePreflightIn(
            **self._body(**overrides).model_dump(), service_id=None
        )
        with patch.object(
            services_api,
            "_service_target_reachable",
            lambda _host, _port, timeout=2.0: (True, "Reachable in 3.0 ms"),
        ):
            return services_api.preflight_service(_request("POST", "/preflight"), body)

    def _named(self, result: dict, name: str) -> list[dict]:
        return [c for c in result["checks"] if c["name"] == name]

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


class PreflightLooksAtEveryTargetTests(_MultiSyncTestCase):
    """Each verdict here is measured against what the save route then does with the same body.

    That is the only thing that makes a preflight worth reading: `blocking` where the API
    answers 400, a warning where it publishes and reports the refusal afterwards.
    """

    def test_a_dead_extra_dns_target_is_named(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium", reachable=False)

        result = self._preflight(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3])

        checks = self._named(result, "extra_dns_provider")
        self.assertEqual(len(checks), 1, [c["name"] for c in result["checks"]])
        self.assertFalse(checks[0]["ok"])
        self.assertEqual(checks[0]["detail_key"], "extra_provider_failed")
        self.assertEqual(
            checks[0]["detail_params"]["name"],
            "Technitium",
            "a line that says one target is dead without saying which names nothing",
        )
        self.assertEqual(result["summary"]["warnings"], 1)
        self.assertEqual(result["summary"]["blocking_failures"], 0)
        self.assertTrue(result["ok"], "a secondary that is down must not lock the route away")

    def test_an_extra_target_that_answers_raises_nothing(self):
        """The witness. A check that failed on every extra would satisfy the test above."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")

        result = self._preflight(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3])

        checks = self._named(result, "extra_dns_provider")
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0]["ok"])
        self.assertEqual(checks[0]["detail_params"]["name"], "Technitium")
        self.assertEqual(result["summary"]["warnings"], 0)

    def test_a_dead_extra_proxy_target_is_named_too(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(4, "NPM bis", "npm", reachable=False)

        result = self._preflight(
            proxy_provider_id=1, dns_provider_id=2, extra_proxy_provider_ids=[4]
        )

        checks = self._named(result, "extra_proxy_provider")
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0]["ok"])
        self.assertEqual(checks[0]["detail_params"]["name"], "NPM bis")
        self.assertEqual(result["summary"]["warnings"], 1)

    def test_an_extra_id_the_save_refuses_is_blocking(self):
        """The two verdicts have to agree: this body is a 400, and the panel must say so."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        payload = {"proxy_provider_id": 1, "dns_provider_id": 2, "extra_dns_provider_ids": [999]}

        result = self._preflight(**payload)

        self.assertEqual(result["summary"]["blocking_failures"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(self._named(result, "extra_dns_provider")[0]["detail_key"], "provider_missing")

        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as raised:
            self._create(**payload)
        self.assertEqual(raised.exception.status_code, 400)

    def test_a_disabled_extra_target_warns_without_blocking(self):
        """The push skips a disabled target instead of failing on it, so the panel does too."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium", enabled=False)
        payload = {"proxy_provider_id": 1, "dns_provider_id": 2, "extra_dns_provider_ids": [3]}

        result = self._preflight(**payload)

        check = self._named(result, "extra_dns_provider")[0]
        self.assertFalse(check["ok"])
        self.assertFalse(check["blocking"])
        self.assertEqual(check["detail_key"], "extra_provider_disabled")
        self.assertEqual(result["summary"]["warnings"], 1)
        self.assertEqual(result["summary"]["blocking_failures"], 0)

        status, body = self._create(**payload)
        self.assertEqual(status, 201)
        self.assertEqual(body["errors"], [])
        self.assertFalse(second.holds("vault.example.com"), "a disabled target receives nothing")

    def test_the_primary_is_not_probed_a_second_time_as_an_extra(self):
        """Same de-duplication as `add_service`, which drops an extra equal to its primary."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")

        result = self._preflight(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[2])

        self.assertEqual(self._named(result, "extra_dns_provider"), [])
        self.assertEqual(result["summary"]["warnings"], 0)


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
        self.assertIn("goes on being published by its other targets", detail["message"])
        self.assertTrue(detail["services"][0]["still_published"])

    def test_a_service_left_with_nothing_still_says_so(self):
        """The original sentence was right for this case, and has to survive."""
        self._add_provider(2, "AdGuard", "adguard")
        self._create(dns_provider_id=2)

        detail = self._conflict(2)

        self.assertIn("stops being published anywhere", detail["message"])
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


class DisablingWithdrawsFromEveryTargetTests(_MultiSyncTestCase):
    """Turning a service off has to take it off the extra targets, not only the two columns.

    The write routes learned to publish on every target and to withdraw from one that is
    dropped or renamed away. The `enabled` flag was left out of both walks: disabling a
    service suspended the primary proxy and deleted the primary DNS record, and said nothing
    to the second proxy or the second DNS server. The hostname went on resolving and the
    proxy went on forwarding while the interface showed the service as off, which is the one
    state an operator reads as "this is not reachable any more".
    """

    HOST = "vault.example.com"

    def _service_with_a_second_dns_server(self) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3])
        self.assertTrue(self.providers[3].holds(self.HOST), "the fixture never published")
        return body["id"]

    def _service_with_a_second_proxy(self) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(4, "NPM bis", "npm")
        _, body = self._create(
            proxy_provider_id=1, dns_provider_id=2, extra_proxy_provider_ids=[4]
        )
        self.assertTrue(self.providers[4].holds(self.HOST), "the fixture never published")
        return body["id"]

    def _bulk(self, ids: list[int], action: str):
        return services_api.bulk_action(
            services_api._BulkActionBody(ids=ids, action=action), _request("POST")
        )

    def test_disabling_takes_the_record_off_the_second_dns_server(self):
        sid = self._service_with_a_second_dns_server()

        services_api.update_service(
            sid,
            _request("PUT"),
            self._body(
                proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3], enabled=False
            ),
        )

        self.assertFalse(
            self.providers[3].holds(self.HOST),
            "the second DNS server kept resolving a service shown as disabled",
        )
        self.assertFalse(self.providers[2].holds(self.HOST), "the primary is the control")

    def test_disabling_takes_the_route_off_the_second_proxy(self):
        sid = self._service_with_a_second_proxy()

        services_api.update_service(
            sid,
            _request("PUT"),
            self._body(
                proxy_provider_id=1, dns_provider_id=2, extra_proxy_provider_ids=[4], enabled=False
            ),
        )

        self.assertFalse(
            self.providers[4].holds(self.HOST),
            "the second proxy kept forwarding a service shown as disabled",
        )

    def test_editing_a_service_that_is_already_off_leaves_the_extra_target_empty(self):
        """The primary had this exact defect: the branch only ran on a transition."""
        sid = self._service_with_a_second_dns_server()
        disabled = self._body(
            proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3], enabled=False
        )
        services_api.update_service(sid, _request("PUT"), disabled)
        self.providers[3].rewrites.append({"domain": self.HOST, "answer": self.IP})

        services_api.update_service(sid, _request("PUT"), disabled)

        self.assertFalse(
            self.providers[3].holds(self.HOST),
            "a record that reappeared on the extra target survived an edit of a disabled service",
        )

    def test_re_enabling_publishes_on_the_extra_target_again(self):
        sid = self._service_with_a_second_dns_server()
        services_api.update_service(
            sid,
            _request("PUT"),
            self._body(
                proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3], enabled=False
            ),
        )
        self.assertFalse(self.providers[3].holds(self.HOST))

        services_api.update_service(
            sid,
            _request("PUT"),
            self._body(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3]),
        )

        self.assertTrue(
            self.providers[3].holds(self.HOST),
            "the extra target stayed empty after the service came back",
        )

    def test_the_bulk_disable_withdraws_the_extra_target_too(self):
        """Selecting rows in the table has to do what editing them one by one does."""
        sid = self._service_with_a_second_dns_server()

        self._bulk([sid], "disable")

        self.assertFalse(
            self.providers[3].holds(self.HOST),
            "a bulk disable left the second DNS server resolving the hostname",
        )

    def test_the_bulk_enable_republishes_on_the_extra_target(self):
        sid = self._service_with_a_second_dns_server()
        self._bulk([sid], "disable")
        self.assertFalse(self.providers[3].holds(self.HOST))

        self._bulk([sid], "enable")

        self.assertTrue(
            self.providers[3].holds(self.HOST),
            "a bulk enable brought back the primary and left the extra target empty",
        )

    def test_a_service_with_no_extra_target_is_not_made_to_talk_to_anyone(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.calls.clear()

        services_api.update_service(
            sid := body["id"],
            _request("PUT"),
            self._body(proxy_provider_id=1, dns_provider_id=2, enabled=False),
        )

        self.assertEqual(sid, body["id"])
        self.assertEqual([c for c in self.calls if c[0] not in ("NPM", "AdGuard")], [])

    def test_the_journal_says_disabled_and_not_deleted(self):
        """`withdraw_service_routes` is shared with the delete routes, and it labelled the line.

        A service that is merely off still exists, and the journal is the one place an
        operator reconstructs which of the two happened to it. `[Delete] DNS record removed`
        beside a service still sitting in the table reads as the deletion that never was.
        """
        sid = self._service_with_a_second_dns_server()

        services_api.update_service(
            sid,
            _request("PUT"),
            self._body(
                proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3], enabled=False
            ),
        )

        withdrawals = [m for m in self._messages() if "Technitium" in m and "removed" in m]
        self.assertTrue(withdrawals, self._messages())
        self.assertTrue(
            all(m.startswith("[Disable]") for m in withdrawals),
            withdrawals,
        )
        self.assertEqual([m for m in withdrawals if m.startswith("[Delete]")], [])

    def test_deleting_the_service_still_says_deleted(self):
        """The witness for the line above: the prefix follows the reason, it is not gone."""
        sid = self._service_with_a_second_dns_server()

        services_api.delete_service(sid, _request("DELETE"))

        withdrawals = [m for m in self._messages() if "Technitium" in m and "removed" in m]
        self.assertTrue(withdrawals, self._messages())
        self.assertTrue(all(m.startswith("[Delete]") for m in withdrawals), withdrawals)


if __name__ == "__main__":
    unittest.main()


class PushingADisabledServiceTests(_MultiSyncTestCase):
    """A push converges the providers on the record, and the record is allowed to say off.

    `_collect_push_targets` reads `providers.enabled` and never read `services.enabled`, and
    neither did the push or the drift check. So a service the table showed as disabled was
    published again by anything that pushed it, and the drift check reported the absence it
    had itself asked for as two errors -- with a Reconcile button beside them whose only
    honest meaning would have been "publish it again".

    Two clicks reached that. The drift drawer offers Reconcile next to the errors it just
    listed, and `ExposeModal` fires a push of its own right after saving any service that
    carries a multi-sync target: pressing Save on a disabled service republished it on every
    provider while the row went on reading "off". The scheduler was the only safe caller, and
    only because `run_auto_reconcile` selects `WHERE enabled=1`.
    """

    HOST = "vault.example.com"

    def _published_then_switched_off_behind_the_route(self) -> int:
        """Published on two proxies and two DNS servers, then switched off in the table.

        The flag is written to the row rather than sent through `PUT` on purpose. The write
        route now withdraws as it disables, so going through it would converge the providers
        before the push could be asked anything. What is under test is the state the route is
        not the only way to reach: a disable whose provider was unreachable at the time, a row
        written before the withdrawal existed, a flag flipped anywhere else.
        """
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        self._add_provider(4, "NPM bis", "npm")
        _, body = self._create(
            proxy_provider_id=1,
            dns_provider_id=2,
            extra_dns_provider_ids=[3],
            extra_proxy_provider_ids=[4],
        )
        sid = body["id"]
        for pid in (1, 2, 3, 4):
            self.assertTrue(
                self.providers[pid].serves(self.HOST), f"the fixture never published on {pid}"
            )
        conn = models.get_db()
        conn.execute("UPDATE services SET enabled=0 WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        return sid

    def _stored_host_id(self, sid: int):
        conn = models.get_db()
        row = conn.execute("SELECT npm_host_id FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return row["npm_host_id"]

    def _full_body(self, **overrides):
        fields = {
            "proxy_provider_id": 1,
            "dns_provider_id": 2,
            "extra_dns_provider_ids": [3],
            "extra_proxy_provider_ids": [4],
        }
        fields.update(overrides)
        return self._body(**fields)

    def test_pushing_a_disabled_service_withdraws_it_instead_of_publishing_it(self):
        sid = self._published_then_switched_off_behind_the_route()

        result = sync_api.push_service(sid, _request("POST"))

        self.assertEqual(result["errors"], [])
        self.assertTrue(result["ok"])
        for pid in (1, 2, 3, 4):
            self.assertFalse(
                self.providers[pid].serves(self.HOST),
                f"provider {pid} still answers for a service the table shows as off",
            )

    def test_the_primary_proxy_is_suspended_where_the_second_one_is_deleted(self):
        """The asymmetry is the point, and it is the one the disable path already draws.

        Suspending keeps what NPM holds and Vauxtra does not model -- custom locations, the
        advanced block, the certificate binding -- and keeps `npm_host_id` pointing at a host
        that exists, which is what the re-enable toggles. The extra proxy has no stored id, so
        a suspension there could never be lifted; deleting it is what lets the next push
        create it again.
        """
        sid = self._published_then_switched_off_behind_the_route()
        stored = self._stored_host_id(sid)

        sync_api.push_service(sid, _request("POST"))

        self.assertTrue(self.providers[1].holds(self.HOST), "the primary host was deleted")
        self.assertFalse(self.providers[1].serves(self.HOST), "the primary host still answers")
        self.assertFalse(self.providers[4].holds(self.HOST), "the extra proxy was only suspended")
        self.assertEqual(
            self._stored_host_id(sid), stored, "npm_host_id moved, so the re-enable would miss"
        )

    def test_the_service_comes_back_whole_after_being_pushed_while_off(self):
        """The witness for the line above: withheld is a state a service can leave."""
        sid = self._published_then_switched_off_behind_the_route()
        sync_api.push_service(sid, _request("POST"))

        services_api.update_service(sid, _request("PUT"), self._full_body(enabled=True))

        for pid in (1, 2, 3, 4):
            self.assertTrue(
                self.providers[pid].serves(self.HOST),
                f"provider {pid} stayed dark after the service was switched back on",
            )

    def test_drift_is_clean_once_the_disabled_service_is_withheld(self):
        sid = self._published_then_switched_off_behind_the_route()
        sync_api.push_service(sid, _request("POST"))

        report = sync_api.service_drift(sid, _request("GET"))

        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["issues"], [], "a suspended proxy host was read as drift")

    def test_drift_names_what_still_serves_a_disabled_service(self):
        sid = self._published_then_switched_off_behind_the_route()

        report = sync_api.service_drift(sid, _request("GET"))

        self.assertFalse(report["ok"])
        types = {i["type"] for i in report["issues"]}
        self.assertIn("proxy_route_still_served", types)
        self.assertIn("dns_rewrite_still_served", types)
        self.assertNotIn("missing_proxy_route", types)
        self.assertNotIn("missing_dns_rewrite", types)
        self.assertEqual(
            {i["provider"] for i in report["issues"]},
            {"NPM", "NPM bis", "AdGuard", "Technitium"},
        )
        self.assertEqual(
            {i["detail_key"] for i in report["issues"]},
            {"route_still_served", "rewrite_still_served"},
        )

    def test_reconcile_converges_a_disabled_service_rather_than_publishing_it(self):
        sid = self._published_then_switched_off_behind_the_route()

        result = sync_api.reconcile_service(sid, _request("POST"))

        self.assertFalse(result["before"]["ok"], "the fixture was already converged")
        self.assertTrue(result["after"]["ok"], result["after"]["issues"])
        self.assertTrue(result["ok"], result["push"])
        for pid in (1, 2, 3, 4):
            self.assertFalse(self.providers[pid].serves(self.HOST), f"provider {pid} answers")

    def test_saving_a_disabled_service_does_not_bring_it_back_through_the_push(self):
        """ExposeModal pushes on its own after saving any service with a multi-sync target."""
        sid = self._published_then_switched_off_behind_the_route()
        services_api.update_service(sid, _request("PUT"), self._full_body(enabled=False))

        sync_api.push_service(sid, _request("POST"))

        for pid in (1, 2, 3, 4):
            self.assertFalse(
                self.providers[pid].serves(self.HOST),
                f"Save republished a disabled service on provider {pid}",
            )

    def test_an_enabled_service_is_still_published_by_the_same_push(self):
        """The other witness: the guard reads the flag, it does not stop the push."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3])
        self.providers[3].rewrites.clear()

        result = sync_api.push_service(body["id"], _request("POST"))

        self.assertEqual(result["errors"], [])
        self.assertTrue(self.providers[3].serves(self.HOST), "the push stopped publishing")

    # -- the dry-run, which is what says what the push will write -------------------------
    def _plan_actions(self, plan: dict) -> dict:
        """Provider name to the single action the plan holds for it."""
        actions = {a["provider_name"]: a["action"] for a in plan["proxy_actions"]}
        actions.update({a["provider_name"]: a["action"] for a in plan["dns_actions"]})
        self.assertEqual(
            len(actions),
            len(plan["proxy_actions"]) + len(plan["dns_actions"]),
            "two actions for one provider, so the mapping above hides one",
        )
        return actions

    def test_the_dry_run_describes_the_withdrawal_rather_than_a_publication(self):
        """`docs/TROUBLESHOOTING.md` sends the operator here first, so it has to be true.

        A plan promising to create the route on four providers, followed by a push that
        removes it from four providers, is the one answer a dry-run must never give.
        """
        sid = self._published_then_switched_off_behind_the_route()

        plan = sync_api.dry_run_push_service(sid, _request("POST"))

        self.assertTrue(plan["withheld"])
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertTrue(plan["would_change"])
        self.assertEqual(
            self._plan_actions(plan),
            {"NPM": "suspend", "NPM bis": "delete", "AdGuard": "delete", "Technitium": "delete"},
        )
        self.assertEqual(plan["service_updates"], [])
        self.assertEqual(plan["dns_target"], "", "a withdrawal resolves no address")

    def test_the_dry_run_writes_nothing(self):
        """The note under the panel says a dry run only reads. It has to stay true here."""
        sid = self._published_then_switched_off_behind_the_route()
        before = self._stored_host_id(sid)

        sync_api.dry_run_push_service(sid, _request("POST"))

        for pid in (1, 2, 3, 4):
            self.assertTrue(self.providers[pid].serves(self.HOST), f"provider {pid} was touched")
        self.assertEqual(self._stored_host_id(sid), before)

    def test_a_proxy_with_no_suspension_is_planned_as_the_deletion_it_will_be(self):
        """The fallback read off the class instead of discovered by the failed call.

        `ProxyProvider.toggle_host` returns False, so a provider that never overrode it can
        only fail the suspension `withhold_service_routes` would send, and the route has to
        be deleted. The plan says so in advance rather than promising a suspension nobody
        can perform.
        """
        sid = self._published_then_switched_off_behind_the_route()
        stale = self.providers[1]
        plain = _ProviderWithoutSuspension(stale.name, self.calls)
        plain.hosts, plain.rewrites = stale.hosts, stale.rewrites
        self.providers[1] = plain

        plan = sync_api.dry_run_push_service(sid, _request("POST"))

        self.assertEqual(self._plan_actions(plan)["NPM"], "delete")

        # And the push agrees, which is the only thing that makes the plan worth reading.
        sync_api.push_service(sid, _request("POST"))
        self.assertFalse(plain.holds(self.HOST))
        self.assertIsNone(
            self._stored_host_id(sid), "npm_host_id outlived the host it names"
        )

    def test_a_refused_suspension_does_not_turn_into_a_deletion(self):
        """The fallback is for a provider with no suspension, not for one that hiccupped.

        NPM and Zoraxy answer the same `False` whether they cannot suspend at all or merely
        failed this call, and the withdrawal read every `False` as the first. So an expired
        token, a 500 or a dropped connection during a disable deleted the host instead of
        suspending it -- taking the custom locations, the advanced configuration and the
        certificate binding the suspension exists to keep -- and blanked `npm_host_id`, so
        nothing was left to put back. A provider that refuses is a provider to report.
        """
        sid = self._published_then_switched_off_behind_the_route()
        stale = self.providers[1]
        refusing = _ProviderRefusingTheToggle(stale.name, self.calls)
        refusing.hosts, refusing.rewrites = stale.hosts, stale.rewrites
        self.providers[1] = refusing
        host_id_before = self._stored_host_id(sid)

        result = sync_api.push_service(sid, _request("POST"))

        self.assertTrue(
            refusing.holds(self.HOST), "a refused suspension deleted the host it could not suspend"
        )
        self.assertEqual(
            self._stored_host_id(sid), host_id_before, "npm_host_id was blanked on a host that is still there"
        )
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("suspend" in e for e in result["errors"]), result["errors"]
        )

    def test_the_plan_says_suspend_for_a_provider_that_can_suspend(self):
        """The positive control of the pair above: nothing about the wire changed the plan."""
        sid = self._published_then_switched_off_behind_the_route()
        stale = self.providers[1]
        refusing = _ProviderRefusingTheToggle(stale.name, self.calls)
        refusing.hosts, refusing.rewrites = stale.hosts, stale.rewrites
        self.providers[1] = refusing

        plan = sync_api.dry_run_push_service(sid, _request("POST"))

        self.assertEqual(self._plan_actions(plan)["NPM"], "suspend")

    def test_the_dry_run_of_an_enabled_service_still_plans_the_publication(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)

        plan = sync_api.dry_run_push_service(body["id"], _request("POST"))

        self.assertFalse(plan["withheld"])
        self.assertEqual(self._plan_actions(plan), {"NPM": "update", "AdGuard": "upsert"})


class ASuspendedRouteUnderAnEnabledServiceTests(_MultiSyncTestCase):
    """The mirror of the disabled service: the record says on, the proxy host is suspended.

    Suspension is how Vauxtra switches a service off -- it keeps the custom locations, the
    advanced configuration and the certificate that no column models, and it keeps
    `npm_host_id` valid. That makes "held but not serving" a state Vauxtra itself produces,
    and every way back out of it went through one `if` in `update_service`: the transition
    branch, which runs only when `enabled` actually flips and only in `proxy_dns` mode, and
    whose `except` writes a warning and lets the route answer 200.

    Everything downstream was blind to the result. `_compute_service_drift` looked the host
    up by hostname and then compared the origin, never the flag it had itself written, so a
    suspended route read as perfectly in sync. `update_host` is a PUT that does not carry
    `enabled`, so a push -- and Reconcile, which is a push -- walked over the host without
    lifting anything. The hostname answered nothing and every screen in Vauxtra said the
    service was published and in sync.
    """

    HOST = "vault.example.com"

    def _published_then_suspended_behind_the_route(self) -> int:
        """Published, then suspended on the proxy while the row still reads enabled.

        The suspension is applied to the provider rather than through `PUT` for the same
        reason the disabled-service fixture writes its flag straight to the row: the write
        route converges both halves, and what is under test is the state reached when one
        half did not happen. A failed re-enable is the ordinary way there -- the `except`
        in `update_service` swallows it -- and an operator suspending the host in NPM's own
        interface is the other.
        """
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        npm = self.providers[1]
        self.assertTrue(npm.serves(self.HOST), "the fixture never published")
        self.assertTrue(npm.toggle_host(npm.hosts[0]["id"], False))
        self.assertTrue(npm.holds(self.HOST))
        self.assertFalse(npm.serves(self.HOST))
        return sid

    def test_drift_reports_a_route_that_is_held_but_not_served(self):
        sid = self._published_then_suspended_behind_the_route()

        drift = sync_api.service_drift(sid, _request("GET"))

        kinds = {i["type"] for i in drift["issues"]}
        self.assertIn("proxy_route_suspended", kinds, drift["issues"])
        self.assertFalse(drift["ok"], "a hostname that answers nothing is not in sync")

    def test_a_push_lifts_the_suspension_instead_of_writing_past_it(self):
        sid = self._published_then_suspended_behind_the_route()

        result = sync_api.push_service(sid, _request("POST"))

        self.assertEqual(result["errors"], [])
        self.assertTrue(
            self.providers[1].serves(self.HOST),
            "the push updated the host and left it suspended",
        )

    def test_reconcile_converges_a_suspended_route(self):
        sid = self._published_then_suspended_behind_the_route()

        result = sync_api.reconcile_service(sid, _request("POST"))

        self.assertTrue(result["ok"], result["after"]["issues"])
        self.assertTrue(self.providers[1].serves(self.HOST))

    def test_the_dry_run_says_the_suspension_will_be_lifted(self):
        sid = self._published_then_suspended_behind_the_route()

        plan = sync_api.dry_run_push_service(sid, _request("POST"))

        self.assertFalse(plan["withheld"])
        actions = {a["provider_name"]: a["action"] for a in plan["proxy_actions"]}
        self.assertEqual(actions["NPM"], "resume")

    def test_a_route_that_serves_is_not_reported_as_suspended(self):
        """The positive control: nothing about an ordinary published service changes."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)

        drift = sync_api.service_drift(body["id"], _request("GET"))

        self.assertTrue(drift["ok"], drift["issues"])
        self.assertEqual(drift["issues"], [])

    def test_a_provider_that_never_reports_the_flag_is_read_as_serving(self):
        """Cloudflare Tunnel has no `enabled` key at all, and a rule that is there serves.

        The default matters more than it looks: read the other way, every provider without
        a suspension would report every route it holds as suspended, which is the same
        false alarm as the one being fixed, pointed at more services.
        """
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        for host in self.providers[1].hosts:
            host.pop("enabled", None)

        drift = sync_api.service_drift(body["id"], _request("GET"))

        self.assertTrue(drift["ok"], drift["issues"])


class ResumingAProxyHostOnEnableTests(_MultiSyncTestCase):
    """Re-enabling a service is one call, and its failure was filed as a success.

    `toggle_host` returns False for two unrelated reasons: the provider has no suspension,
    or the provider has one and refused. The enable branch read every False as the first and
    wrote `Proxy active (toggle not supported, host already present)` at info level -- which
    is exactly wrong for NPM and Zoraxy, the only two that implement it. A failed resume left
    the host suspended, the row reading enabled, the route answering nothing, and the journal
    saying the proxy was active.
    """

    HOST = "vault.example.com"

    def _switched_off_with_the_host_kept(self, replacement=None) -> int:
        """Published, then switched off in the table with `npm_host_id` still naming the rule.

        That is what a suspension leaves behind. The flag is written to the row rather than
        sent through `PUT` so that the next `PUT` is the transition back to enabled, which is
        the branch under test; going through the route twice would spend the transition on
        the way down.
        """
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        npm = self.providers[1]
        self.assertTrue(npm.toggle_host(npm.hosts[0]["id"], False))
        if replacement is not None:
            swap = replacement(npm.name, self.calls)
            swap.hosts, swap.rewrites = npm.hosts, npm.rewrites
            self.providers[1] = swap
        conn = models.get_db()
        conn.execute("UPDATE services SET enabled=0 WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        self.assertIsNotNone(self._stored_host_id(sid), "the fixture lost the host id")
        return sid

    def _stored_host_id(self, sid: int):
        conn = models.get_db()
        row = conn.execute("SELECT npm_host_id FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return row["npm_host_id"]

    def _enable(self, sid: int) -> dict:
        return services_api.update_service(
            sid,
            _request("PUT"),
            self._body(proxy_provider_id=1, dns_provider_id=2, enabled=True),
        )

    def test_a_refused_resume_is_reported_instead_of_logged_as_active(self):
        sid = self._switched_off_with_the_host_kept(_ProviderRefusingTheToggle)

        result = self._enable(sid)

        self.assertTrue(result["errors"], "the route answered clean about a host still off")
        self.assertFalse(self.providers[1].serves(self.HOST))

    def test_a_successful_resume_says_nothing_and_serves_again(self):
        """The positive control: the ordinary re-enable is unchanged."""
        sid = self._switched_off_with_the_host_kept()

        result = self._enable(sid)

        self.assertEqual(result["errors"], [])
        self.assertTrue(self.providers[1].serves(self.HOST))

    def test_a_provider_with_no_suspension_is_not_called_a_failure(self):
        """Its `False` is the base class answering, and the host was never suspended.

        Reading that as a refusal would file an error on every provider that has no toggle,
        which is most of them -- the same false alarm as the one being fixed, pointed the
        other way.
        """
        sid = self._switched_off_with_the_host_kept(_ProviderWithoutSuspension)
        for host in self.providers[1].hosts:
            host["enabled"] = True

        result = self._enable(sid)

        self.assertEqual(result["errors"], [])


class AProviderThatSpellsTheHostnameBackTests(_MultiSyncTestCase):
    """A hostname is not case-sensitive, and half of Vauxtra compared it as if it were.

    Zoraxy keeps a rule under the spelling it was typed with and AdGuard echoes the name it
    was given, so a route created as "Vault.Example.com" is the route the service spells
    "vault.example.com". The push had learned that; the drift check and one of the two DNS
    readers had not. The result was a route reported missing with a Reconcile button beside
    it, a push that found the host and updated it, and the same error still on screen -- plus
    a second DNS record added next to the one that was already right.
    """

    HOST = "vault.example.com"

    def _published_then_spelled_back(self) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        for pid in (1, 2):
            stale = self.providers[pid]
            echoing = _ProviderEchoingMixedCase(stale.name, self.calls)
            echoing.hosts, echoing.rewrites = stale.hosts, stale.rewrites
            self.providers[pid] = echoing
        return sid

    def test_drift_does_not_call_an_existing_route_missing(self):
        sid = self._published_then_spelled_back()

        drift = sync_api.service_drift(sid, _request("GET"))

        kinds = {i["type"] for i in drift["issues"]}
        self.assertNotIn("missing_proxy_route", kinds, drift["issues"])
        self.assertNotIn("missing_dns_rewrite", kinds, drift["issues"])
        self.assertTrue(drift["ok"], drift["issues"])

    def test_a_push_does_not_add_a_second_record_beside_the_first(self):
        sid = self._published_then_spelled_back()
        dns = self.providers[2]

        result = sync_api.push_service(sid, _request("POST"))

        self.assertEqual(result["errors"], [])
        self.assertEqual(
            [r["domain"] for r in dns.rewrites],
            [self.HOST],
            "the push read past a record it could not spell and added another",
        )

    def test_a_provider_that_answers_in_the_same_spelling_is_unaffected(self):
        """The positive control: the pair above measures the spelling, nothing else."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)

        drift = sync_api.service_drift(body["id"], _request("GET"))
        result = sync_api.push_service(body["id"], _request("POST"))

        self.assertTrue(drift["ok"], drift["issues"])
        self.assertEqual(result["errors"], [])
        self.assertEqual([r["domain"] for r in self.providers[2].rewrites], [self.HOST])


class ClearingAPrimaryTakesItsRouteDownTests(_MultiSyncTestCase):
    """The provider columns can be emptied, and until now that only hid the route.

    An operator who moves a service to DNS only, or to proxy only, clears one of the two
    fields in the editor. The route that provider was publishing has to come down with it,
    the same way a target dropped from the multi-sync list does -- and the same way the
    switch to tunnel mode already does it for both columns at once.
    """

    HOST = "vault.example.com"

    def _published(self) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertTrue(self.providers[1].holds(self.HOST))
        self.assertTrue(self.providers[2].holds(self.HOST))
        return body["id"]

    def _stored(self, sid: int) -> dict:
        conn = models.get_db()
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return dict(row)

    def test_clearing_the_proxy_provider_takes_its_host_down(self):
        sid = self._published()

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=None, dns_provider_id=2)
        )

        self.assertIsNone(self._stored(sid)["proxy_provider_id"])
        self.assertFalse(
            self.providers[1].holds(self.HOST),
            "the proxy went on serving a hostname Vauxtra had stopped claiming, and the "
            "same edit blanked `npm_host_id`, so nothing could reach the host afterwards",
        )

    def test_clearing_the_dns_provider_takes_its_record_down(self):
        sid = self._published()

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=None)
        )

        self.assertIsNone(self._stored(sid)["dns_provider_id"])
        self.assertFalse(
            self.providers[2].holds(self.HOST),
            "the rewrite went on resolving the hostname with no DNS provider on the service",
        )

    def test_deleting_the_service_afterwards_finds_nothing_left(self):
        """What the orphan cost: the columns are the only map to the holders.

        `_all_route_holders` reads `proxy_provider_id` and `dns_provider_id`, so a route
        left behind by the edit that emptied them is one no later deletion can see.
        """
        sid = self._published()
        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=None, dns_provider_id=None,
                                             extra_proxy_provider_ids=[1])
        )

        services_api.delete_service(sid, _request("DELETE"))

        self.assertFalse(self.providers[1].holds(self.HOST))
        self.assertFalse(self.providers[2].holds(self.HOST))

    def test_a_primary_moved_into_the_extras_keeps_serving(self):
        """The control that matters: dropped from the column is not dropped from the service."""
        sid = self._published()
        self._add_provider(4, "NPM bis", "npm")

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=4, dns_provider_id=2,
                                             extra_proxy_provider_ids=[1])
        )

        self.assertEqual(self._push_targets(sid), {1})
        self.assertTrue(
            self.providers[1].holds(self.HOST),
            "a provider promoted to a multi-sync target was withdrawn from instead",
        )
        self.assertTrue(self.providers[4].holds(self.HOST))

    def test_an_edit_that_keeps_both_primaries_withdraws_nothing(self):
        """The second control: an ordinary edit must not take anything down."""
        sid = self._published()

        services_api.update_service(
            sid, _request("PUT"), self._body(proxy_provider_id=1, dns_provider_id=2,
                                             target_port=9000)
        )

        self.assertTrue(self.providers[1].holds(self.HOST))
        self.assertTrue(self.providers[2].holds(self.HOST))


class DisablingAServiceDoesNotDeleteARefusedHostTests(_MultiSyncTestCase):
    """The reading `withhold_service_routes` was taught, asked of the two write routes.

    NPM and Zoraxy answer `False` from `toggle_host` when the call failed, and the base's
    `toggle_host` answers the same `False` because there is no suspension to attempt. Both
    disable paths here read every `False` as the second one and deleted the host: the custom
    locations, the advanced block and the certificate binding went with it, `npm_host_id` was
    blanked so nothing could put it back, and the journal recorded an info line saying the
    route had been removed on purpose.
    """

    HOST = "vault.example.com"

    def _published(self, proxy_class=None) -> int:
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        if proxy_class is not None:
            swapped = proxy_class("NPM", self.calls)
            swapped.hosts = self.providers[1].hosts
            swapped.rewrites = self.providers[1].rewrites
            self.providers[1] = swapped
        self.assertTrue(self.providers[1].holds(self.HOST))
        return body["id"]

    def _stored_host_id(self, sid: int):
        conn = models.get_db()
        row = conn.execute("SELECT npm_host_id FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return row["npm_host_id"]

    # -- the editor's own toggle ------------------------------------------------------------
    def test_the_editor_reports_a_refused_suspension_instead_of_deleting(self):
        sid = self._published(_ProviderRefusingTheToggle)
        host_id_before = self._stored_host_id(sid)

        result = services_api.update_service(
            sid, _request("PUT"),
            self._body(proxy_provider_id=1, dns_provider_id=2, enabled=False),
        )

        self.assertTrue(
            self.providers[1].holds(self.HOST),
            "a provider that refused one toggle had its host deleted, configuration and all",
        )
        self.assertEqual(self._stored_host_id(sid), host_id_before)
        self.assertTrue(any("suspend" in e for e in result["errors"]), result["errors"])

    def test_the_editor_still_deletes_where_there_is_no_suspension(self):
        """The control: the fallback exists for this provider and has to keep working."""
        sid = self._published(_ProviderWithoutSuspension)

        result = services_api.update_service(
            sid, _request("PUT"),
            self._body(proxy_provider_id=1, dns_provider_id=2, enabled=False),
        )

        self.assertFalse(self.providers[1].holds(self.HOST))
        self.assertIsNone(self._stored_host_id(sid))
        self.assertEqual(result["errors"], [])

    # -- the table's bulk action ------------------------------------------------------------
    def test_a_bulk_disable_reports_a_refused_suspension_instead_of_deleting(self):
        sid = self._published(_ProviderRefusingTheToggle)
        host_id_before = self._stored_host_id(sid)

        result = services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action="disable"), _request("POST")
        )

        self.assertTrue(
            self.providers[1].holds(self.HOST),
            "selecting the row and pressing Disable deleted the host the editor now keeps",
        )
        self.assertEqual(self._stored_host_id(sid), host_id_before)
        self.assertTrue(any("suspend" in e for e in result["errors"]), result["errors"])

    def test_a_bulk_disable_still_deletes_where_there_is_no_suspension(self):
        """The control for the batch, and the one that says the two routes agree."""
        sid = self._published(_ProviderWithoutSuspension)

        result = services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action="disable"), _request("POST")
        )

        self.assertFalse(self.providers[1].holds(self.HOST))
        self.assertIsNone(self._stored_host_id(sid))
        self.assertEqual(result["errors"], [])
