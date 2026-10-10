"""Regression tests for the correctness findings of the cross-checked review (C1 to C13, P2).

Each class names the finding it covers. The fake provider and the isolated database come
from `test_multi_sync_targets.py`; `tests/` is not a package, so they are loaded by path.
"""

import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app import models
from app.api import services as services_api
from app.api import sync as sync_api

_spec = importlib.util.spec_from_file_location(
    "_multi_sync_fixtures", os.path.join(os.path.dirname(__file__), "test_multi_sync_targets.py")
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)

_request = _fixtures._request
FQDN = "vault.example.com"


class _Base(_fixtures._MultiSyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        p = patch.object(sync_api, "require_auth", lambda _req, scope=None: None)
        p.start()
        self._patchers.append(p)

    def _update(self, sid: int, **overrides):
        return services_api.update_service(sid, _request("PUT"), self._body(**overrides))

    def _bulk(self, action: str, *ids: int):
        return services_api.bulk_action(
            services_api._BulkActionBody(ids=list(ids), action=action), _request()
        )

    def _row(self, sid: int) -> dict:
        conn = models.get_db()
        try:
            return dict(conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone())
        finally:
            conn.close()


# -------------------------------------------------------------------------------- C1


class C1DisablingWithdrawsTheExtraTargetsTests(_Base):
    def _setup(self):
        self._add_provider(1, "NPM", "npm")
        primary = self._add_provider(2, "AdGuard", "adguard")
        second = self._add_provider(3, "Technitium", "technitium")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3])
        self.assertTrue(second.holds(FQDN))
        return body["id"], primary, second

    def test_update_to_disabled_withdraws_the_extra_dns_record(self):
        sid, primary, second = self._setup()
        self._update(sid, proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3],
                     enabled=False)
        self.assertFalse(second.holds(FQDN), "the extra DNS target kept publishing")

    def test_bulk_disable_withdraws_the_extra_dns_record(self):
        sid, _primary, second = self._setup()
        self._bulk("disable", sid)
        self.assertFalse(second.holds(FQDN), "the extra DNS target kept publishing")

    def test_bulk_enable_publishes_it_again(self):
        sid, _primary, second = self._setup()
        self._bulk("disable", sid)
        self._bulk("enable", sid)
        self.assertTrue(second.holds(FQDN))


# -------------------------------------------------------------------------------- C2


class C2ChangingTheDnsProviderCleansTheOldOneTests(_Base):
    def test_the_former_dns_provider_loses_the_record(self):
        self._add_provider(1, "NPM", "npm")
        old = self._add_provider(2, "AdGuard", "adguard")
        new = self._add_provider(3, "Technitium", "technitium")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertTrue(old.holds(FQDN))

        self._update(body["id"], proxy_provider_id=1, dns_provider_id=3)

        self.assertFalse(old.holds(FQDN), "the former DNS provider kept an orphan record")
        self.assertTrue(new.holds(FQDN))

    def test_removing_the_dns_provider_cleans_it_too(self):
        self._add_provider(1, "NPM", "npm")
        old = self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)

        self._update(body["id"], proxy_provider_id=1, dns_provider_id=None)

        self.assertFalse(old.holds(FQDN))


# ------------------------------------------------------------------------------ C13.1


class C13DisableFailureKeepsTheHostIdTests(_Base):
    def test_a_refused_delete_is_reported_and_the_id_kept(self):
        proxy = self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        host_id = self._row(sid)["npm_host_id"]
        self.assertTrue(host_id)

        proxy.ok = False
        proxy.toggle_host = lambda *_a: False  # no suspend either: the delete is the fallback
        result = self._bulk("disable", sid)

        self.assertEqual(self._row(sid)["npm_host_id"], host_id,
                         "the id of a host still live on the proxy was forgotten")
        self.assertTrue(result["errors"])

    def test_a_refused_dns_delete_is_reported_by_bulk(self):
        self._add_provider(1, "NPM", "npm")
        dns = self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)

        dns.ok = False
        result = self._bulk("disable", body["id"])

        self.assertTrue(result["errors"], "a DNS record left live was reported as withdrawn")


# -------------------------------------------------------------------------------- C6


class C6PushRefusesADisabledServiceTests(_Base):
    def test_the_push_route_answers_409_and_publishes_nothing(self):
        self._add_provider(1, "NPM", "npm")
        dns = self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2, enabled=False)
        self.calls.clear()

        with self.assertRaises(HTTPException) as ctx:
            sync_api.push_service(body["id"], _request())

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertFalse(dns.holds(FQDN))
        self.assertEqual(self.calls, [])

    def test_the_helper_refuses_it_too(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2, enabled=False)
        self.calls.clear()
        conn = models.get_db()
        try:
            svc = conn.execute("SELECT * FROM services WHERE id=?", (body["id"],)).fetchone()
            result = sync_api._push_service_row(conn, svc, body["id"])
        finally:
            conn.close()
        self.assertFalse(result["ok"])
        self.assertEqual(self.calls, [])


