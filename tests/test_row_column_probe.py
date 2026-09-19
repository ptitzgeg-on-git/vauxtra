"""Asking a database row whether it has a column, and getting a truthful answer.

`"expose_mode" in svc` is the obvious spelling and it asks a `sqlite3.Row` the wrong
question. A row is a sequence, so `in` searches its VALUES, not its column names. Measured
on the dev instance, on a real service row:

    keys: ['expose_mode', 'name']
    "expose_mode" in r  -> False
    "tunnel" in r       -> True
    r['expose_mode']    -> tunnel

No service column ever holds the literal string "expose_mode", so the six sites in
`app/api/sync.py` that asked were deterministically False and every one of them fell
through to its default. Nothing ever contradicted them, which is why this sat there: the
guard did not misjudge a case, it silently answered "no" to all of them.

Four things were broken by it, and each has a test here.

A tunnel service read as `proxy_dns`. `proxy_provider_id` is NULL on such a service, so it
was collected against no provider at all: the drift check compared it to nothing and said
"in sync" about a route that had never been published, and the dry-run beside it said there
was nothing to do. Measured on the dev instance, on a service whose creation had literally
answered `errors: ["Failed to create tunnel route"]`: drift `{"mode": "proxy_dns",
"issues": []}` and dry-run `{"proxy_actions": [], "would_change": false}`.

`tunnel_hostname` was never read, so a service exposed under one name was read, compared
and -- worse -- withdrawn under another.

And `public_target_mode` was read the same way on every service, tunnel or not, so `auto`
reached `resolve_public_target` as `manual` and never re-resolved the public address it
exists to follow.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import sync as sync_api


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class WhatARowAnswersTests(unittest.TestCase):
    """The demonstration that found the defect, kept as a test.

    Written against `sqlite3` directly rather than against a Vauxtra schema: the trap is a
    property of the row type, and a test that reads the real `services` table would go on
    passing the day a column called `expose_mode` stops existing.
    """

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE t (expose_mode TEXT, name TEXT)")
        self.conn.execute("INSERT INTO t VALUES ('tunnel', 'vault')")
        self.row = self.conn.execute("SELECT * FROM t").fetchone()

    def tearDown(self) -> None:
        self.conn.close()

    def test_in_searches_the_values_of_a_row(self):
        self.assertIn("expose_mode", self.row.keys())
        self.assertNotIn("expose_mode", self.row)  # the whole defect, in one line
        self.assertIn("tunnel", self.row)  # a VALUE, and `in` is happy to find it

    def test_has_column_answers_on_the_column_names(self):
        self.assertTrue(sync_api._has_column(self.row, "expose_mode"))
        self.assertTrue(sync_api._has_column(self.row, "name"))
        self.assertFalse(sync_api._has_column(self.row, "tunnel"))
        self.assertFalse(sync_api._has_column(self.row, "public_target_mode"))

    def test_has_column_reads_a_dict_the_same_way(self):
        """Every other caller in `app/` passes a dict, and a dict already answers on keys.

        The helper has to leave that behaviour alone, or moving a call site from one shape
        to the other would change the answer.
        """
        mapping = {"expose_mode": "tunnel", "name": "vault"}
        self.assertTrue(sync_api._has_column(mapping, "expose_mode"))
        self.assertFalse(sync_api._has_column(mapping, "tunnel"))

    def test_has_column_survives_a_row_that_is_only_a_sequence(self):
        """A plain tuple has no `keys`, so the membership test is all there is to fall back
        on. It is the wrong answer, and it is the only one available: what matters is that
        the helper does not raise on a shape it was not given."""
        self.assertFalse(sync_api._has_column(("tunnel", "vault"), "expose_mode"))


class _FakeTunnel:
    """A Cloudflare-Tunnel-shaped proxy that keeps its ingress rules in memory.

    `HOST_ID_IS_HOSTNAME` is declared because the real one declares it: the tunnel
    addresses a rule by the hostname it serves, so the name the withdrawal passes to
    `delete_host` is the assertion, not an id nobody can read.
    """

    HOST_ID_IS_HOSTNAME = True

    def __init__(self, calls: list):
        self.calls = calls
        self.hosts: list[dict] = []

    def list_hosts(self):
        return list(self.hosts)

    def create_host(self, domain, ip, port, scheme, websocket, cert_id):
        self.calls.append(("create_host", domain))
        host = {
            "id": domain,
            "domains": [domain],
            "host": ip,
            "port": port,
            "scheme": scheme,
            "enabled": True,
        }
        self.hosts.append(host)
        return host

    def update_host(self, host_id, domain, ip, port, scheme, websocket, cert_id):
        self.calls.append(("update_host", host_id, domain))
        return True

    def delete_host(self, host_id):
        self.calls.append(("delete_host", host_id))
        before = len(self.hosts)
        self.hosts = [h for h in self.hosts if host_id not in h["domains"]]
        return len(self.hosts) < before

    def find_best_certificate(self, _domain):
        return None

    def publish(self, host: str, origin: str = "http://10.0.0.9:8080") -> None:
        """Seed a rule the way the tunnel would already hold one."""
        scheme, rest = origin.split("://", 1)
        ip, port = rest.rsplit(":", 1)
        self.hosts.append(
            {"id": host, "domains": [host], "host": ip, "port": int(port),
             "scheme": scheme, "enabled": True}
        )


class _SyncTestCase(unittest.TestCase):
    """A database with one tunnel provider, and the sync routes pointed at a fake."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "rowprobe.test.db")
        models.init_db()

        self.calls: list = []
        self.tunnel = _FakeTunnel(self.calls)
        self._patchers = [
            patch.object(sync_api, "require_auth", lambda _req, scope=None: None),
            patch.object(sync_api, "create_provider", lambda _row: self.tunnel),
        ]
        for p in self._patchers:
            p.start()

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (1, 'CF Tunnel', 'cloudflare_tunnel', '', 'acct', 'token', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        import app.db as _app_db

        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures ---------------------------------------------------------------------
    def _tunnel_service(self, *, subdomain: str = "vault", hostname: str = "vault.example.com") -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, tunnel_provider_id, tunnel_hostname)
               VALUES (?, 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'tunnel', 1, ?)""",
            (subdomain, hostname),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def _row(self, sid: int):
        conn = models.get_db()
        svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        return conn, svc


class TheDriftCheckReachesTheTunnelTests(_SyncTestCase):
    """A tunnel route that is not there has to read as missing, not as in sync.

    This is the consequence that was measured live: a service whose creation had answered
    `errors: ["Failed to create tunnel route"]` came back from the drift check as
    `{"ok": true, "issues": []}` one call later, because the check had collected it against
    `proxy_provider_id` -- NULL on a tunnel service -- and found nothing to compare.
    """

    def test_a_tunnel_service_is_read_in_tunnel_mode(self):
        drift = sync_api.service_drift(self._tunnel_service(), _request())
        self.assertEqual(drift["mode"], "tunnel")

    def test_an_unpublished_tunnel_route_is_reported_missing(self):
        drift = sync_api.service_drift(self._tunnel_service(), _request())
        self.assertFalse(drift["ok"])
        self.assertEqual([i["type"] for i in drift["issues"]], ["missing_proxy_route"])
        self.assertEqual(drift["issues"][0]["provider"], "CF Tunnel")

    def test_a_published_tunnel_route_reads_as_in_sync(self):
        """The mirror, so the fix cannot be a check that simply always complains."""
        self.tunnel.publish("vault.example.com")
        drift = sync_api.service_drift(self._tunnel_service(), _request())
        self.assertTrue(drift["ok"])
        self.assertEqual(drift["issues"], [])

    def test_a_tunnel_route_pointing_elsewhere_is_reported(self):
        self.tunnel.publish("vault.example.com", origin="http://10.0.0.99:9999")
        drift = sync_api.service_drift(self._tunnel_service(), _request())
        self.assertEqual([i["type"] for i in drift["issues"]], ["proxy_origin_mismatch"])

    def test_a_provider_that_will_not_list_is_named_rather_than_read_as_empty(self):
        """The D69 answer, which only reaches a tunnel service now that the mode is read.

        A refused listing and an empty one are the same `[]` on the wire; the provider says
        which by raising. Read as `proxy_dns`, a tunnel service never asked the question at
        all, so this whole path was unreachable for it.
        """
        from app.providers.base import ProviderListingRefused

        def _refuse():
            raise ProviderListingRefused("Cloudflare would not list the tunnel's ingress rules")

        self.tunnel.list_hosts = _refuse
        drift = sync_api.service_drift(self._tunnel_service(), _request())
        self.assertEqual([i["type"] for i in drift["issues"]], ["proxy_check_failed"])
        self.assertIn("ingress rules", drift["issues"][0]["detail"])


class ThePushPlanNamesTheTunnelActionTests(_SyncTestCase):
    """The dry-run sits beside the drift report and answered the same nothing.

    Measured live on the same unpublished service: `{"proxy_actions": [], "would_change":
    false}`. An operator reading the preview of the Reconcile button was told there was
    nothing to reconcile.
    """

    def test_the_plan_is_built_in_tunnel_mode(self):
        plan = sync_api.dry_run_push_service(self._tunnel_service(), _request("POST"))
        self.assertEqual(plan["mode"], "tunnel")

    def test_an_unpublished_tunnel_route_is_planned_for_creation(self):
        plan = sync_api.dry_run_push_service(self._tunnel_service(), _request("POST"))
        self.assertTrue(plan["would_change"])
        self.assertEqual(
            [(a["provider_name"], a["action"], a["target_host"]) for a in plan["proxy_actions"]],
            [("CF Tunnel", "create", "vault.example.com")],
        )

    def test_a_published_tunnel_route_is_planned_for_an_update(self):
        self.tunnel.publish("vault.example.com")
        plan = sync_api.dry_run_push_service(self._tunnel_service(), _request("POST"))
        self.assertEqual([a["action"] for a in plan["proxy_actions"]], ["update"])

    def test_a_tunnel_plan_asks_for_no_dns_record(self):
        """The tunnel publishes the name itself; the DNS half belongs to the other mode."""
        plan = sync_api.dry_run_push_service(self._tunnel_service(), _request("POST"))
        self.assertEqual(plan["dns_actions"], [])
        self.assertEqual(plan["dns_target"], "")

    def test_the_push_writes_the_tunnel_rule(self):
        sid = self._tunnel_service()
        result = sync_api.push_service(sid, _request("POST"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.calls, [("create_host", "vault.example.com")])
        self.assertTrue(sync_api.service_drift(sid, _request())["ok"])


class ThePublicHostFollowsTheTunnelHostnameTests(_SyncTestCase):
    """A tunnel service is exposed under `tunnel_hostname`, not under `subdomain.domain`.

    The two are the same for most services, which is exactly why this went unseen. Measured
    live where they differ: a service with subdomain `interne` and tunnel hostname
    `testva2.example.com` reported `public_host: interne.example.com`. Every read, every
    comparison and -- the one that bites -- every withdrawal then named the wrong host.
    """

    SUB = "interne"
    HOST = "testva2.example.com"

    def _service(self) -> int:
        return self._tunnel_service(subdomain=self.SUB, hostname=self.HOST)

    def test_the_drift_check_compares_the_tunnel_hostname(self):
        drift = sync_api.service_drift(self._service(), _request())
        self.assertEqual(drift["public_host"], self.HOST)
        self.assertEqual(drift["issues"][0]["detail_params"]["host"], self.HOST)

    def test_the_plan_targets_the_tunnel_hostname(self):
        plan = sync_api.dry_run_push_service(self._service(), _request("POST"))
        self.assertEqual(plan["public_host"], self.HOST)
        self.assertEqual([a["target_host"] for a in plan["proxy_actions"]], [self.HOST])

    def test_a_route_held_under_the_subdomain_does_not_count_as_published(self):
        """The failing half of the old behaviour, stated as what it would have meant.

        `interne.example.com` is not the name the tunnel serves. A check that accepted it
        would call the service published while nothing answered on the real hostname.
        """
        self.tunnel.publish(f"{self.SUB}.example.com")
        drift = sync_api.service_drift(self._service(), _request())
        self.assertFalse(drift["ok"])
        self.assertEqual([i["type"] for i in drift["issues"]], ["missing_proxy_route"])

    def test_the_withdrawal_removes_the_tunnel_hostname(self):
        sid = self._service()
        self.tunnel.publish(self.HOST)
        conn, svc = self._row(sid)
        try:
            errors = sync_api.withdraw_service_routes(conn, svc, sid)
        finally:
            conn.close()
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [("delete_host", self.HOST)])
        self.assertEqual(self.tunnel.hosts, [])


class AnAutoPublicTargetReachesTheResolverTests(_SyncTestCase):
    """`public_target_mode = "auto"` exists to re-resolve the public address on every push.

    It was read with the same broken test, on every service and not only a tunnel one, so
    `resolve_public_target` was always called with `mode="manual"`: the stored `dns_ip` was
    re-published verbatim for ever and the mode the operator chose did nothing at all.

    The resolver itself is stubbed here. What these tests weigh is the mode the two call
    sites hand it -- the plan's and the push's -- which is the part that was wrong.
    """

    RESOLVED = "203.0.113.55"

    def setUp(self) -> None:
        super().setUp()
        self.resolver_calls: list[dict] = []

        def _record(conn, *, mode, manual_value, proxy_provider_id, current_value):
            self.resolver_calls.append(
                {"mode": mode, "manual_value": manual_value, "current_value": current_value}
            )
            return (self.RESOLVED, "proxy_provider") if mode == "auto" else (manual_value, "manual")

        patcher = patch.object(sync_api, "resolve_public_target", _record)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _proxy_service(self, *, target_mode: str) -> int:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, proxy_provider_id, dns_provider_id, dns_ip,
                  public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'proxy_dns',
                       2, 3, '198.51.100.7', ?)""",
            (target_mode,),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def test_the_plan_asks_the_resolver_in_auto_mode(self):
        plan = sync_api.dry_run_push_service(self._proxy_service(target_mode="auto"), _request("POST"))
        self.assertEqual([c["mode"] for c in self.resolver_calls], ["auto"])
        self.assertEqual(plan["dns_target"], self.RESOLVED)
        self.assertEqual(plan["dns_target_source"], "proxy_provider")

    def test_the_plan_leaves_a_manual_target_manual(self):
        plan = sync_api.dry_run_push_service(self._proxy_service(target_mode="manual"), _request("POST"))
        self.assertEqual([c["mode"] for c in self.resolver_calls], ["manual"])
        self.assertEqual(plan["dns_target"], "198.51.100.7")

    def test_an_auto_target_is_not_pinned_to_the_stored_address(self):
        """`manual_value` is emptied in auto mode, or the resolver would be handed the very
        address it is there to replace."""
        sync_api.dry_run_push_service(self._proxy_service(target_mode="auto"), _request("POST"))
        self.assertEqual(self.resolver_calls[0]["manual_value"], "")
        self.assertEqual(self.resolver_calls[0]["current_value"], "198.51.100.7")

    def test_the_plan_announces_the_address_it_will_write(self):
        plan = sync_api.dry_run_push_service(self._proxy_service(target_mode="auto"), _request("POST"))
        self.assertEqual(
            plan["service_updates"],
            [{"field": "dns_ip", "old": "198.51.100.7", "new": self.RESOLVED,
              "source": "proxy_provider"}],
        )
        self.assertEqual([a["target"] for a in plan["dns_actions"]], [self.RESOLVED])

    def test_the_push_stores_the_re_resolved_address(self):
        sid = self._proxy_service(target_mode="auto")
        sync_api.push_service(sid, _request("POST"))
        self.assertEqual([c["mode"] for c in self.resolver_calls], ["auto"])
        conn, svc = self._row(sid)
        try:
            self.assertEqual(svc["dns_ip"], self.RESOLVED)
        finally:
            conn.close()
