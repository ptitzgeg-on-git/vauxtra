"""A scan says which zone each name belongs to, and which integration answered what.

Measured in production on 2026-09-22: two scans minutes apart, with no setting changed in
between according to the report, answered 91 routes and then 32. The 59 others came from
twelve zones nobody had declared, listed through one Cloudflare token by the first scan and
not by the second. Each row named its integration, but an integration that failed left no
row, and nothing said it had failed. And in the code, the import split every name at its
first dot: `a.b.example.net` became a service `a` under a domain `b.example.net`, which the
import declared in passing.

So a scan now answers with `_zone` and `_declared` on every row, one line per integration in
`providers`, and the domains the operator declared in `declared_domains`; and the import
splits a name at its zone and finds an existing service by the whole name.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import sync as sync_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _Scanned:
    """A provider that answers a scan with exactly what the test handed it."""

    def __init__(self, hosts=(), rewrites=(), raises: Exception | None = None):
        self._hosts = [dict(h) for h in hosts]
        self._rewrites = [dict(r) for r in rewrites]
        self._raises = raises

    def list_hosts(self):
        if self._raises:
            raise self._raises
        return [dict(h) for h in self._hosts]

    def list_rewrites(self):
        if self._raises:
            raise self._raises
        return [dict(r) for r in self._rewrites]


def _host(domain: str, host_id: int = 77) -> dict:
    return {
        "id": host_id,
        "domains": [domain],
        "forward_host": "10.0.0.9",
        "forward_port": 8080,
        "forward_scheme": "http",
    }


class _ScanTestCase(unittest.TestCase):
    """Its own database per test, and three integrations: Technitium, NPM, AdGuard."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "zones.test.db")
        models.init_db()

        patcher = patch.object(sync_api, "require_auth_or_setup", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        conn = models.get_db()
        for pid, name, kind in ((1, "Technitium", "technitium"), (2, "NPM", "npm"), (3, "AdGuard", "adguard")):
            conn.execute(
                """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
                   VALUES (?, ?, ?, 'http://x', 'admin', 'pass', '{}', 1)""",
                (pid, name, kind),
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures -------------------------------------------------------------------------
    def _declare(self, *names: str) -> None:
        conn = models.get_db()
        for name in names:
            conn.execute("INSERT INTO domains (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()

    def _track(self, subdomain: str, domain: str) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO services (subdomain, domain, target_ip, target_port) VALUES (?,?,?,80)",
            (subdomain, domain, "10.0.0.9"),
        )
        conn.commit()
        conn.close()

    def _scan(self, *, technitium=None, npm=None, adguard=None) -> dict:
        answers = {
            1: technitium or _Scanned(),
            2: npm or _Scanned(),
            3: adguard or _Scanned(),
        }
        with patch.object(sync_api, "create_provider", lambda row: answers[row["id"]]):
            return sync_api.sync_services(_request())

    def _import(self, *, rewrites=(), hosts=()) -> dict:
        return sync_api.import_services(
            _request(), {"dns_rewrites": list(rewrites), "proxy_hosts": list(hosts)}
        )

    def _rows(self) -> list[dict]:
        conn = models.get_db()
        rows = conn.execute(
            "SELECT subdomain, domain, target_port, dns_ip FROM services ORDER BY id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _domains(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT name FROM domains ORDER BY name").fetchall()
        conn.close()
        return [r["name"] for r in rows]

    def _logs(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [r["message"] for r in rows]


class TheZoneOfANameTests(unittest.TestCase):
    """`_zone_of` on its own: three answers, in a fixed order."""

    def test_a_declared_domain_wins_over_a_longer_zone_the_provider_reports(self) -> None:
        """Where the operator said their services live is how the editor would store it."""
        self.assertEqual(
            sync_api._zone_of("nas.maison.example.org", ["example.org"], "maison.example.org"),
            ("example.org", True),
        )

    def test_the_longest_declared_domain_is_the_one(self) -> None:
        self.assertEqual(
            sync_api._zone_of("nas.maison.example.org", ["example.org", "maison.example.org"]),
            ("maison.example.org", True),
        )

    def test_the_provider_zone_splits_a_name_with_more_labels_than_the_first_dot(self) -> None:
        self.assertEqual(sync_api._zone_of("a.b.example.net", [], "example.net"), ("example.net", False))

    def test_a_provider_zone_the_name_is_not_under_is_ignored(self) -> None:
        self.assertEqual(sync_api._zone_of("a.other.eu", [], "example.net"), ("other.eu", False))

    def test_without_either_the_name_splits_at_its_first_dot(self) -> None:
        """The witness: how every name used to be split, and still the last resort."""
        self.assertEqual(sync_api._zone_of("a.b.c", [], ""), ("b.c", False))

    def test_a_declared_domain_only_matches_at_a_label_boundary(self) -> None:
        self.assertEqual(
            sync_api._zone_of("a.notexample.org", ["example.org"]), ("notexample.org", False)
        )

    def test_a_single_label_name_has_no_zone(self) -> None:
        self.assertEqual(sync_api._zone_of("localhost", ["example.org"]), ("", False))


class AScanSaysWhatItAskedOfWhomTests(_ScanTestCase):
    def test_every_integration_scanned_has_a_line_with_its_count(self) -> None:
        result = self._scan(
            technitium=_Scanned(rewrites=[
                {"domain": "a.vxlab.test", "answer": "10.0.0.1", "zone": "vxlab.test"},
                {"domain": "b.vxlab.test", "answer": "10.0.0.2", "zone": "vxlab.test"},
            ]),
            npm=_Scanned(hosts=[_host("app.vxlab.test")]),
        )

        self.assertEqual(
            [(p["name"], p["ok"], p["count"], p["error"]) for p in result["providers"]],
            [("Technitium", True, 2, ""), ("NPM", True, 1, ""), ("AdGuard", True, 0, "")],
        )

    def test_a_failed_integration_is_named_with_its_reason_and_the_rest_still_answer(self) -> None:
        result = self._scan(
            technitium=_Scanned(raises=RuntimeError("the zone list was refused")),
            adguard=_Scanned(rewrites=[{"domain": "c.vxlab.test", "answer": "10.0.0.3"}]),
        )

        lines = {p["name"]: p for p in result["providers"]}
        self.assertFalse(lines["Technitium"]["ok"])
        self.assertIn("the zone list was refused", lines["Technitium"]["error"])
        self.assertTrue(lines["AdGuard"]["ok"])
        self.assertEqual([r["domain"] for r in result["dns_rewrites"]], ["c.vxlab.test"])

    def test_a_credential_in_the_reason_is_masked_on_screen_and_in_the_journal(self) -> None:
        """Technitium authenticates in the query string, and `requests` quotes the URL."""
        refusal = RuntimeError(
            "401 Client Error for url: http://dns:5380/api/zones/list?token=abc123secretvalue"
        )
        result = self._scan(technitium=_Scanned(raises=refusal))

        error = result["providers"][0]["error"]
        self.assertNotIn("abc123secretvalue", error)
        self.assertIn("token=***", error)
        journal = "\n".join(self._logs())
        self.assertNotIn("abc123secretvalue", journal)
        self.assertIn("token=***", journal)

    def test_the_declared_domains_come_back_normalized_and_sorted(self) -> None:
        self._declare("Example.Org", "example.net")

        result = self._scan()

        self.assertEqual(result["declared_domains"], ["example.net", "example.org"])


class EveryScannedRowCarriesItsZoneTests(_ScanTestCase):
    def test_dns_rows_and_routes_are_filed_under_their_zone(self) -> None:
        self._declare("example.org")
        result = self._scan(
            technitium=_Scanned(rewrites=[
                {"domain": "nas.maison.example.org", "answer": "10.0.0.9", "zone": "maison.example.org"},
                {"domain": "a.b.example.net", "answer": "10.0.0.8", "zone": "example.net"},
            ]),
            npm=_Scanned(hosts=[_host("app.b.example.net")]),
            adguard=_Scanned(rewrites=[{"domain": "x.foreign.org", "answer": "10.0.0.7"}]),
        )

        zones = {r["domain"]: (r["_zone"], r["_declared"]) for r in result["dns_rewrites"]}
        self.assertEqual(zones, {
            "nas.maison.example.org": ("example.org", True),
            "a.b.example.net": ("example.net", False),
            "x.foreign.org": ("foreign.org", False),
        })
        # A proxy knows no zone; the zone a DNS integration reported is the best guess.
        route = result["proxy_hosts"][0]
        self.assertEqual((route["_zone"], route["_declared"]), ("example.net", False))

    def test_two_scans_of_one_configuration_answer_the_same_rows_in_the_same_order(self) -> None:
        technitium = _Scanned(rewrites=[{"domain": "same.vxlab.test", "answer": "10.0.0.1"}])
        adguard = _Scanned(rewrites=[{"domain": "same.vxlab.test", "answer": "10.0.0.3"}])

        first = self._scan(technitium=technitium, adguard=adguard)
        second = self._scan(technitium=technitium, adguard=adguard)

        self.assertEqual(first["dns_rewrites"], second["dns_rewrites"])
        self.assertEqual(
            [r["_provider_name"] for r in first["dns_rewrites"]], ["Technitium", "AdGuard"]
        )

    def test_of_two_answers_the_first_added_integration_is_the_one_imported(self) -> None:
        scanned = self._scan(
            technitium=_Scanned(rewrites=[{"domain": "same.vxlab.test", "answer": "10.0.0.1"}]),
            adguard=_Scanned(rewrites=[{"domain": "same.vxlab.test", "answer": "10.0.0.3"}]),
        )

        result = self._import(rewrites=scanned["dns_rewrites"])

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._rows()[0]["dns_ip"], "10.0.0.1")
        self.assertIn("Technitium and AdGuard both answer", result["errors"][0])


class TheImportSplitsANameAtItsZoneTests(_ScanTestCase):
    def test_a_name_with_two_labels_in_front_of_its_zone_keeps_both(self) -> None:
        result = self._import(rewrites=[{
            "domain": "a.b.example.net", "answer": "10.0.0.8", "_provider_id": 1,
            "_provider_name": "Technitium", "zone": "example.net", "_zone": "example.net",
        }])

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(
            [(r["subdomain"], r["domain"]) for r in self._rows()], [("a.b", "example.net")]
        )
        # The first dot used to declare `b.example.net` as a domain of its own.
        self.assertEqual(self._domains(), ["example.net"])

    def test_a_declared_domain_wins_over_the_zone_the_row_carries(self) -> None:
        self._declare("example.org")

        result = self._import(rewrites=[{
            "domain": "nas.maison.example.org", "answer": "10.0.0.9", "_provider_id": 1,
            "zone": "maison.example.org", "_zone": "maison.example.org",
        }])

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(
            [(r["subdomain"], r["domain"]) for r in self._rows()], [("nas.maison", "example.org")]
        )

    def test_the_zone_itself_is_refused_with_its_reason(self) -> None:
        result = self._import(rewrites=[{
            "domain": "example.net", "answer": "10.0.0.8", "_provider_id": 1, "_zone": "example.net",
        }])

        self.assertEqual(result["imported"], 0, result)
        self.assertEqual(len(result["errors"]), 1, result)
        self.assertIn("zone example.net itself", result["errors"][0])
        self.assertEqual(self._rows(), [])

    def test_a_single_label_name_is_still_refused_for_its_missing_dot(self) -> None:
        result = self._import(rewrites=[{"domain": "localhost", "answer": "127.0.0.1", "_provider_id": 1}])

        self.assertEqual(result["imported"], 0, result)
        self.assertIn("dot", result["errors"][0])

    def test_a_dns_record_imported_alone_carries_no_port(self) -> None:
        """A record names an address, not a service listening on port 80 of it."""
        self._import(rewrites=[{"domain": "vpn.vxlab.test", "answer": "10.0.0.5", "_provider_id": 1}])

        self.assertEqual(self._rows()[0]["target_port"], 0)


class AnExistingServiceIsFoundByItsWholeNameTests(_ScanTestCase):
    """A service the editor stored as `nas.maison` under `example.org` is `nas.maison.example.org`.

    With no declared domain the import splits that name as `nas` + `maison.example.org`, and
    it used to look for that split: it found nothing, and made a second service.
    """

    def test_a_dns_record_links_to_it_instead_of_making_a_second_one(self) -> None:
        self._track("nas.maison", "example.org")

        result = self._import(rewrites=[{
            "domain": "nas.maison.example.org", "answer": "10.0.0.9", "_provider_id": 1,
            "_zone": "maison.example.org",
        }])

        self.assertEqual((result["imported"], result["linked"]), (0, 1), result)
        self.assertEqual(len(self._rows()), 1)

    def test_a_route_is_set_aside_instead_of_making_a_second_one(self) -> None:
        self._track("nas.maison", "example.org")
        route = {**_host("nas.maison.example.org"), "_provider_id": 2, "_provider_name": "NPM"}

        result = self._import(hosts=[route])

        self.assertEqual(result["imported"], 0, result)
        self.assertEqual(len(result["skipped"]), 1, result)
        self.assertEqual(len(self._rows()), 1)


if __name__ == "__main__":
    unittest.main()