# ------------------------------------------------------------------------------ C13.3


class C13RawHostnamePairIsCheckedBeforeProvidersTests(_Base):
    def test_a_pair_held_by_a_tunnel_service_is_refused_before_any_call(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        conn = models.get_db()
        conn.execute(
            """INSERT INTO services (subdomain, domain, target_ip, target_port, expose_mode,
                                     tunnel_hostname, enabled)
               VALUES ('vault', 'example.com', '10.0.0.5', 80, 'tunnel', 'other.example.org', 1)"""
        )
        conn.commit()
        conn.close()

        with self.assertRaises(HTTPException) as ctx:
            self._create(proxy_provider_id=1, dns_provider_id=2)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.calls, [], "the providers were written before the refusal")

    def test_a_refused_insert_publishes_nothing(self):
        """H2: a concurrent creation can win the race after the pre-check. The row is reserved
        before any provider call, so the loser is refused before it writes anything and never
        withdraws the winner's records (create_host and add_rewrite are idempotent)."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        conn = models.get_db()
        # Stands in for the losing side of the race: the database refuses the row.
        conn.execute(
            """CREATE TRIGGER refuse_service BEFORE INSERT ON services
               BEGIN SELECT RAISE(ABORT, 'UNIQUE constraint failed'); END"""
        )
        conn.commit()
        conn.close()

        with self.assertRaises(HTTPException) as ctx:
            self._create(proxy_provider_id=1, dns_provider_id=2)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.calls, [], "the loser called a provider")

    def test_the_winner_keeps_its_records_when_a_duplicate_loses(self):
        self._add_provider(1, "NPM", "npm")
        dns = self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertTrue(dns.holds(FQDN))
        conn = models.get_db()
        conn.execute(
            """CREATE TRIGGER refuse_service BEFORE INSERT ON services
               BEGIN SELECT RAISE(ABORT, 'UNIQUE constraint failed'); END"""
        )
        conn.commit()
        conn.close()
        self.calls.clear()
        # Bypasses the pre-check, as the losing request of a race would.
        with patch.object(services_api, "_conflicting_service", return_value=None):
            with self.assertRaises(HTTPException):
                self._create(proxy_provider_id=1, dns_provider_id=2)
        self.assertTrue(dns.holds(FQDN), "the winner's DNS record was withdrawn")
        self.assertTrue(self._row(body["id"])["npm_host_id"])
        self.assertNotIn("delete_rewrite", [c[1] for c in self.calls])
        self.assertNotIn("delete_host", [c[1] for c in self.calls])


# -------------------------------------------------------------------------------- C3


class C3DuplicateWarningUsesTheMigrationConnectionTests(_Base):
    def test_the_warning_is_written_inside_the_open_transaction(self):
        conn = models.get_db()
        try:
            conn.execute("DROP INDEX IF EXISTS idx_services_hostname")
            conn.execute("DROP INDEX IF EXISTS idx_services_public_hostname")
            for ip in ("10.0.0.1", "10.0.0.2"):
                conn.execute(
                    "INSERT INTO services (subdomain, domain, target_ip, target_port) "
                    "VALUES ('dup', 'example.com', ?, 80)",
                    (ip,),
                )
            # Uncommitted: this connection holds the write lock, as during `_migrate`.
            with patch.object(models, "get_db", side_effect=AssertionError("second connection")):
                models._ensure_unique_service_hostnames(conn)
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(any("dup.example.com" in m for m in self._messages()))


# ------------------------------------------------------------------------------ C10.5


class C10NpmUpdateKeepsTheOperatorSettingsTests(unittest.TestCase):
    CURRENT = {
        "id": 7,
        "domain_names": ["old.example.com", "alias.example.com"],
        "certificate_id": 4,
        "ssl_forced": True,
        "http2_support": True,
        "hsts_enabled": True,
        "advanced_config": "client_max_body_size 0;",
        "access_list_id": 2,
        "locations": [{"path": "/api", "forward_host": "10.0.0.8", "forward_port": 9000}],
        "meta": {"note": "kept"},
    }

    def _npm(self, get_status: int = 200):
        from unittest.mock import MagicMock

        from app.providers.npm import NPMProvider

        npm = NPMProvider("http://npm:81", "admin@example.com", "pw")
        npm._ensure_auth = MagicMock(return_value=True)
        got = MagicMock(status_code=get_status)
        got.json.return_value = dict(self.CURRENT)
        npm.session.get = MagicMock(return_value=got)
        self.sent = {}

        def _put(_url, json=None, **_kw):
            self.sent.update(json or {})
            return MagicMock(status_code=200)

        npm.session.put = MagicMock(side_effect=_put)
        return npm

    def test_locations_hsts_access_list_and_aliases_survive(self):
        self.assertTrue(self._npm().update_host(7, "new.example.com", "10.0.0.5", 3000))
        for field in ("locations", "hsts_enabled", "advanced_config", "access_list_id", "meta"):
            self.assertEqual(self.sent[field], self.CURRENT[field], field)
        self.assertEqual(self.sent["domain_names"], ["new.example.com", "alias.example.com"])
        self.assertEqual(self.sent["forward_host"], "10.0.0.5")
        self.assertEqual(self.sent["forward_port"], 3000)

    def test_an_attached_certificate_is_not_dropped(self):
        self._npm().update_host(7, "new.example.com", "10.0.0.5", 3000, cert_id=None)
        self.assertEqual(self.sent["certificate_id"], 4)
        self.assertTrue(self.sent["ssl_forced"])

    def test_an_unreadable_host_is_not_overwritten(self):
        """N11: the bare payload reset the operator's settings; the update is now refused."""
        self.assertFalse(self._npm(get_status=500).update_host(7, "new.example.com", "10.0.0.5", 3000))
        self.assertFalse(self.sent, "a PUT was sent without the current host")


