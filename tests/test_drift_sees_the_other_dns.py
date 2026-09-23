"""A name another DNS integration answers differently is shown, and never pushed over.

The drift check read the DNS integrations a service is attached to, and nothing else.
Measured in production on 2026-09-22: a media server was imported with AdGuard answering a
LAN address for it; a grey Cloudflare A record gave the same name a public one, and `/drift`
answered `ok: true, issues: []`. Whoever resolved the name through Cloudflare was sent to an
address where nothing listens, and nothing on the screen could say so.

Split-horizon is the same shape on purpose, so the answer is a warning and not an error: `ok`
stays where the attached integrations put it. The scheduler reads only `ok` and pushes only to
those, so an error would set off a push every round that could never clear it; and it does not
ask, which would cost one call per integration per service every round.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models, scheduler
from app.api import sync as sync_api
from app.providers.base import DNSProvider

HOST = "jellyfin.example.com"
PUBLISHED = "10.0.0.2"
ELSEWHERE = "203.0.113.7"


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _Dns:
    """A DNS integration that holds records in memory and notes each time it is read.

    Only `list_rewrites` on the read side, as a plugin written before `records_for` would
    have: the drift check has to reach it through its listing.
    """

    def __init__(self, name, reads, records=(), *, error=None):
        self.name = name
        self.reads = reads
        self.records = list(records)
        self.error = error

    def list_rewrites(self):
        self.reads.append(self.name)
        if self.error:
            raise self.error
        return list(self.records)


class _AsksForOneName(_Dns):
    """One that can ask for a single name, as Cloudflare does: its listing must go unread."""

    def list_rewrites(self):
        raise AssertionError("the whole listing was read for one name")

    def records_for(self, domain):
        self.reads.append((self.name, domain))
        return [r for r in self.records if r["domain"] == domain]


class _BrokenProxy:
    def __init__(self, error):
        self.error = error

    def list_hosts(self):
        raise self.error


class _DriftTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "elsewhere.test.db")
        models.init_db()

        self.reads: list = []
        self.providers: dict = {}
        self._patchers = [
            patch.object(sync_api, "create_provider", lambda row: self.providers[row["id"]]),
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

    def _exec(self, sql: str, params: tuple = ()) -> None:
        conn = models.get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _provider(self, pid: int, name: str, ptype: str, fake, *, enabled: bool = True):
        self._exec(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?,?,?,'http://x','u','p','{}',?)""",
            (pid, name, ptype, int(enabled)),
        )
        self.providers[pid] = fake
        return fake

    def _service(
        self,
        *,
        dns_provider_id=1,
        proxy_provider_id=None,
        dns_ip=PUBLISHED,
        expose_mode="proxy_dns",
        enabled=True,
    ) -> int:
        self._exec(
            """INSERT INTO services (id, subdomain, domain, target_ip, target_port,
                                     dns_provider_id, proxy_provider_id, dns_ip, expose_mode,
                                     enabled)
               VALUES (1, 'jellyfin', 'example.com', '10.0.0.6', 8096, ?, ?, ?, ?, ?)""",
            (dns_provider_id, proxy_provider_id, dns_ip, expose_mode, int(enabled)),
        )
        return 1

    def _home(self):
        """AdGuard, attached, holding exactly what the service publishes."""
        return self._provider(
            1, "AdGuard", "adguard", _Dns("AdGuard", self.reads, [{"domain": HOST, "answer": PUBLISHED}])
        )

    def _cloudflare(self, *answers: str, enabled: bool = True):
        """Cloudflare, not attached, holding the name with the answers given."""
        records = [{"domain": HOST, "answer": a} for a in answers]
        return self._provider(
            2, "Cloudflare", "cloudflare", _Dns("Cloudflare", self.reads, records), enabled=enabled
        )

    def _drift(self) -> dict:
        return sync_api.service_drift(1, _request())

    @staticmethod
    def _shape(issue: dict) -> tuple:
        return issue["severity"], issue["type"], issue["provider"]


