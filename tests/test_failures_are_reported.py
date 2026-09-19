"""When a provider refuses, Vauxtra must not answer "synced".

Every provider in `app/providers/` reports a refusal the same way: `add_rewrite`,
`delete_rewrite`, `update_host` and `delete_host` return `False`, `create_host` returns
`None`. None of them raise. `push_service` only ever filled its `errors` list from an
`except` clause, so an expired NPM token, a revoked Cloudflare scope or a Pi-hole that
answered 401 all produced `{"ok": true, "errors": []}` and a `[Push] Proxy synced` line in
the journal the operator reads to check exactly that.

The same shape appears three more times, and each is covered here:

- `CloudflareTunnelProvider._delete_dns_record` treated a failed *listing* as an empty zone,
  so `delete_host` reported a hostname withdrawn while its CNAME still resolved;
- the Cloudflare DNS provider acted on the first record and the first zone the API returned,
  without checking that either was the one asked for;
- `save_settings` dropped a value it did not like with a bare `continue`, then answered
  `{"ok": true}` -- and for `check_interval` the value it *did* accept was read back with a
  bare `int()` at startup, so "later" in that field kept the application from booting.

A fifth shape is the absence of a target rather than a refusal, and it reached four routes
that disagreed with one another about it: `add_service` raised a 400, `update_service`
saved the state and answered 200, `push/dry-run` answered `ok:false`, and `push_service`
answered `{"ok": true, "errors": []}` for the same service at the same instant. The last
one is the route that writes records.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import providers as providers_api
from app.api import services as services_api
from app.api import settings as settings_api
from app.api import sync as sync_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _ScriptedProvider:
    """A provider that answers with booleans, the way the real ones do."""

    def __init__(
        self,
        calls: list,
        *,
        host_ok: bool = True,
        add_ok: bool = True,
        delete_ok: bool = True,
        rewrites: list | None = None,
    ):
        self._calls = calls
        self._host_ok = host_ok
        self._add_ok = add_ok
        self._delete_ok = delete_ok
        self._rewrites = rewrites if rewrites is not None else []

    def _record(self, name, *args):
        self._calls.append((name, *args))

    # ProxyProvider
    def find_best_certificate(self, _domain):
        return None

    def list_hosts(self):
        return []

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._record("create_host", domain)
        return {"id": 4242} if self._host_ok else None

    def update_host(self, host_id, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._record("update_host", host_id, domain)
        return self._host_ok

    def delete_host(self, host_id):
        self._record("delete_host", host_id)
        return self._host_ok

    # DNSProvider
    def list_rewrites(self):
        self._record("list_rewrites")
        return list(self._rewrites)

    def add_rewrite(self, domain, ip):
        self._record("add_rewrite", domain, ip)
        return self._add_ok

    def delete_rewrite(self, domain, ip):
        self._record("delete_rewrite", domain, ip)
        return self._delete_ok


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "failures.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _logs(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [r["message"] for r in rows]


class PushReportsProviderRefusalTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        self.calls: list = []
        self.provider = _ScriptedProvider(self.calls)
        self._patchers = [
            patch.object(sync_api, "require_auth", lambda _req, scope=None: None),
            patch.object(sync_api, "create_provider", lambda _row: self.provider),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patchers)])

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def _seed(self, *, npm_host_id=77, dns_provider_id=3) -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, proxy_provider_id, npm_host_id,
                  dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'proxy_dns',
                       2, ?, ?, '198.51.100.7', 'manual')""",
            (npm_host_id, dns_provider_id),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def test_successful_push_still_reports_ok(self) -> None:
        sid = self._seed()
        result = sync_api.push_service(sid, _request())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("Proxy synced" in m for m in self._logs()))
        self.assertTrue(any("DNS synced" in m for m in self._logs()))

    def test_refused_host_update_is_an_error_not_a_sync(self) -> None:
        self.provider = _ScriptedProvider(self.calls, host_ok=False)
        sid = self._seed()

        result = sync_api.push_service(sid, _request())

        self.assertFalse(result["ok"])
        self.assertTrue(any(e.startswith("Proxy (NPM):") for e in result["errors"]), result)
        self.assertFalse(any("Proxy synced" in m for m in self._logs()))
        self.assertTrue(any("Proxy refused" in m for m in self._logs()))

    def test_refused_host_creation_is_an_error(self) -> None:
        """No `npm_host_id` yet, so the push goes through `create_host`, which returns None."""
        self.provider = _ScriptedProvider(self.calls, host_ok=False)
        sid = self._seed(npm_host_id=None)

        result = sync_api.push_service(sid, _request())

        self.assertFalse(result["ok"])
        self.assertIn("create_host", [c[0] for c in self.calls])
        self.assertTrue(any("creation of a proxy host" in e for e in result["errors"]), result)

    def test_refused_rewrite_creation_is_an_error(self) -> None:
        self.provider = _ScriptedProvider(self.calls, add_ok=False)
        sid = self._seed()

        result = sync_api.push_service(sid, _request())

        self.assertFalse(result["ok"])
        self.assertTrue(any(e.startswith("DNS (AdGuard):") for e in result["errors"]), result)
        self.assertFalse(any("DNS synced" in m for m in self._logs()))

    def test_a_failed_removal_does_not_add_a_second_record(self) -> None:
        """AdGuard and Pi-hole hold two rewrites for one name without complaining."""
        self.provider = _ScriptedProvider(
            self.calls,
            delete_ok=False,
            rewrites=[{"domain": "app.example.com", "answer": "203.0.113.4"}],
        )
        sid = self._seed()

        result = sync_api.push_service(sid, _request())

        self.assertFalse(result["ok"])
        self.assertIn("delete_rewrite", [c[0] for c in self.calls])
        self.assertNotIn("add_rewrite", [c[0] for c in self.calls])
        self.assertTrue(any("removal of the stale" in e for e in result["errors"]), result)

    def test_a_stale_record_is_replaced_when_the_provider_cooperates(self) -> None:
        self.provider = _ScriptedProvider(
            self.calls,
            rewrites=[{"domain": "app.example.com", "answer": "203.0.113.4"}],
        )
        sid = self._seed()

        result = sync_api.push_service(sid, _request())

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [c for c in self.calls if c[0] in ("delete_rewrite", "add_rewrite")],
            [
                ("delete_rewrite", "app.example.com", "203.0.113.4"),
                ("add_rewrite", "app.example.com", "198.51.100.7"),
            ],
        )

    def test_the_error_names_the_provider_and_says_where_to_look(self) -> None:
        self.provider = _ScriptedProvider(self.calls, host_ok=False)
        sid = self._seed()

        message = sync_api.push_service(sid, _request())["errors"][0]

        self.assertIn("NPM", message)
        self.assertIn("credentials", message)