# ------------------------------------------------------------------------- C4, C10.1, C10.2


def _tunnel():
    from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

    return CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tid"})


class C4TunnelRenamePublishesBeforeWithdrawingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = _tunnel()
        self.order: list = []
        self.p._upsert_ingress_rule = MagicMock(side_effect=lambda *a: self.order.append("upsert") or True)
        self.p._ensure_dns_record = MagicMock(side_effect=lambda *a: self.order.append("dns") or True)
        self.p._delete_ingress_rule = MagicMock(side_effect=lambda *a: self.order.append("del") or True)
        self.p._delete_dns_record = MagicMock(side_effect=lambda *a: self.order.append("deldns") or True)

    def test_new_route_first_old_one_after(self):
        self.assertTrue(self.p.update_host("old.example.com", "new.example.com", "10.0.0.1", 80))
        self.assertEqual(self.order, ["upsert", "dns", "del", "deldns"])

    def test_failed_publication_keeps_the_old_route(self):
        self.p._upsert_ingress_rule = MagicMock(return_value=False)
        self.assertFalse(self.p.update_host("old.example.com", "new.example.com", "10.0.0.1", 80))
        self.p._delete_ingress_rule.assert_not_called()
        self.p._delete_dns_record.assert_not_called()

    def test_failed_withdrawal_of_the_old_route_is_reported(self):
        self.p._delete_dns_record = MagicMock(return_value=False)
        self.assertFalse(self.p.update_host("old.example.com", "new.example.com", "10.0.0.1", 80))


class C10_1TunnelDnsDeleteNeedsTheTunnelTests(unittest.TestCase):
    def test_without_tunnel_id_nothing_is_deleted(self):
        p = _tunnel()
        p._find_zone = MagicMock(return_value="zone")
        p._resolve_tunnel_id = MagicMock(return_value="")
        p._request = MagicMock()
        self.assertFalse(p._delete_dns_record("app.example.com"))
        p._request.assert_not_called()


class C10_2TunnelIngressKeepsOperatorRulesTests(unittest.TestCase):
    CONFIG = {
        "ingress": [
            {"hostname": "app.example.com", "path": "/admin", "service": "http://admin:9000"},
            {"hostname": "app.example.com", "service": "http://old:80",
             "originRequest": {"noTLSVerify": True}},
            {"service": "http_status:503"},
        ]
    }

    def _upsert(self):
        import copy

        p = _tunnel()
        p._get_configuration = MagicMock(return_value=copy.deepcopy(self.CONFIG))
        sent: list = []
        p._put_configuration = MagicMock(side_effect=lambda c: sent.append(c) or True)
        self.assertTrue(p._upsert_ingress_rule("app.example.com", "http://new:3000"))
        return sent[0]["ingress"]

    def test_path_rule_origin_request_and_fallback_survive(self):
        ingress = self._upsert()
        self.assertIn({"hostname": "app.example.com", "path": "/admin", "service": "http://admin:9000"},
                      ingress)
        main = next(r for r in ingress if r.get("hostname") == "app.example.com" and not r.get("path"))
        self.assertEqual(main["service"], "http://new:3000")
        self.assertEqual(main["originRequest"], {"noTLSVerify": True})
        self.assertEqual(ingress[-1], {"service": "http_status:503"})

    def test_writes_are_serialised_per_tunnel(self):
        self.assertIs(_tunnel()._tunnel_lock(), _tunnel()._tunnel_lock())


