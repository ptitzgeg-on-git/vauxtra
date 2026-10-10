"""Regression tests for the third review pass (C1 to C9, N1 to N31, I1 to I9).

Each class names the finding it covers. The isolated-database fixtures come from
`test_multi_sync_targets.py`; `tests/` is not a package, so they are loaded by path.
"""

import importlib.util
import os
import unittest
from unittest.mock import MagicMock, patch

from app.providers.desec import DesecProvider
from app.providers.powerdns import PowerDNSProvider

_ms_spec = importlib.util.spec_from_file_location(
    "_multisync_fixtures_third", os.path.join(os.path.dirname(__file__), "test_multi_sync_targets.py")
)
_ms = importlib.util.module_from_spec(_ms_spec)
_ms_spec.loader.exec_module(_ms)


def _response(status_code: int = 200, json_data=None, headers=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = {} if json_data is None else json_data
    r.links = {}
    r.headers = headers if headers is not None else {}
    return r


# -------------------------------------------------------------------------------- C2


class C2PowerDNSLookupReadsOneZoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = PowerDNSProvider("http://pdns:8081", "", "key")
        self.read: list[str] = []

        def get(url, **_kw):
            if url.endswith("/zones"):
                return _response(200, [{"id": "example.com.", "name": "example.com."},
                                       {"id": "locked.org.", "name": "locked.org."}])
            self.read.append(url)
            if url.endswith("/locked.org."):
                return _response(403)
            return _response(200, {"rrsets": [
                {"name": "app.example.com.", "type": "A", "records": [{"content": "10.0.0.1"}]},
                {"name": "other.example.com.", "type": "A", "records": [{"content": "10.0.0.2"}]},
            ]})

        self.p.session.get = MagicMock(side_effect=get)

    def test_an_unreadable_unrelated_zone_does_not_block_the_name(self):
        self.assertIsNone(self.p.list_rewrites())
        self.assertEqual(
            self.p.lookup_rewrites("App.example.com"),
            [{"domain": "app.example.com", "answer": "10.0.0.1", "type": "A"}],
        )

    def test_a_name_outside_every_zone_has_no_record(self):
        self.assertEqual(self.p.lookup_rewrites("app.elsewhere.net"), [])

    def test_the_zone_of_the_name_unreadable_is_unknown(self):
        self.assertIsNone(self.p.lookup_rewrites("x.locked.org"))


class C2DesecLookupReadsOneNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = DesecProvider("", "", "tok")
        self.p._list_domains = MagicMock(return_value=[{"name": "example.com"}, {"name": "slow.net"}])

    def _get(self, by_type: dict):
        def get(url, **_kw):
            for rtype, resp in by_type.items():
                if url.endswith(f"/{rtype}/"):
                    return resp
            return _response(404)
        self.p.session.get = MagicMock(side_effect=get)

    def test_only_the_rrsets_of_the_name_are_read(self):
        self._get({"A": _response(200, {"records": ["10.0.0.1"]})})
        self.assertEqual(
            self.p.lookup_rewrites("app.example.com"),
            [{"domain": "app.example.com", "answer": "10.0.0.1", "type": "A"}],
        )
        urls = [c.args[0] for c in self.p.session.get.call_args_list]
        self.assertTrue(all("/domains/example.com/rrsets/app/" in u for u in urls), urls)

    def test_a_refused_rrset_is_unknown(self):
        self._get({"AAAA": _response(500)})
        self.assertIsNone(self.p.lookup_rewrites("app.example.com"))


class C2PushUsesTheTargetedLookupTests(_ms._MultiSyncTestCase):
    def test_the_push_asks_for_the_name_only(self):
        from app.api import sync as sync_api
        from app.providers.base import DNSProvider

        class _Dns(DNSProvider):
            def __init__(self):
                self.asked = []

            def test_connection(self):
                return True

            def list_rewrites(self):
                raise AssertionError("the full list must not be read")

            def lookup_rewrites(self, name):
                self.asked.append(name)
                return []

            def add_rewrite(self, domain, ip):
                return True

            def delete_rewrite(self, domain, ip):
                return True

            def update_rewrite(self, old_domain, old_ip, domain, ip):
                return True

        dns = _Dns()
        self.assertEqual(sync_api._records_of_name(dns, "Vault.example.com"), [])
        self.assertEqual(dns.asked, ["Vault.example.com"])


# -------------------------------------------------------------------------------- C7


class C7CloudflareDnsPartialListIsUnknownTests(unittest.TestCase):
    def _provider(self):
        from app.providers import cloudflare as cf_module

        p = cf_module.CloudflareProvider.__new__(cf_module.CloudflareProvider)
        p._configured_zone_id = "zone1"
        p._client = MagicMock()
        return p

    def test_a_failed_page_makes_the_list_unknown(self):
        p = self._provider()
        record = MagicMock(content="1.2.3.4", proxied=False)
        record.name = "a.example.com"

        def records(zone_id, type):
            if type == "AAAA":
                raise RuntimeError("429")
            return [record] if type == "A" else []

        p._client.dns.records.list.side_effect = records
        self.assertIsNone(p.list_rewrites())

    def test_a_complete_read_is_still_a_list(self):
        p = self._provider()
        p._client.dns.records.list.return_value = []
        self.assertEqual(p.list_rewrites(), [])


# -------------------------------------------------------------------------------- I2


class I2RetryAfterNaNIsNotHonouredTests(unittest.TestCase):
    def test_a_nan_wait_stops_the_retries(self):
        p = DesecProvider("", "", "tok")
        p.session.get = MagicMock(return_value=_response(429, headers={"Retry-After": "nan"}))
        with patch("app.providers.desec.time.sleep") as sleep:
            r = p._get("https://desec.io/api/v1/domains/")
        self.assertEqual(r.status_code, 429)
        sleep.assert_not_called()
        self.assertEqual(p.session.get.call_count, 1)


# ------------------------------------------------------------------- C5, N22, N23


class _Imports(_ms._MultiSyncTestCase):
    def _import(self, proxy_hosts=(), dns_rewrites=()):
        with patch.object(_ms.sync_api, "require_auth_or_setup", lambda _req, scope=None: None):
            return _ms.sync_api.import_services(
                _ms._request(),
                _ms.sync_api.ServicesImportIn(proxy_hosts=list(proxy_hosts), dns_rewrites=list(dns_rewrites)),
            )

    def _rows(self, sql="SELECT subdomain, domain, target_port, dns_provider_id, dns_ip FROM services"):
        conn = _ms.models.get_db()
        try:
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()


class C5RefusedProxyHostDoesNotComeBackAsDnsOnlyTests(_Imports):
    def test_a_refused_route_leaves_no_port_80_service(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        result = self._import(
            proxy_hosts=[{"id": 999, "domains": ["app.example.com"], "host": "10.0.0.3", "_provider_id": 1}],
            dns_rewrites=[{"domain": "app.example.com", "answer": "10.0.0.3", "_provider_id": 2}],
        )
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self._rows(), [])


class N23ImportChecksTheProviderRolesTests(_Imports):
    def test_an_unknown_or_dns_provider_is_not_a_proxy(self):
        self._add_provider(2, "AdGuard", "adguard")
        result = self._import(proxy_hosts=[
            {"domains": ["a.example.com"], "host": "10.0.0.3", "_provider_id": 42},
            {"domains": ["b.example.com"], "host": "10.0.0.3", "_provider_id": 2},
        ])
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(self._rows(), [])

    def test_an_existing_record_on_another_provider_is_not_overwritten(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Pi-hole", "pihole")
        self._create(proxy_provider_id=1, dns_provider_id=2)
        result = self._import(dns_rewrites=[
            {"domain": "vault.example.com", "answer": "10.9.9.9", "_provider_id": 3},
        ])
        self.assertEqual(len(result["errors"]), 1)
        row = self._rows()[0]
        self.assertEqual((row["dns_provider_id"], row["dns_ip"]), (2, self.IP))


class N22SyncComparesRouteIdsPerProviderTests(_Imports):
    def _sync(self):
        with patch.object(_ms.sync_api, "require_auth_or_setup", lambda _req, scope=None: None):
            return _ms.sync_api.sync_services(_ms._request())

    def test_the_same_id_on_another_proxy_is_not_imported_yet(self):
        first = self._add_provider(1, "NPM-1", "npm")
        second = self._add_provider(2, "NPM-2", "npm")
        self._create(proxy_provider_id=1, dns_ip="")
        bound = self._rows("SELECT npm_host_id FROM services")[0]["npm_host_id"]
        first.hosts = [{"id": bound, "domains": ["vault.example.com"], "host": "10.0.0.9", "port": 8080}]
        second.hosts = [{"id": bound, "domains": ["other.example.com"], "host": "10.0.0.8", "port": 80}]
        flags = {(h["_provider_id"], h["domains"][0]): h["_already_imported"] for h in self._sync()["proxy_hosts"]}
        self.assertEqual(flags, {(1, "vault.example.com"): True, (2, "other.example.com"): False})

    def test_a_name_with_capitals_and_a_root_dot_is_already_imported(self):
        proxy = self._add_provider(1, "NPM", "npm")
        self._create(proxy_provider_id=1, dns_ip="")
        proxy.hosts = [{"id": 5000, "domains": ["Vault.Example.com."], "host": "10.0.0.9", "port": 8080}]
        self.assertTrue(self._sync()["proxy_hosts"][0]["_already_imported"])


# ----------------------------------------------------------------------- C9, N3, N9

_sec_spec = importlib.util.spec_from_file_location(
    "_security_fixtures_third", os.path.join(os.path.dirname(__file__), "test_review_security_fixes.py")
)
_sec = importlib.util.module_from_spec(_sec_spec)
_sec_spec.loader.exec_module(_sec)


class C9LabelsWithSeparatorsAreRenamedAtStartupTests(_sec._IsolatedDB):
    def test_the_migration_renames_without_collision(self):
        self._exec("INSERT INTO tags (id, name) VALUES (1, 'prod:eu'), (2, 'prod-eu')")
        self._exec("INSERT INTO environments (id, name) VALUES (5, 'a,b')")
        _sec.models.init_db()
        conn = _sec.models.get_db()
        try:
            tags = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM tags")}
            envs = [r["name"] for r in conn.execute("SELECT name FROM environments")]
        finally:
            conn.close()
        self.assertEqual(tags, {1: "prod-eu-1", 2: "prod-eu"})
        self.assertEqual(envs, ["a-b"])


class C9N3N9RestoreTests(_sec._WithKeys):
    def _backup(self) -> dict:
        return self.client.get("/api/backup", headers=self._h("adm")).json()

    def _restore(self, backup: dict):
        return self.client.post("/api/restore", headers=self._h("adm"), json={"backup": backup})

    def _names(self, table: str) -> list[str]:
        conn = _sec.models.get_db()
        try:
            return sorted(r[0] for r in conn.execute(f"SELECT name FROM {table}"))  # noqa: S608
        finally:
            conn.close()

    def test_environment_names_are_renamed_or_left_out(self):
        backup = self._backup()
        backup["environments"] = [
            {"id": 1, "name": "eu,west"},
            {"id": 2, "name": "   "},
            {"id": 3, "name": "x" * 40},
        ]
        backup["services"] = [{"id": 7, "subdomain": "a", "domain": "example.com",
                               "target_ip": "10.0.0.1", "target_port": 80}]
        backup["service_environments"] = [{"service_id": 7, "environment_id": 2}]
        r = self._restore(backup)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._names("environments"), ["eu-west"])
        self.assertEqual(len(r.json()["skipped"]), 2)

    def test_an_invalid_template_is_left_out(self):
        backup = self._backup()
        backup["service_templates"] = [
            {"id": 1, "name": "ok", "forward_scheme": "http"},
            {"id": 2, "name": "bad", "forward_scheme": "gopher"},
        ]
        r = self._restore(backup)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._names("service_templates"), ["ok"])

    def test_an_entry_that_is_not_an_object_is_a_400(self):
        for section, value in (("services", ["x"]), ("tags", [1]), ("webhooks", "nope")):
            with self.subTest(section=section):
                backup = self._backup()
                backup[section] = value
                self.assertEqual(self._restore(backup).status_code, 400)

    def test_an_undecodable_salt_is_a_400(self):
        backup = self._backup()
        backup.update(secrets_included=True, encryption_salt="a")  # bad padding
        r = self.client.post("/api/restore", headers=self._h("adm"),
                             json={"backup": backup, "passphrase": "x"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("salt", r.json()["detail"])


class N16ExportsLeaveTheProtectedSettingsOutTests(_sec._WithKeys):
    def test_neither_export_carries_a_protected_key(self):
        from app.api.settings import _PROTECTED_SETTINGS

        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_env_fingerprint', 'f')")
        plain = self.client.get("/api/backup", headers=self._h("adm")).json()
        secure = self.client.post("/api/backup/secure", headers=self._h("adm"),
                                  json={"passphrase": "Correct-Horse-Battery-9"})
        self.assertEqual(secure.status_code, 200, secure.text)
        for name, backup in (("plain", plain), ("secure", secure.json())):
            with self.subTest(export=name):
                keys = {s["key"] for s in backup["settings"]}
                self.assertFalse(keys & set(_PROTECTED_SETTINGS), keys)


# -------------------------------------------------------------------------------- N2, N5


class N2WebhookLocalTargetsAreSeenThroughTheirSpellingsTests(unittest.TestCase):
    def test_numeric_spellings_of_loopback_are_local(self):
        from app.api import webhooks

        for url in ("ntfy://127.1/topic", "ntfy://2130706433/topic", "ntfy://0x7f.1/topic",
                    "gotify://[::ffff:127.0.0.1]/token", "ntfy://169.254.169.254/t"):
            with self.subTest(url=url):
                self.assertTrue(webhooks._targets_the_local_network(url))

    def test_a_public_name_resolving_to_a_private_address_is_local(self):
        from app import webhook_targets
        from app.api import webhooks

        with patch.object(webhook_targets, "_resolve", return_value=["10.1.2.3"]):
            self.assertTrue(webhooks._targets_the_local_network("ntfy://ntfy.example.com/t"))
        with patch.object(webhook_targets, "_resolve", return_value=["93.184.216.34"]):
            self.assertFalse(webhooks._targets_the_local_network("ntfy://ntfy.example.com/t"))

    def test_a_dotless_host_part_that_resolves_nowhere_is_not_local(self):
        from app import webhook_targets
        from app.api import webhooks

        # M10 resolves dotless names too; a Discord id or a bot token resolves to nothing.
        with patch.object(webhook_targets, "_resolve", return_value=[]):
            self.assertFalse(webhooks._targets_the_local_network("discord://112233445566778899/abcdef"))
            self.assertFalse(webhooks._targets_the_local_network("tgram://bottoken/chat"))


class N2N5WebhookRoutesTests(_sec._WithKeys):
    def test_a_write_key_cannot_store_a_numeric_loopback_target(self):
        r = self.client.post("/api/webhooks", headers=self._h("rw"),
                             json={"name": "x", "url": "ntfy://127.1/topic"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_a_legacy_multi_url_webhook_can_still_be_switched_off(self):
        self._exec("INSERT INTO webhooks (id, name, url, enabled) VALUES "
                   "(7, 'legacy', 'discord://1/a,discord://2/b', 1)")
        r = self.client.put("/api/webhooks/7", headers=self._h("rw"), json={"enabled": False})
        self.assertEqual(r.status_code, 200, r.text)
        conn = _sec.models.get_db()
        try:
            row = conn.execute("SELECT enabled, url FROM webhooks WHERE id=7").fetchone()
        finally:
            conn.close()
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["url"], "discord://1/a,discord://2/b")

    def test_a_supplied_url_is_still_validated(self):
        self._exec("INSERT INTO webhooks (id, name, url, enabled) VALUES (8, 'w', 'discord://1/a', 1)")
        r = self.client.put("/api/webhooks/8", headers=self._h("rw"),
                            json={"url": "discord://1/a discord://2/b"})
        self.assertEqual(r.status_code, 400, r.text)


# -------------------------------------------------------------------------------- N4


class N4SettingsTestWebhookTests(_sec._WithKeys):
    def test_the_exception_text_is_not_echoed(self):
        self._exec("INSERT INTO webhooks (id, name, url, enabled) VALUES (3, 'w', 'discord://1/a', 1)")

        class _Boom:
            def add(self, _url):
                raise RuntimeError("connection to http://10.0.0.5:8080 refused, token=abc")

        fake = MagicMock()
        fake.Apprise = _Boom
        with patch.dict("sys.modules", {"apprise": fake}):
            r = self.client.post("/api/settings/test-webhook", headers=self._h("rw"))
        self.assertEqual(r.status_code, 200, r.text)
        error = r.json()["results"][0]["error"]
        self.assertNotIn("10.0.0.5", error)
        self.assertNotIn("token", error)

    def test_the_route_is_rate_limited(self):
        from app.limiter import limiter

        limits = limiter._route_limits.get("app.api.settings.test_webhook") or []
        self.assertTrue(any("10 per 1 minute" in str(lim.limit) for lim in limits), limits)


# -------------------------------------------------------------------------------- N7


class N7LogStreamSlotsTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.api import settings as settings_api

        self.s = settings_api
        self._patchers = [
            patch.object(settings_api, "_open_log_streams", 0),
            patch.object(settings_api, "_log_streams_by_identity", {}),
            patch.object(settings_api, "require_auth", lambda request, scope="read": None),
            patch.object(settings_api, "_HAS_SSE", True),
            patch.object(settings_api, "_SSEResponse", lambda gen: gen),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()

    def _open(self, identity: str, admin: bool = False):
        import asyncio

        class _Req:
            async def is_disconnected(self):
                return True

        with patch.object(self.s, "_stream_identity", lambda _r: (identity, admin)):
            return asyncio.run(self.s.stream_logs(_Req()))

    def test_a_burst_is_counted_before_any_stream_starts(self):
        from fastapi import HTTPException

        held = [self._open(f"key:{i}") for i in range(self.s._MAX_LOG_STREAMS)]
        with self.assertRaises(HTTPException) as ctx:
            self._open("key:99")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(len(held), self.s._MAX_LOG_STREAMS)

    def test_one_identity_cannot_take_every_slot(self):
        from fastapi import HTTPException

        held = [self._open("key:1") for _ in range(self.s._MAX_LOG_STREAMS_PER_IDENTITY)]
        with self.assertRaises(HTTPException):
            self._open("key:1")
        held.append(self._open("key:2"))
        self.assertEqual(len(held), self.s._MAX_LOG_STREAMS_PER_IDENTITY + 1)

    def test_an_admin_is_not_locked_out_by_lower_keys(self):
        held = [self._open(f"key:{i}") for i in range(self.s._MAX_LOG_STREAMS)]
        held.append(self._open("session", admin=True))
        self.assertEqual(len(held), self.s._MAX_LOG_STREAMS + 1)

    def test_a_stream_never_started_gives_its_slot_back(self):
        import gc

        class _Req:
            async def is_disconnected(self):
                return True

        async def _inline(fn, *args):
            return fn(*args)

        # Driven by hand: `asyncio.run` keeps the coroutine result referenced after it returns.
        with patch.object(self.s, "_stream_identity", lambda _r: ("key:1", False)), \
                patch.object(self.s, "run_in_threadpool", _inline):
            coro = self.s.stream_logs(_Req())
            try:
                coro.send(None)
            except StopIteration as done:
                gen = done.value
            del coro
        self.assertEqual(self.s._open_log_streams, 1)
        del gen
        gc.collect()
        self.assertEqual(self.s._open_log_streams, 0)
        self.assertEqual(self.s._log_streams_by_identity, {})


# -------------------------------------------------------------------------------- N12, N13


class N12N13ProviderUrlGuardTests(unittest.TestCase):
    def test_query_fragment_and_userinfo_are_refused(self):
        from app.providers.base import validate_provider_url

        for url in ("http://npm:81/?x=/admin", "http://npm:81/#frag", "http://user@npm:81",
                    "http://user:pw@npm:81/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_provider_url(url)
        self.assertEqual(validate_provider_url("http://npm:81/base"), "http://npm:81/base")

    def test_the_gcp_ipv6_metadata_address_is_blocked(self):
        from app.providers.base import is_blocked_host, validate_provider_url

        self.assertTrue(is_blocked_host("fd20:ce::254"))
        with self.assertRaises(ValueError):
            validate_provider_url("http://[fd20:ce::254]/")


# -------------------------------------------------------------------------------- N6


class N6CaBundleIsNotAFileOracleTests(_sec._WithKeys):
    def _create(self, key: str, path: str):
        return self.client.post(
            "/api/providers", headers=self._h(key),
            json={"name": "P", "type": "npm", "url": "https://npm.example.com", "username": "a",
                  "password": "b", "extra": {"ca_bundle": path}},
        )

    def test_a_write_key_gets_the_same_answer_for_any_path(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
            present = fh.name
        try:
            missing = present + ".missing"
            a, b = self._create("rw", present), self._create("rw", missing)
        finally:
            os.unlink(present)
        self.assertEqual((a.status_code, b.status_code), (403, 403), (a.text, b.text))
        self.assertEqual(a.json(), b.json())

    def test_an_admin_still_learns_the_path_is_wrong(self):
        r = self._create("adm", os.path.join(os.sep, "vauxtra-no-such-dir", "ca.pem"))
        self.assertEqual(r.status_code, 400, r.text)

    def test_an_unchanged_bundle_does_not_ask_for_admin(self):
        import json as _json
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
            path = fh.name
        try:
            self._exec("UPDATE providers SET extra=? WHERE id=1", (_json.dumps({"ca_bundle": path}),))
            r = self.client.put("/api/providers/1", headers=self._h("rw"),
                                json={"extra": {"ca_bundle": path, "proxied": True}})
        finally:
            os.unlink(path)
        self.assertEqual(r.status_code, 200, r.text)


# -------------------------------------------------------------------------------- N8


class N8RebindingCheckRunsOffTheLoopTests(_sec._WithKeys):
    def test_the_password_lookup_is_not_on_the_event_loop(self):
        import asyncio

        on_loop = []

        def _probe():
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
            return True

        with patch("app.auth.has_password_configured", _probe):
            self.client.get("/api/health", headers={"Host": "vauxtra.example.com"})
        self.assertEqual(on_loop[:1], [False])


# -------------------------------------------------------------------------------- N14


class N14UnexpectedShapesAreUnreadableTests(unittest.TestCase):
    def test_pihole_v6_answering_a_list_is_unknown(self):
        from app.providers.pihole import PiholeProvider

        p = PiholeProvider("http://pihole", "", "pw")
        p._version = 6
        p.session.get = MagicMock(return_value=_response(200, ["not", "an", "object"]))
        self.assertIsNone(p._list_rewrites_inner())

    def test_pihole_v5_rows_of_the_wrong_shape_are_unknown(self):
        from app.providers.pihole import PiholeProvider

        p = PiholeProvider("http://pihole", "", "pw")
        p._version = 5
        p.session.get = MagicMock(return_value=_response(200, {"data": [5, 6]}))
        self.assertIsNone(p._list_rewrites_inner())

    def test_traefik_answering_an_object_is_unknown_not_empty(self):
        from app.providers.traefik import TraefikProvider

        p = TraefikProvider("http://traefik:8080", "", "")
        p.session.get = MagicMock(return_value=_response(200, {"message": "error page"}))
        self.assertIsNone(p.list_hosts())

    def test_traefik_empty_list_is_still_empty(self):
        from app.providers.traefik import TraefikProvider

        p = TraefikProvider("http://traefik:8080", "", "")
        p.session.get = MagicMock(return_value=_response(200, []))
        self.assertEqual(p.list_hosts(), [])


# -------------------------------------------------------------------------------- N15


class N15PreflightDoesNotEchoTheSocketErrorTests(unittest.TestCase):
    def test_the_reason_is_generic(self):
        from app.api import services

        for exc in (ConnectionRefusedError(111, "Connection refused"), TimeoutError("timed out"),
                    OSError(-2, "Name or service not known")):
            with self.subTest(exc=type(exc).__name__), \
                    patch.object(services.socket, "create_connection", side_effect=exc):
                self.assertEqual(services._service_target_reachable("10.0.0.9", 22),
                                 (False, "connection failed"))


# -------------------------------------------------------------------------------- N17, N18


class N17N18RouteScopesTests(_sec._WithKeys):
    def setUp(self) -> None:
        super().setUp()
        self._exec("""INSERT INTO services (id, subdomain, domain, target_ip, target_port)
                      VALUES (1, 'app', 'example.com', '127.0.0.1', 9)""")
        self._exec("""INSERT INTO docker_endpoints (id, name, docker_host, enabled, is_default)
                      VALUES (50, 'remote', 'tcp://10.0.0.5:2375', 1, 0)""")

    def test_the_single_check_is_no_longer_a_get(self):
        r = self.client.get("/api/services/1/check", headers=self._h("rw"))
        self.assertIn(r.status_code, (404, 405), r.text)
        with patch("app.api.services._service_target_reachable", return_value=(False, "connection failed")):
            r = self.client.post("/api/services/1/check", headers=self._h("rw"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_docker_endpoint_routes_need_admin(self):
        for method, path in (("post", "/api/docker/endpoints/50/default"),
                             ("post", "/api/docker/endpoints/50/test"),
                             ("delete", "/api/docker/endpoints/50")):
            with self.subTest(path=path, method=method):
                r = getattr(self.client, method)(path, headers=self._h("rw"))
                self.assertEqual(r.status_code, 403, r.text)
        r = self.client.post("/api/docker/endpoints/50/default", headers=self._h("adm"))
        self.assertEqual(r.status_code, 200, r.text)


# -------------------------------------------------------------------------------- N19


class N19ImageCodeIsNotOwnedByTheRuntimeUserTests(unittest.TestCase):
    def test_only_the_data_directory_is_chowned(self):
        path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(path, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if "chown" in ln and not ln.lstrip().startswith("#")]
        self.assertTrue(lines)
        for line in lines:
            self.assertRegex(line, r"chown -R appuser:appuser /app/data$")


# -------------------------------------------------------------------------------- N10, N30, N31


class N10N30N31DocsFollowTheCodeTests(unittest.TestCase):
    _ROOT = os.path.join(os.path.dirname(__file__), "..")

    def _read(self, *parts: str) -> str:
        with open(os.path.join(self._ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_python_version_is_the_image_one_everywhere(self):
        import re

        image = re.search(r"FROM python:(\d+)\.(\d+)", self._read("Dockerfile")).groups()
        self.assertIn(f"badge/Python-{image[0]}.{image[1]}-", self._read("README.md"))
        self.assertIn(f'target-version = "py{image[0]}{image[1]}"', self._read("ruff.toml"))

    def test_the_mcp_http_url_ends_in_mcp_and_the_421_is_said(self):
        readme = self._read("vauxtra_mcp", "README.md")
        self.assertIn("http://127.0.0.1:9000/mcp", readme)
        self.assertNotIn("client at `http://127.0.0.1:9000`", readme)
        self.assertIn("421", readme)
        self.assertIn("clear_logs", readme.split("\n## ")[1])
        howto = self._read("docs", "HOWTO.md")
        scrape = howto[howto.index("### Prometheus scrape config"):howto.index("### Alerting examples")]
        self.assertIn("421", scrape)


# -------------------------------------------------------------------------------- I3


class I3McpGuardTests(unittest.TestCase):
    def _run(self, scope_type: str, headers, token: str = "tok"):
        import asyncio

        from vauxtra_mcp import server as mcp_server

        reached, sent = [], []

        async def _app(scope, receive, send):
            reached.append(True)

        async def _send(message):
            sent.append(message)

        async def _receive():
            return {"type": "websocket.connect"}

        guard = mcp_server._HttpGuard(_app, "127.0.0.1", token)
        asyncio.run(guard({"type": scope_type, "headers": headers}, _receive, _send))
        return reached, sent

    def test_the_401_names_the_bearer_scheme(self):
        _reached, sent = self._run("http", [(b"host", b"127.0.0.1")])
        self.assertEqual(sent[0]["status"], 401)
        self.assertIn((b"www-authenticate", b'Bearer realm="vauxtra-mcp"'), sent[0]["headers"])

    def test_a_websocket_handshake_is_checked_too(self):
        reached, sent = self._run("websocket", [(b"host", b"127.0.0.1")])
        self.assertEqual((reached, sent[0]["type"]), ([], "websocket.close"))
        reached, sent = self._run("websocket", [(b"host", b"evil.example")], token="")
        self.assertEqual((reached, sent[0]["type"]), ([], "websocket.close"))
        reached, _sent = self._run("websocket", [(b"host", b"127.0.0.1"),
                                                  (b"authorization", b"Bearer tok")])
        self.assertEqual(reached, [True])


# -------------------------------------------------------------------------------- N20


class N20UnownedPendingDeliveriesTests(_sec._IsolatedDB):
    def _status(self, rid: int) -> str:
        conn = _sec.models.get_db()
        try:
            return conn.execute("SELECT status FROM webhook_delivery_log WHERE id=?", (rid,)).fetchone()[0]
        finally:
            conn.close()

    def _run(self) -> None:
        conn = _sec.models.get_db()
        try:
            _sec.models._cancel_unowned_pending_deliveries(conn)
            conn.commit()
        finally:
            conn.close()

    def test_they_are_cancelled_once(self):
        self._exec("DELETE FROM settings WHERE key=?", (_sec.models._UNOWNED_DELIVERIES_MARKER,))
        self._exec("INSERT INTO webhook_delivery_log (id, webhook_id, url, status) "
                   "VALUES (1, NULL, 'discord://1/a', 'pending')")
        self._run()
        self.assertEqual(self._status(1), "cancelled")
        self._exec("INSERT INTO webhook_delivery_log (id, webhook_id, url, status) "
                   "VALUES (2, NULL, 'discord://1/a', 'pending')")
        self._run()
        self.assertEqual(self._status(2), "pending")


# -------------------------------------------------------------------------------- N21


class N21OneWanDetectionPerCycleTests(_sec._IsolatedDB):
    def test_the_helper_caches_inside_the_block_only(self):
        from app import public_target

        calls = []
        with patch.object(public_target, "_detect_server_public_ip",
                          side_effect=lambda s, t: calls.append(1) or "203.0.113.7"):
            with public_target.one_wan_detection_per_cycle():
                for _ in range(3):
                    self.assertEqual(public_target.detect_server_public_ip(["https://a"], 2.0), "203.0.113.7")
            self.assertEqual(len(calls), 1)
            public_target.detect_server_public_ip(["https://a"], 2.0)
            self.assertEqual(len(calls), 2)

    def test_the_auto_reconcile_cycle_detects_once(self):
        from app import public_target, scheduler

        for sid in (1, 2, 3):
            self._exec("INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled) "
                       "VALUES (?, ?, 'example.com', '10.0.0.1', 80, 1)", (sid, f"s{sid}"))
        self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')")
        calls = []

        def _drift(conn, svc, sid):
            public_target.detect_server_public_ip(["https://a"], 2.0)
            return {"ok": False}

        def _push(svc, sid):
            public_target.detect_server_public_ip(["https://a"], 2.0)
            return {"ok": True}

        with patch.object(public_target, "_detect_server_public_ip",
                          side_effect=lambda s, t: calls.append(1) or "203.0.113.7"), \
                patch("app.api.sync._compute_service_drift", _drift), \
                patch("app.api.sync._execute_push", _push), \
                patch.object(scheduler, "_fire_reconcile_webhook", lambda *a: None):
            scheduler.run_auto_reconcile()
        self.assertEqual(len(calls), 1)


# -------------------------------------------------------------------------------- N27


class N27EnableRefreshesTheAutomaticTargetTests(_sec._WithKeys):
    def _service(self, mode: str) -> None:
        self._exec("""INSERT INTO providers (id, name, type, url, enabled)
                      VALUES (70, 'dns', 'pihole', 'http://pihole', 1)""")
        self._exec("""INSERT INTO services (id, subdomain, domain, target_ip, target_port, enabled,
                      dns_provider_id, dns_ip, public_target_mode)
                      VALUES (7, 'app', 'example.com', '10.0.0.5', 80, 0, 70, '198.51.100.1', ?)""", (mode,))

    def _enable(self, detected: str):
        dns = MagicMock()
        dns.add_rewrite.return_value = True
        with patch("app.api.services.create_provider", lambda _row: dns), \
                patch("app.api.services.resolve_dns_target", return_value=(detected, "auto")), \
                patch("app.api.services.push_extra_targets", return_value=[]):
            r = self.client.post("/api/services/bulk", headers=self._h("rw"),
                                 json={"ids": [7], "action": "enable"})
        self.assertEqual(r.status_code, 200, r.text)
        conn = _sec.models.get_db()
        try:
            stored = conn.execute("SELECT dns_ip FROM services WHERE id=7").fetchone()[0]
        finally:
            conn.close()
        return dns, stored

    def test_an_auto_target_is_resolved_again(self):
        self._service("auto")
        dns, stored = self._enable("203.0.113.9")
        dns.add_rewrite.assert_called_once_with("app.example.com", "203.0.113.9")
        self.assertEqual(stored, "203.0.113.9")

    def test_a_manual_target_is_kept(self):
        self._service("manual")
        dns, stored = self._enable("203.0.113.9")
        dns.add_rewrite.assert_called_once_with("app.example.com", "198.51.100.1")
        self.assertEqual(stored, "198.51.100.1")


# -------------------------------------------------------------------------------- N28


class N28DockerImportErrorsNameTheContainerTests(_sec._WithKeys):
    def test_one_line_per_container_with_its_name(self):
        r = self.client.post("/api/docker/import", headers=self._h("rw"), json={
            "domain": "example.com",
            "containers": [{"name": "web", "target_ip": "10.0.0.5", "target_port": 70000}],
        })
        self.assertEqual(r.status_code, 200, r.text)
        errors = r.json()["errors"]
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith("web: "), errors)
        self.assertNotIn("\n", errors[0])


# -------------------------------------------------------------------------------- N24


class N24ServiceCreationRaceTests(_sec._WithKeys):
    def test_the_row_is_not_enabled_while_the_providers_are_written(self):
        seen = {}

        class _Proxy:
            def find_best_certificate(self, _domain):
                return None

            def create_host(self, *_a, **_kw):
                conn = _sec.models.get_db()
                try:
                    seen["enabled"] = conn.execute(
                        "SELECT enabled FROM services WHERE subdomain='app'").fetchone()[0]
                finally:
                    conn.close()
                return {"id": 42}

        with patch("app.api.services.create_provider", lambda _row: _Proxy()):
            r = self.client.post("/api/services", headers=self._h("rw"), json={
                "subdomain": "app", "domain": "example.com", "target_ip": "10.0.0.5",
                "target_port": 8080, "proxy_provider_id": 1,
            })
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(seen["enabled"], 0)
        conn = _sec.models.get_db()
        try:
            row = conn.execute("SELECT enabled, npm_host_id FROM services WHERE subdomain='app'").fetchone()
        finally:
            conn.close()
        self.assertEqual((row["enabled"], str(row["npm_host_id"])), (1, "42"))

    def test_a_tunnel_hostname_is_held_by_the_unique_index(self):
        import sqlite3

        self._exec("""INSERT INTO services (subdomain, domain, target_ip, target_port, expose_mode,
                      tunnel_hostname) VALUES ('x', 'example.com', '10.0.0.1', 80, 'tunnel',
                      'app.example.com')""")
        with self.assertRaises(sqlite3.IntegrityError):
            self._exec("""INSERT INTO services (subdomain, domain, target_ip, target_port)
                          VALUES ('App', 'example.com', '10.0.0.2', 80)""")


if __name__ == "__main__":
    unittest.main()