class TheOtherIntegrationsAreRead(_DriftTestCase):
    def test_a_record_the_service_is_not_pushed_to_is_named(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service()

        drift = self._drift()

        self.assertTrue(drift["ok"], "a record that is not the service's put it out of sync")
        [issue] = drift["issues"]
        self.assertEqual(self._shape(issue), ("warn", "dns_answered_elsewhere", "Cloudflare"))
        self.assertEqual(issue["detail_key"], "answered_elsewhere")
        self.assertEqual(
            issue["detail_params"], {"host": HOST, "found": ELSEWHERE, "expected": PUBLISHED}
        )
        self.assertIn(ELSEWHERE, issue["detail"])

    def test_the_same_answer_there_is_not_drift(self):
        self._home()
        self._cloudflare(PUBLISHED)
        self._service()

        self.assertEqual(self._drift()["issues"], [])
        # Silence has to come from an answer: it was asked, and it agreed.
        self.assertIn("Cloudflare", self.reads)

    def test_every_other_answer_is_named_once_in_order(self):
        self._home()
        # Read first, sorted last: the order the records came back in is not the one shown.
        self._cloudflare(ELSEWHERE, "2001:db8::7", PUBLISHED, ELSEWHERE)
        self._service()

        [issue] = self._drift()["issues"]
        self.assertEqual(issue["detail_params"]["found"], f"2001:db8::7, {ELSEWHERE}")

    def test_a_name_spelled_another_way_is_the_same_name(self):
        self._home()
        self._provider(
            2,
            "Technitium",
            "technitium",
            _Dns("Technitium", self.reads, [{"domain": "JellyFin.Example.com.", "answer": ELSEWHERE}]),
        )
        self._service()

        [issue] = self._drift()["issues"]
        self.assertEqual(self._shape(issue), ("warn", "dns_answered_elsewhere", "Technitium"))

    def test_an_attached_integration_is_read_once_by_the_loop_that_owns_it(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service()
        self._exec("INSERT INTO service_push_targets (service_id, provider_id, role) VALUES (1, 2, 'dns')")

        drift = self._drift()

        self.assertEqual([i["type"] for i in drift["issues"]], ["dns_target_mismatch"])
        self.assertEqual(self.reads.count("Cloudflare"), 1)

    def test_one_that_can_ask_for_a_single_name_is_not_listed_whole(self):
        self._home()
        self._provider(
            2,
            "Cloudflare",
            "cloudflare",
            _AsksForOneName("Cloudflare", self.reads, [{"domain": HOST, "answer": ELSEWHERE}]),
        )
        self._service()

        [issue] = self._drift()["issues"]
        self.assertEqual(issue["type"], "dns_answered_elsewhere")
        self.assertIn(("Cloudflare", HOST), self.reads)


class NothingElseIsAskedWithoutAnAnswerToCompare(_DriftTestCase):
    def test_a_tunnel_writes_its_own_record(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service(expose_mode="tunnel")

        self.assertEqual(self._drift()["issues"], [])
        self.assertNotIn("Cloudflare", self.reads)

    def test_a_disabled_service_publishes_nothing(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service(enabled=False)

        drift = self._drift()

        # AdGuard still answering is the drift a disabled service does have.
        self.assertEqual([i["type"] for i in drift["issues"]], ["dns_rewrite_still_served"])
        self.assertNotIn("Cloudflare", self.reads)

    def test_a_service_with_no_address_has_nothing_to_hold_it_against(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service(dns_ip="")

        self._drift()
        self.assertNotIn("Cloudflare", self.reads)

    def test_a_service_pushed_to_no_dns_publishes_no_answer(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service(dns_provider_id=None)

        self.assertEqual(self._drift()["issues"], [])
        self.assertEqual(self.reads, [])

    def test_a_disabled_integration_is_not_asked(self):
        self._home()
        self._cloudflare(ELSEWHERE, enabled=False)
        self._service()

        self.assertEqual(self._drift()["issues"], [])
        self.assertNotIn("Cloudflare", self.reads)

    def test_a_proxy_is_not_asked_about_a_name(self):
        self._home()
        self._provider(2, "NPM", "npm", _Dns("NPM", self.reads, [{"domain": HOST, "answer": ELSEWHERE}]))
        self._service()

        self.assertEqual(self._drift()["issues"], [])
        self.assertNotIn("NPM", self.reads)


class AnIntegrationThatCannotBeReadSaysSo(_DriftTestCase):
    REFUSAL = "Technitium zone list failed: for url: /api/zones/list?token=abc123secret"

    def test_its_silence_is_not_read_as_nobody_answering(self):
        self._home()
        self._provider(
            2, "Technitium", "technitium", _Dns("Technitium", self.reads, error=RuntimeError(self.REFUSAL))
        )
        self._service()

        drift = self._drift()

        self.assertTrue(drift["ok"])
        [issue] = drift["issues"]
        self.assertEqual(self._shape(issue), ("warn", "dns_elsewhere_check_failed", "Technitium"))
        self.assertNotIn("abc123secret", issue["detail"])
        self.assertIn("token=***", issue["detail"])

    def test_an_attached_one_that_fails_is_masked_too(self):
        self._provider(
            1, "Technitium", "technitium", _Dns("Technitium", self.reads, error=RuntimeError(self.REFUSAL))
        )
        self._service()

        [issue] = self._drift()["issues"]
        self.assertEqual(self._shape(issue), ("error", "dns_check_failed", "Technitium"))
        self.assertNotIn("abc123secret", issue["detail"])

    def test_a_proxy_that_fails_is_masked_too(self):
        self._home()
        self._provider(3, "NPM", "npm", _BrokenProxy(RuntimeError("for url: /api/hosts?auth=K3yK3yK3y")))
        self._service(proxy_provider_id=3)

        [issue] = self._drift()["issues"]
        self.assertEqual(self._shape(issue), ("error", "proxy_check_failed", "NPM"))
        self.assertNotIn("K3yK3yK3y", issue["detail"])


class TheSchedulerDoesNotAsk(_DriftTestCase):
    def test_a_round_reads_the_attached_integrations_and_nothing_else(self):
        self._home()
        self._cloudflare(ELSEWHERE)
        self._service()
        self._exec(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_reconcile_enabled', 'true')"
        )

        with patch.object(sync_api, "_execute_push") as push:
            scheduler.run_auto_reconcile()

        self.assertIn("AdGuard", self.reads, "the round never computed the drift")
        self.assertNotIn("Cloudflare", self.reads)
        push.assert_not_called()


class RecordsForTests(unittest.TestCase):
    def test_the_default_filters_the_listing_by_name(self):
        class _Listing(DNSProvider):
            def test_connection(self):
                return True

            def list_rewrites(self):
                return [
                    {"domain": "App.Example.com.", "answer": "10.0.0.1"},
                    {"domain": "other.example.com", "answer": "10.0.0.2"},
                    {"domain": " app.example.com ", "answer": "10.0.0.3"},
                ]

            def add_rewrite(self, domain, ip):
                return True

            def delete_rewrite(self, domain, ip):
                return True

        answers = [r["answer"] for r in _Listing().records_for("app.example.com")]
        self.assertEqual(answers, ["10.0.0.1", "10.0.0.3"])


if __name__ == "__main__":
    unittest.main()