# ------------------------------------------------------------------------------- C10.3


class C10_3CloudflareUpdateKeepsTtlAndProxyTests(unittest.TestCase):
    def test_ttl_and_proxied_survive_an_address_change(self):
        from app.providers.cloudflare import CloudflareProvider

        p = CloudflareProvider("", "zone", "tok", {"proxied": False})
        p._client = MagicMock()
        p._client.dns.records.list.return_value = [
            SimpleNamespace(id="r1", name="app.example.com", content="10.0.0.1", ttl=300, proxied=True)
        ]
        self.assertTrue(p.add_rewrite("app.example.com", "10.0.0.2"))
        # A PATCH (records.edit) since M9: `proxied` is left out so Cloudflare keeps it.
        kwargs = p._client.dns.records.edit.call_args.kwargs
        self.assertEqual(kwargs["ttl"], 300)
        self.assertNotIn("proxied", kwargs)
        self.assertEqual(kwargs["content"], "10.0.0.2")


# ------------------------------------------------------------------------------- C10.4


class C10_4PowerDnsKeepsDisabledSiblingsTests(unittest.TestCase):
    RRSET = {
        "name": "app.example.com.", "type": "A", "ttl": 60,
        "records": [
            {"content": "10.0.0.1", "disabled": False},
            {"content": "10.0.0.2", "disabled": True},
        ],
    }

    def _provider(self):
        from app.providers.powerdns import PowerDNSProvider

        p = PowerDNSProvider("http://pdns", "localhost", "key")
        p._find_zone = MagicMock(return_value="example.com.")
        p._find_rrset = MagicMock(return_value=self.RRSET)
        self.sent: list = []
        p._patch = MagicMock(side_effect=lambda _z, rrset: self.sent.append(rrset) or True)
        return p

    def _state(self) -> dict:
        return {r["content"]: r["disabled"] for r in self.sent[0]["records"]}

    def test_adding_a_value_keeps_the_disabled_sibling_disabled(self):
        self.assertTrue(self._provider().add_rewrite("app.example.com", "10.0.0.3"))
        self.assertEqual(self._state(), {"10.0.0.1": False, "10.0.0.2": True, "10.0.0.3": False})

    def test_deleting_a_value_keeps_the_disabled_sibling_disabled(self):
        self.assertTrue(self._provider().delete_rewrite("app.example.com", "10.0.0.1"))
        self.assertEqual(self._state(), {"10.0.0.2": True})

    def test_adding_the_disabled_value_enables_it_alone(self):
        self.assertTrue(self._provider().add_rewrite("app.example.com", "10.0.0.2"))
        self.assertEqual(self._state(), {"10.0.0.1": False, "10.0.0.2": False})


# ---------------------------------------------------------------------------------- C5


class C5HealthReadsTheConnectionTestTests(_Base):
    def test_a_refused_connection_test_is_unhealthy(self):
        from app.api import providers as providers_api

        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self.providers[1].test_connection = lambda: True
        self.providers[2].test_connection = lambda: False
        with patch.object(providers_api, "require_auth", lambda _req, scope=None: None), \
                patch.object(providers_api, "create_provider", self._provider_for):
            result = providers_api.all_providers_health(_request())
        self.assertEqual(result["1"]["status"], "healthy")
        self.assertEqual(result["2"]["status"], "unhealthy")


# ---------------------------------------------------------------------------------- C7