class CloudflareTunnelDeleteTests(unittest.TestCase):
    """A listing that failed is not a zone with nothing in it."""

    def _provider(self, responses):
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        provider = CloudflareTunnelProvider("", "acct", "token", {"tunnel_id": "tun-1"})
        calls: list = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path))
            return responses.pop(0) if responses else None

        provider._request = fake_request  # noqa: SLF001 -- that is the seam under test
        provider._find_zone = lambda _hostname: "zone-1"  # noqa: SLF001
        return provider, calls

    def test_a_failed_listing_does_not_report_the_record_deleted(self) -> None:
        provider, _ = self._provider([None])
        self.assertFalse(provider._delete_dns_record("app.example.com"))  # noqa: SLF001

    def test_an_empty_zone_still_reports_success(self) -> None:
        provider, _ = self._provider([[]])
        self.assertTrue(provider._delete_dns_record("app.example.com"))  # noqa: SLF001

    def test_a_matching_record_is_deleted(self) -> None:
        provider, calls = self._provider(
            [
                [{"id": "rec-1", "content": "tun-1.cfargotunnel.com"}],
                {"id": "rec-1"},
            ]
        )
        self.assertTrue(provider._delete_dns_record("app.example.com"))  # noqa: SLF001
        self.assertIn(("DELETE", "/zones/zone-1/dns_records/rec-1"), calls)

    def test_a_zone_whose_name_differs_is_not_used(self) -> None:
        from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

        provider = CloudflareTunnelProvider("", "acct", "token", {"tunnel_id": "tun-1"})
        provider._request = lambda *a, **k: [  # noqa: SLF001
            {"id": "wrong-zone", "name": "notexample.com"}
        ]
        self.assertEqual(provider._find_zone("app.example.com"), "")  # noqa: SLF001


class _FakeRecord:
    def __init__(self, rid, name, content, proxied=False):
        self.id, self.name, self.content, self.proxied = rid, name, content, proxied


class _FakeZone:
    def __init__(self, zid, name):
        self.id, self.name = zid, name