class C7AutoDnsRecordsOnlyWhatWasWrittenTests(_Base):
    NEW = "203.0.113.9"

    def _run(self, dns, current: str = "203.0.113.1"):
        from app import scheduler

        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        conn = models.get_db()
        conn.execute(
            "UPDATE services SET public_target_mode='auto', auto_update_dns=1, dns_ip=? WHERE id=?",
            (current, sid),
        )
        conn.commit()
        scheduler._dns_update_failures.pop(sid, None)
        try:
            with patch.object(scheduler, "load_public_target_policy",
                              lambda _c: {"sources": [], "timeout_seconds": 1}), \
                    patch.object(scheduler, "detect_server_public_ip", lambda **_k: self.NEW), \
                    patch.object(scheduler, "resolve_public_target", lambda *_a, **_k: (self.NEW, "test")), \
                    patch.object(scheduler, "create_provider", lambda _row: dns):
                scheduler._run_dns_auto_updates(conn)
        finally:
            conn.close()
        failures = scheduler._dns_update_failures.pop(sid, 0)
        return self._row(sid), failures

    def test_refused_write_leaves_dns_ip_and_counts_a_failure(self):
        dns = MagicMock()
        dns.update_rewrite.return_value = False
        dns.add_rewrite.return_value = False
        row, failures = self._run(dns)
        self.assertEqual(row["dns_ip"], "203.0.113.1")
        self.assertEqual(failures, 1)

    def test_empty_dns_ip_adds_instead_of_updating_itself(self):
        dns = MagicMock()
        dns.add_rewrite.return_value = True
        row, failures = self._run(dns, current="")
        dns.update_rewrite.assert_not_called()
        dns.add_rewrite.assert_called_once()
        self.assertEqual(row["dns_ip"], self.NEW)
        self.assertEqual(failures, 0)

    def test_fallback_add_removes_the_old_record(self):
        dns = MagicMock()
        dns.update_rewrite.return_value = False
        dns.add_rewrite.return_value = True
        dns.delete_rewrite.return_value = True
        row, _ = self._run(dns)
        dns.delete_rewrite.assert_called_once_with(FQDN, "203.0.113.1")
        self.assertEqual(row["dns_ip"], self.NEW)


# ---------------------------------------------------------------------------------- C8


class C8TagAndEnvironmentNamesTests(unittest.TestCase):
    def test_tag_names_cannot_hold_the_separators(self):
        from pydantic import ValidationError

        from app.api.tags import TagIn

        for bad in ("a:b", "a,b", "x" * 33):
            with self.assertRaises(ValidationError):
                TagIn(name=bad)
        self.assertEqual(TagIn(name=" prod ").name, "prod")

    def test_environment_names_are_checked_too(self):
        from app.api.environments import _clean_name

        for bad in ("a:b", "a,b", "x" * 33, "", 5):
            with self.assertRaises(HTTPException) as ctx:
                _clean_name({"name": bad})
            self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(_clean_name({"name": " staging "}), "staging")


# ---------------------------------------------------------------------------------- C9


class C9ImportsGoThroughTheServiceModelTests(_Base):
    def _services(self) -> list[dict]:
        conn = models.get_db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM services ORDER BY id")]
        finally:
            conn.close()

    def test_sync_import_lowercases_and_refuses_a_bad_target(self):
        self._add_provider(1, "NPM", "npm")
        with patch.object(sync_api, "require_auth_or_setup", lambda _req, scope=None: None):
            result = sync_api.import_services(_request(), sync_api.ServicesImportIn(
                proxy_hosts=[
                    {"domains": ["App.Example.COM."], "host": "10.0.0.3", "port": 8080,
                     "_provider_id": 1},
                    {"domains": ["bad.example.com"], "host": "10.0.0.4", "port": 99999,
                     "_provider_id": 1},
                ],
            ))
        rows = self._services()
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["errors"]), 1, "an out-of-range port was imported")
        self.assertEqual((rows[0]["subdomain"], rows[0]["domain"]), ("app", "example.com"))

    def test_docker_import_refuses_an_invalid_port_or_address(self):
        from app.api import docker as docker_api

        with patch.object(docker_api, "require_auth", lambda _req, scope=None: None):
            result = docker_api.import_docker_containers(_request(), {
                "domain": "example.com",
                "containers": [
                    {"name": "a", "target_ip": "10.0.0.2", "target_port": 70000},
                    {"name": "b", "target_ip": "not a host/../x", "target_port": 80},
                    "not an object",
                ],
            })
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 3)
        self.assertEqual(self._services(), [])

    def test_docker_import_answers_400_for_a_non_numeric_id(self):
        from app.api import docker as docker_api

        with patch.object(docker_api, "require_auth", lambda _req, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                docker_api.import_docker_containers(_request(), {
                    "domain": "example.com", "proxy_provider_id": "abc",
                    "containers": [{"name": "a", "target_ip": "10.0.0.2", "target_port": 80}],
                })
        self.assertEqual(ctx.exception.status_code, 400)


# ------------------------------------------------------------------------------- C10.6


class C10_6BulkFollowsAMovedHostTests(_Base):
    def test_a_stale_hostname_id_is_looked_up_and_written_back(self):
        proxy = self._add_provider(1, "Zoraxy", "zoraxy")
        self._add_provider(2, "AdGuard", "adguard")
        proxy.HOST_ID_IS_HOSTNAME = True
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        sid = body["id"]
        proxy.hosts = [{"id": FQDN, "domains": [FQDN]}]
        conn = models.get_db()
        conn.execute("UPDATE services SET npm_host_id='old.example.com' WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        self.calls.clear()

        self._bulk("disable", sid)

        toggles = [c for c in self.calls if c[1] in ("toggle_host", "delete_host")]
        self.assertTrue(toggles)
        self.assertTrue(all(c[2] == FQDN for c in toggles), toggles)
        self.assertEqual(self._row(sid)["npm_host_id"], FQDN)


# -------------------------------------------------------------------------------- C11


class C11UnreadableListIsNotEmptyTests(_Base):
    def _setup(self):
        proxy = self._add_provider(1, "NPM", "npm")
        dns = self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        self.calls.clear()
        return body["id"], proxy, dns

    def test_unreadable_dns_list_reports_and_adds_nothing(self):
        sid, _proxy, dns = self._setup()
        dns.list_rewrites = lambda: None
        result = sync_api.push_service(sid, _request())
        self.assertFalse(result["ok"])
        self.assertNotIn("add_rewrite", [c[1] for c in self.calls])

    def test_unreadable_host_list_creates_no_second_host(self):
        sid, proxy, _dns = self._setup()
        conn = models.get_db()
        conn.execute("UPDATE services SET npm_host_id=NULL WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        proxy.list_hosts = lambda: None
        result = sync_api.push_service(sid, _request())
        self.assertFalse(result["ok"])
        self.assertNotIn("create_host", [c[1] for c in self.calls])


# -------------------------------------------------------------------------------- C12


class C12TraefikReadsEveryPageAndEveryHostTests(unittest.TestCase):
    def test_pages_are_followed_and_multi_host_rules_read(self):
        from app.providers.traefik import TraefikProvider

        def _resp(data, nxt=None):
            r = MagicMock()
            r.json.return_value = data
            r.headers = {"X-Next-Page": str(nxt)} if nxt else {}
            r.raise_for_status = MagicMock()
            return r

        def _get(url, params=None):
            page = (params or {}).get("page", 1)
            if url.endswith("/routers"):
                if page == 1:
                    return _resp([{"name": "a", "rule": "Host(`a.example.com`, `b.example.com`)",
                                   "service": "s"}], nxt=2)
                return _resp([{"name": "c", "rule": "Host(`c.example.com`)", "service": "s"}])
            return _resp([])

        t = TraefikProvider("http://traefik", "", "")
        t.session.get = MagicMock(side_effect=_get)
        names = {d for h in t.list_hosts() for d in h["domains"]}
        self.assertEqual(names, {"a.example.com", "b.example.com", "c.example.com"})


# ------------------------------------------------------------------------------ C13.4


class C13_4WebhooksFollowTheirCurrentStateTests(_Base):
    def _queue(self, enabled: int, url: str = "mailto://new@example.com") -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'w', ?, ?)", (url, enabled)
        )
        conn.execute(
            """INSERT INTO webhook_delivery_log
               (webhook_id, url, title, body, status, attempt, next_retry_at)
               VALUES (1, 'mailto://old@example.com', 't', 'b', 'pending', 1,
                       '2000-01-01 00:00:00')"""
        )
        conn.commit()
        conn.close()

    def _retry(self) -> list[str]:
        import sys
        import types

        from app import scheduler

        sent: list[str] = []

        class FakeApprise:
            def add(self, url):
                self.url = url
                return True

            def notify(self, title, body):
                sent.append(self.url)
                return True

        conn = models.get_db()
        try:
            with patch.dict(sys.modules, {"apprise": types.SimpleNamespace(Apprise=FakeApprise)}):
                scheduler._run_webhook_retry(conn)
            conn.commit()
        finally:
            conn.close()
        return sent

    def test_a_disabled_webhook_gets_no_retry(self):
        self._queue(enabled=0)
        self.assertEqual(self._retry(), [])

    def test_the_retry_uses_the_current_url(self):
        self._queue(enabled=1)
        self.assertEqual(self._retry(), ["mailto://new@example.com"])

    def test_a_scoped_webhook_only_hears_of_its_services(self):
        from app import scheduler

        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        conn = models.get_db()
        conn.execute(
            """INSERT INTO webhooks (id, name, url, enabled, scope_type, scope_ref_id,
                                     alert_on_any_down)
               VALUES (7, 'scoped', 'mailto://s@example.com', 1, 'service', ?, 1)""",
            (body["id"],),
        )
        conn.commit()
        conn.close()
        sent = []
        with patch.object(scheduler, "_try_send_apprise",
                          lambda url, title, text, **_k: sent.append(text)):
            scheduler._fire_reconcile_webhook(["other.example.com"], ["other.example.com: x"])
            self.assertEqual(sent, [], "a scoped webhook heard of a service outside its scope")
            scheduler._fire_reconcile_webhook([FQDN], [])
        self.assertEqual(len(sent), 1)
        self.assertIn(FQDN, sent[0])


# ------------------------------------------------------------------------------ C13.5


class C13_5NpmExpiryFormatTests(unittest.TestCase):
    def test_the_space_separated_form_is_read(self):
        import datetime

        from app.api.certificates import _parse_expiry

        self.assertEqual(_parse_expiry("2026-01-31 12:00:00"),
                         datetime.datetime(2026, 1, 31, 12, 0, 0))
        self.assertEqual(_parse_expiry("2026-01-31T12:00:00Z"),
                         datetime.datetime(2026, 1, 31, 12, 0, 0))


# ------------------------------------------------------------------------------ C13.6


class C13_6NamesCompareWithoutCaseOrRootDotTests(unittest.TestCase):
    def test_adguard_sees_an_existing_record_spelled_differently(self):
        from app.providers.adguard import AdGuardProvider

        ag = AdGuardProvider("http://ag", "u", "p")
        listing = MagicMock()
        listing.json.return_value = [{"domain": "App.Example.com.", "answer": "10.0.0.1"}]
        listing.raise_for_status = MagicMock()
        ag.session.get = MagicMock(return_value=listing)
        ag.session.post = MagicMock()
        self.assertTrue(ag.add_rewrite("app.example.com", "10.0.0.1"))
        ag.session.post.assert_not_called()

    def test_the_sync_helper_normalises(self):
        self.assertEqual(sync_api._dns_name(" App.Example.COM. "), "app.example.com")


# ------------------------------------------------------------------------------ C13.7


class C13_7BadInputIsAClientErrorTests(_Base):
    def test_a_duplicate_environment_is_409(self):
        from app.api import environments as env_api

        with patch.object(env_api, "require_auth", lambda _req, scope=None: None):
            env_api.add_environment(_request(), {"name": "prod"})
            other = env_api.add_environment(_request(), {"name": "staging"})
            with self.assertRaises(HTTPException) as add_ctx:
                env_api.add_environment(_request(), {"name": "prod"})
            with self.assertRaises(HTTPException) as put_ctx:
                env_api.update_environment(other["id"], _request(), {"name": "prod"})
            with self.assertRaises(HTTPException) as missing_ctx:
                env_api.update_environment(999, _request(), {"name": "x"})
        self.assertEqual(add_ctx.exception.status_code, 409)
        self.assertEqual(put_ctx.exception.status_code, 409)
        self.assertEqual(missing_ctx.exception.status_code, 404)

    def test_an_unknown_webhook_is_400_and_keeps_the_alerts(self):
        from app.api import webhooks as webhooks_api

        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        from pydantic import ValidationError

        alerts_in = webhooks_api.ServiceAlertsIn.model_validate
        with patch.object(webhooks_api, "require_auth", lambda _req, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                webhooks_api.set_service_alerts(
                    body["id"], _request(), alerts_in({"alerts": [{"webhook_id": 999}]})
                )
            self.assertEqual(ctx.exception.status_code, 400)
            # A body of the wrong shape is refused by the model now (a 422 over HTTP).
            for alerts in ([{"webhook_id": None, "min_down_minutes": "x"}], "nope"):
                with self.assertRaises(ValidationError):
                    alerts_in({"alerts": alerts})
            with self.assertRaises(HTTPException) as ctx:
                webhooks_api.set_service_alerts(999, _request(), alerts_in({"alerts": []}))
            self.assertEqual(ctx.exception.status_code, 404)


# ------------------------------------------------------------------------------ C13.8


class C13_8PasswordSpacesTests(unittest.TestCase):
    def test_a_trailing_space_matches_the_stripped_hash(self):
        from app.api import auth as auth_api

        with patch.object(auth_api, "check_password", lambda p: p == "secret"):
            self.assertTrue(auth_api._password_matches(" secret "))
            self.assertFalse(auth_api._password_matches("other"))

    def test_an_old_unstripped_hash_still_matches(self):
        from app.api import auth as auth_api

        with patch.object(auth_api, "check_password", lambda p: p == " legacy "):
            self.assertTrue(auth_api._password_matches(" legacy "))


# -------------------------------------------------------------------------------- P3


class P3AutoReconcileRereadsAndLocksTests(_Base):
    def test_a_service_disabled_mid_run_is_not_pushed(self):
        from app import scheduler

        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, first = self._create(proxy_provider_id=1, dns_provider_id=2)
        _, second = self._create(proxy_provider_id=1, dns_provider_id=2, subdomain="other")
        conn = models.get_db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')")
        conn.commit()
        conn.close()

        pushed: list = []

        def _drift(conn, svc, sid):
            if sid == first["id"]:
                # The operator disables the second service while the first is examined.
                c = models.get_db()
                c.execute("UPDATE services SET enabled=0 WHERE id=?", (second["id"],))
                c.commit()
                c.close()
            return {"ok": False}

        def _push(svc, sid):
            pushed.append((sid, scheduler._lock.locked()))
            return {"ok": True}

        with patch.object(sync_api, "_compute_service_drift", _drift), \
                patch.object(sync_api, "_execute_push", _push), \
                patch.object(scheduler, "_fire_reconcile_webhook", lambda *_a: None):
            scheduler.run_auto_reconcile()

        self.assertEqual([sid for sid, _ in pushed], [first["id"]])
        self.assertTrue(all(locked for _, locked in pushed), "pushed outside the scheduler lock")


# ------------------------------------------------------------------------------- P4.1


class P4_1ApiKeyStampIsAHintTests(_Base):
    KEY = "vx_test-key-for-p4"

    def _insert_key(self, last_used: str | None) -> None:
        from app.api import api_keys

        # The table comes from `init_db` now (T4), not from a per-call `_ensure_table`.
        conn = models.get_db()
        conn.execute(
            """INSERT INTO api_keys (name, key_hash, prefix, scopes, last_used_at)
               VALUES ('k', ?, 'vx_test', 'read', ?)""",
            (api_keys._hash_key(self.KEY), last_used),
        )
        conn.commit()
        conn.close()

    def _stamp(self) -> str | None:
        conn = models.get_db()
        try:
            return conn.execute("SELECT last_used_at FROM api_keys").fetchone()[0]
        finally:
            conn.close()

    def test_a_recent_stamp_is_not_rewritten(self):
        from app.api import api_keys

        self._insert_key("2999-01-01 00:00:00")
        self.assertIsNotNone(api_keys.verify_api_key(self.KEY))
        self.assertEqual(self._stamp(), "2999-01-01 00:00:00")

    def test_an_old_stamp_is_refreshed(self):
        from app.api import api_keys

        self._insert_key("2000-01-01 00:00:00")
        api_keys.verify_api_key(self.KEY)
        self.assertNotEqual(self._stamp(), "2000-01-01 00:00:00")

    def test_a_refused_write_still_accepts_the_key(self):
        import sqlite3

        from app.api import api_keys

        self._insert_key(None)

        class _ReadOnly:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                if sql.lstrip().upper().startswith("UPDATE"):
                    raise sqlite3.OperationalError("database is locked")
                return self._conn.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        with patch.object(api_keys, "get_db", lambda: _ReadOnly(models.get_db())):
            info = api_keys.verify_api_key(self.KEY)
        self.assertIsNotNone(info, "a valid key was refused because a timestamp could not be written")


# ------------------------------------------------------------------------------- P4.2


class P4_2OneSettingsReadPerRequestTests(_Base):
    def test_the_auth_context_reads_the_settings_once(self):
        from app import auth

        real = auth._read_auth_settings
        calls: list = []

        def _counting():
            calls.append(1)
            return real()

        conn = models.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', 'x')"
        )
        conn.commit()
        conn.close()
        request = SimpleNamespace(session={"authenticated": True, "epoch": 0},
                                  headers={})
        with patch.object(auth, "APP_PASSWORD", ""), \
                patch.object(auth, "_read_auth_settings", _counting):
            ctx = auth._get_auth_context(request)
        self.assertEqual(ctx["kind"], "session")
        self.assertEqual(len(calls), 1)

    def test_an_unreadable_database_still_fails_closed(self):
        import sqlite3

        from app import auth

        request = SimpleNamespace(session={"authenticated": True, "epoch": 0}, headers={})
        with patch.object(auth, "APP_PASSWORD", "pbkdf2:whatever"), \
                patch.object(auth, "_read_auth_settings", side_effect=sqlite3.Error("locked")):
            self.assertIsNone(auth._get_auth_context(request))


# ------------------------------------------------------------------------------- P4.6


class P4_6BulkIdsAreBoundedAndUniqueTests(_Base):
    def test_ids_are_deduplicated_and_bounded(self):
        from pydantic import ValidationError

        body = services_api._BulkActionBody(ids=[3, 3, 1, 3], action="disable")
        self.assertEqual(body.ids, [3, 1])
        with self.assertRaises(ValidationError):
            services_api._BulkActionBody(ids=list(range(501)), action="disable")

    def test_a_repeated_id_counts_once(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        _, body = self._create(proxy_provider_id=1, dns_provider_id=2)
        result = self._bulk("disable", body["id"], body["id"], body["id"])
        self.assertEqual(result["affected"], 1)


if __name__ == "__main__":
    unittest.main()