class CloudflareRecordTargetingTests(unittest.TestCase):
    """`name` is a server-side filter, not a promise about what comes back."""

    def _provider(self, records, zones=None):
        from app.providers.cloudflare import CloudflareProvider

        provider = CloudflareProvider("", "", "token")
        self.deleted: list = []
        self.updated: list = []
        self.created: list = []
        outer = self

        class _Records:
            def list(self, **kwargs):
                outer.listed = kwargs
                return list(records)

            def update(self, **kwargs):
                outer.updated.append(kwargs)
                return _FakeRecord(kwargs["dns_record_id"], kwargs["name"], kwargs["content"])

            def create(self, **kwargs):
                outer.created.append(kwargs)
                return _FakeRecord("new", kwargs["name"], kwargs["content"])

            def delete(self, **kwargs):
                outer.deleted.append(kwargs)
                return None

        class _Zones:
            def list(self, **kwargs):
                return list(zones if zones is not None else [_FakeZone("zone-1", kwargs.get("name", ""))])

        class _Client:
            dns = type("_DNS", (), {"records": _Records()})()
            zones = _Zones()

        provider._client = _Client()  # noqa: SLF001
        return provider

    def test_the_exact_filter_is_used(self) -> None:
        provider = self._provider([])
        provider.add_rewrite("app.example.com", "198.51.100.7")
        self.assertEqual(self.listed.get("name"), {"exact": "app.example.com"})

    def test_a_record_with_another_name_is_not_overwritten(self) -> None:
        provider = self._provider([_FakeRecord("other", "app.example.com.evil.test", "1.1.1.1")])
        self.assertTrue(provider.add_rewrite("app.example.com", "198.51.100.7"))
        self.assertEqual(self.updated, [])
        self.assertEqual(len(self.created), 1)

    def test_a_record_with_another_name_is_not_deleted(self) -> None:
        provider = self._provider([_FakeRecord("other", "app.example.com.evil.test", "198.51.100.7")])
        self.assertFalse(provider.delete_rewrite("app.example.com", "198.51.100.7"))
        self.assertEqual(self.deleted, [])

    def test_the_right_record_is_still_updated_and_deleted(self) -> None:
        provider = self._provider([_FakeRecord("r1", "app.example.com", "203.0.113.4")])
        self.assertTrue(provider.add_rewrite("app.example.com", "198.51.100.7"))
        self.assertEqual(self.updated[0]["dns_record_id"], "r1")

        provider = self._provider([_FakeRecord("r1", "APP.example.com.", "198.51.100.7")])
        self.assertTrue(provider.delete_rewrite("app.example.com", "198.51.100.7"))
        self.assertEqual(self.deleted[0]["dns_record_id"], "r1")

    def test_a_zone_whose_name_differs_is_not_cached(self) -> None:
        provider = self._provider([], zones=[_FakeZone("wrong", "notexample.com")])
        self.assertIsNone(provider._find_zone("app.example.com"))  # noqa: SLF001


class SettingsRejectionTests(_IsolatedDB):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(settings_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stored(self, key: str) -> str | None:
        conn = models.get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else None

    def _save(self, body: dict):
        return settings_api.save_settings(_request(), body)

    def test_a_valid_payload_is_saved(self) -> None:
        with patch("app.scheduler.configure", lambda _m: None):
            result = self._save({"check_interval": " 5 ", "log_retention_days": "30"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["saved"], ["check_interval", "log_retention_days"])
        self.assertEqual(self._stored("check_interval"), "5")

    def test_a_non_numeric_interval_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._save({"check_interval": "later"})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("check_interval", ctx.exception.detail)
        self.assertIsNone(self._stored("check_interval"))

    def test_an_out_of_range_interval_is_refused(self) -> None:
        with self.assertRaises(HTTPException):
            self._save({"check_interval": "100000"})
        self.assertIsNone(self._stored("check_interval"))

    def test_nothing_is_written_when_one_key_is_bad(self) -> None:
        with self.assertRaises(HTTPException):
            self._save({"theme": "dark", "check_interval": "soon"})
        self.assertIsNone(self._stored("theme"))

    def test_unwritable_keys_are_reported_not_silently_dropped(self) -> None:
        result = self._save({"theme": "dark", "schema_version": "99"})
        self.assertEqual(result["ignored"], ["schema_version"])
        self.assertEqual(self._stored("theme"), "dark")

    def test_the_masked_webhook_url_cannot_be_written_back(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._save({"webhook_url": "discord://***"})
        self.assertIn("masked", ctx.exception.detail)
        self.assertIsNone(self._stored("webhook_url"))

    def test_a_bad_timezone_is_refused(self) -> None:
        with self.assertRaises(HTTPException):
            self._save({"timezone": "Europe/Paris; DROP"})
        self.assertIsNone(self._stored("timezone"))
        self._save({"timezone": "Europe/Paris"})
        self.assertEqual(self._stored("timezone"), "Europe/Paris")

    def test_boolean_settings_are_normalized(self) -> None:
        # This used to test `webhook_enabled`, which is retired: it switched on a delivery
        # path that no longer existed. `auto_reconcile_enabled` is the boolean that remains,
        # and it is the one that was unwritable until now.
        self._save({"auto_reconcile_enabled": "YES"})
        self.assertEqual(self._stored("auto_reconcile_enabled"), "true")
        with self.assertRaises(HTTPException):
            self._save({"auto_reconcile_enabled": "maybe"})


class StartupSurvivesABadIntervalTests(_IsolatedDB):
    """A value written before the validation existed must not keep the app from booting."""

    def test_a_poisoned_interval_does_not_stop_the_application(self) -> None:
        from fastapi.testclient import TestClient

        import app.auth as auth
        import app.main as app_main
        import app.scheduler as scheduler

        conn = models.get_db()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('check_interval', 'later') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        conn.close()

        started: list = []
        with (
            patch.object(auth, "APP_PASSWORD", ""),
            patch.object(scheduler, "start", lambda interval_minutes=0: started.append(interval_minutes)),
        ):
            with TestClient(app_main.app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)

        self.assertEqual(started, [0])


class MissingDnsTargetIsReportedTests(_IsolatedDB):
    """A DNS provider with nothing to write is one state, and it gets one answer.

    The state is reachable: `dns_ip` is optional, automatic detection returns nothing on a
    host with no route to the WAN resolvers, and a service can be edited to gain a DNS
    provider it had no target for. What the four routes did with it had nothing in common.
    """

    def setUp(self) -> None:
        super().setUp()
        self.calls: list = []
        self.provider = _ScriptedProvider(self.calls)
        self._patchers = [
            patch.object(sync_api, "require_auth", lambda _req, scope=None: None),
            patch.object(sync_api, "create_provider", lambda _row: self.provider),
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: self.provider),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patchers)])

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def _service_in(self, **overrides):
        payload = {
            "subdomain": "app",
            "domain": "example.com",
            "target_ip": "10.0.0.9",
            "target_port": 8080,
            "expose_mode": "proxy_dns",
            "enabled": True,
        }
        payload.update(overrides)
        return services_api.ServiceIn(**payload)

    def _seed_without_target(self, *, dns_provider_id=3) -> int:
        """A row in the state the routes disagreed about: DNS provider set, `dns_ip` empty."""
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, proxy_provider_id, npm_host_id,
                  dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'proxy_dns',
                       NULL, NULL, ?, '', 'manual')""",
            (dns_provider_id,),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    # -- la poussee ----------------------------------------------------------------------

    def test_push_with_no_target_is_an_error_not_a_sync(self) -> None:
        sid = self._seed_without_target()

        result = sync_api.push_service(sid, _request())

        self.assertFalse(result["ok"], result)
        self.assertTrue(result["errors"], "the push wrote nothing and said nothing")
        self.assertNotIn("add_rewrite", [c[0] for c in self.calls])

    def test_dry_run_and_push_agree_about_the_same_service(self) -> None:
        """The preview of an action is worth nothing if the action disagrees with it."""
        sid = self._seed_without_target()

        plan = sync_api.dry_run_push_service(sid, _request())
        result = sync_api.push_service(sid, _request())

        self.assertEqual(plan["ok"], result["ok"])
        self.assertEqual(plan["errors"], result["errors"])

    # -- la creation et la modification --------------------------------------------------

    def test_creating_with_a_dns_provider_and_no_target_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._service_in(dns_provider_id=3))

        self.assertEqual(caught.exception.status_code, 400)

    def test_editing_a_service_to_add_a_dns_provider_needs_a_target_too(self) -> None:
        """This is the one that answered 200 and saved the state the others called invalid."""
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, proxy_provider_id, npm_host_id,
                  dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'proxy_dns',
                       2, 77, NULL, '', 'manual')""",
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()

        with self.assertRaises(HTTPException) as caught:
            services_api.update_service(
                sid, _request(), self._service_in(proxy_provider_id=2, dns_provider_id=3)
            )

        self.assertEqual(caught.exception.status_code, 400)

        conn = models.get_db()
        row = conn.execute("SELECT dns_provider_id FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        self.assertIsNone(row["dns_provider_id"], "the refused provider was attached anyway")

    def test_nothing_is_created_on_the_proxy_before_the_refusal(self) -> None:
        """The refusal used to fire after `create_host`, and undo it with a silent delete."""
        with self.assertRaises(HTTPException):
            services_api.add_service(
                _request(), self._service_in(proxy_provider_id=2, dns_provider_id=3)
            )

        self.assertEqual(self.calls, [], "a host was created on the proxy, then deleted")

    # -- la phrase -----------------------------------------------------------------------

    def test_an_empty_field_and_a_failed_lookup_do_not_read_the_same(self) -> None:
        """One of the two resolves something. Both used to say "Unable to resolve"."""
        with self.assertRaises(HTTPException) as blank:
            services_api.add_service(
                _request(), self._service_in(dns_provider_id=3, public_target_mode="manual")
            )
        with patch.object(services_api, "resolve_public_target", return_value=("", "auto_unavailable")):
            with self.assertRaises(HTTPException) as failed:
                services_api.add_service(
                    _request(), self._service_in(dns_provider_id=3, public_target_mode="auto")
                )

        blank_text = blank.exception.detail["message"]
        failed_text = failed.exception.detail["message"]

        self.assertNotEqual(blank_text, failed_text)
        self.assertNotIn("resolve", blank_text.lower(), "nothing was looked up")
        self.assertIn("AdGuard", blank_text, "the operator has several DNS providers")

    def test_the_refusal_carries_a_key_the_interface_can_act_on(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            services_api.add_service(_request(), self._service_in(dns_provider_id=3))

        self.assertEqual(caught.exception.detail["detail_key"], "dns_target_required")


class _ConnectionProvider:
    """A provider whose `test_connection` answers the way the real ones do."""

    def __init__(self, answer):
        self._answer = answer

    def test_connection(self):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class BatchHealthReadsTheAnswerTests(_IsolatedDB):
    """`GET /providers/health` is the map that paints the dashboard tiles.

    It called `test_connection()` and threw the boolean away, so the only refusal it could
    report was an exception. None of the providers raise: an unreachable AdGuard, an NPM
    with a revoked token and a Pi-hole answering 401 all return False. Six integrations
    that had just refused the connection were drawn green, next to a journal full of
    `authentication failed` lines from the certificate checker looking at the same hosts.
    """

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(providers_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        conn = models.get_db()
        for pid, name in ((1, "AdGuard"), (2, "NPM"), (3, "Pi-hole")):
            conn.execute(
                """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
                   VALUES (?, ?, 'adguard', 'http://unreachable', 'admin', 'pass', '{}', 1)""",
                (pid, name),
            )
        conn.commit()
        conn.close()

    def _health(self, answers: dict) -> dict:
        def _create(row):
            return _ConnectionProvider(answers[row["id"]])

        with patch.object(providers_api, "create_provider", _create):
            return providers_api.all_providers_health(_request("GET", "/api/providers/health"))

    def test_a_refused_connection_is_not_healthy(self) -> None:
        health = self._health({1: False, 2: False, 3: False})
        self.assertEqual(
            {k: v["status"] for k, v in health.items()},
            {"1": "unhealthy", "2": "unhealthy", "3": "unhealthy"},
        )

    def test_a_working_provider_is_still_healthy(self) -> None:
        """The positive control: the fix must not paint everything red instead."""
        health = self._health({1: True, 2: True, 3: True})
        self.assertEqual(
            {k: v["status"] for k, v in health.items()},
            {"1": "healthy", "2": "healthy", "3": "healthy"},
        )

    def test_each_provider_is_judged_on_its_own_answer(self) -> None:
        health = self._health({1: True, 2: False, 3: True})
        self.assertEqual(health["1"]["status"], "healthy")
        self.assertEqual(health["2"]["status"], "unhealthy")
        self.assertEqual(health["3"]["status"], "healthy")

    def test_an_exception_still_reports_its_message(self) -> None:
        health = self._health({1: RuntimeError("name does not resolve"), 2: True, 3: True})
        self.assertEqual(health["1"]["status"], "unhealthy")
        self.assertIn("name does not resolve", health["1"]["error"])

    def test_a_truthy_non_boolean_answer_is_accepted(self) -> None:
        """Some providers return the parsed payload rather than True."""
        health = self._health({1: {"version": "1.2"}, 2: True, 3: True})
        self.assertEqual(health["1"]["status"], "healthy")

    def test_a_disabled_provider_is_left_out_of_the_map(self) -> None:
        conn = models.get_db()
        conn.execute("UPDATE providers SET enabled=0 WHERE id=3")
        conn.commit()
        conn.close()
        health = self._health({1: True, 2: True})
        self.assertEqual(sorted(health), ["1", "2"])

if __name__ == "__main__":
    unittest.main()
