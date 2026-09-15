"""A refused DNS listing is not a zone that holds nothing, on the five other providers.

Cloudflare was the first: one broad handler around the whole sweep, and a list too short
to be the truth handed back as though it were. The other five arrive at the same answer by
different routes. AdGuard and Pi-hole answered [] to any failure, which their own suites
now hold them to; the three here needed more than that, and this is where they are held.

Technitium skipped a zone with `continue` on any non-200, sat inside an outer handler that
returned [], and returned [] again when the login failed. deSEC and PowerDNS each built the
three answers on purpose, one layer down -- `_get_all` returns None "because one means the
account has no records, the other means we do not know", `_zone_rrsets` returns None
because flattening both "made a 403 look like an empty RRset" -- and then spelt `or []` in
`list_rewrites`, which is the one caller that acts on the difference.

PowerDNS said so in a comment: a zone it cannot read "contributes nothing here. That is
the one place the distinction is safely ignorable: listing is read-only." The sweep is
read-only. What reads the sweep is not: the push creates the record it does not find, and
the drift check reports it missing.

The zone lookups carry a second harm the listing does not. Technitium's `_find_zone`
answering "no zone covers this name" sends `add_rewrite` to `_zone_fallback`, which guesses
the zone from the last two labels -- so a refused zone list made it write into the parent
of a delegated zone and report success. Those now raise, and None goes back to meaning what
the write paths read it as: the server holds no zone for this name.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app import models
from app.api import sync as sync_api
from app.providers.base import ProviderListingRefused
from app.providers.desec import DesecProvider
from app.providers.powerdns import PowerDNSProvider
from app.providers.technitium import TechnitiumProvider


def _response(status: int = 200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {} if body is None else body
    r.links = {}
    return r


class DesecListingTests(unittest.TestCase):
    """deSEC kept the third answer in its helpers and dropped it in `list_rewrites`."""

    def setUp(self) -> None:
        self.provider = DesecProvider("", "", "token")

    def _serve(self, domains, rrsets):
        def _get(url, **_kw):
            if url.endswith("/domains/"):
                return _response(200, domains) if domains is not None else _response(403)
            return _response(200, rrsets) if rrsets is not None else _response(403)

        self.provider.session.get = MagicMock(side_effect=_get)

    def test_a_complete_listing_is_unchanged(self) -> None:
        """What the guard must not disturb: the answer when the API did finish."""
        self._serve(
            [{"name": "example.com", "minimum_ttl": 3600}],
            [
                {"subname": "app", "type": "A", "records": ["198.51.100.7"]},
                {"subname": "", "type": "CNAME", "records": ["edge.example.net."]},
                {"subname": "mail", "type": "MX", "records": ["10 mx.example.com."]},
            ],
        )

        got = self.provider.list_rewrites()

        self.assertEqual(
            {(r["domain"], r["answer"], r["type"]) for r in got},
            {
                ("app.example.com", "198.51.100.7", "A"),
                ("example.com", "edge.example.net", "CNAME"),
            },
        )

    def test_a_refused_domain_list_is_not_an_account_without_domains(self) -> None:
        self._serve(None, [])
        with self.assertRaises(ProviderListingRefused):
            self.provider.list_rewrites()

    def test_a_refused_record_read_is_not_a_domain_without_records(self) -> None:
        """The domains listed, so the sweep used to return the part that answered."""
        self._serve([{"name": "example.com"}], None)
        with self.assertRaises(ProviderListingRefused):
            self.provider.list_rewrites()

    def test_a_genuinely_empty_account_still_lists_as_empty(self) -> None:
        self._serve([], [])
        self.assertEqual(self.provider.list_rewrites(), [])

    def test_find_domain_still_answers_none_when_no_domain_covers_the_name(self) -> None:
        """None means the account holds no domain for it. That answer has to survive."""
        self._serve([{"name": "example.com"}], [])
        self.assertIsNone(self.provider._find_domain("app.elsewhere.test"))

    def test_find_domain_raises_rather_than_reporting_no_such_domain(self) -> None:
        self._serve(None, [])
        with self.assertRaises(ProviderListingRefused):
            self.provider._find_domain("app.example.com")


class TechnitiumZoneGuessTests(unittest.TestCase):
    """The harm a short listing does not do, and a refused zone list did.

    `add_rewrite` asks `_find_zone` which zone holds the name and, when none does, guesses
    one from the last two labels. The guess is right often enough to be useful and wrong
    exactly when the name sits in a delegated child zone -- which is the case `_find_zone`
    exists to catch. A zone list that answered [] on any failure made every name look
    uncovered, so the guess ran on every one of them.
    """

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"
        self.provider._ensure_token = MagicMock(return_value=True)
        self.writes: list[dict] = []

    def _serve(self, zones):
        def _get(url, **kwargs):
            params = kwargs.get("params", {})
            if "zones/list" in url:
                if zones is None:
                    return _response(403, {"status": "error"})
                return _response(200, {"status": "ok", "response": {
                    "zones": [{"name": z} for z in zones]}})
            if "records/add" in url:
                self.writes.append(dict(params))
                return _response(200, {"status": "ok"})
            return _response(200, {"status": "ok", "response": {"records": []}})

        self.provider.session.get = MagicMock(side_effect=_get)

    def test_a_delegated_zone_is_found_when_the_list_answers(self) -> None:
        self._serve(["lab.test", "sub.lab.test"])

        self.assertTrue(self.provider.add_rewrite("app.sub.lab.test", "10.0.0.5"))

        self.assertEqual(self.writes[0]["zone"], "sub.lab.test")

    def test_a_refused_zone_list_does_not_become_a_guessed_zone(self) -> None:
        """This used to write into `lab.test`, the parent, and report success."""
        self._serve(None)

        with self.assertRaises(ProviderListingRefused):
            self.provider.add_rewrite("app.sub.lab.test", "10.0.0.5")

        self.assertEqual(self.writes, [])

    def test_the_fallback_still_runs_when_no_zone_covers_the_name(self) -> None:
        """A server that really holds no covering zone is the case the guess is for."""
        self._serve(["other.test"])

        self.assertTrue(self.provider.add_rewrite("app.lab.test", "10.0.0.5"))

        self.assertEqual(self.writes[0]["zone"], "lab.test")


def _request(method: str = "GET", path: str = "/"):
    from starlette.requests import Request

    return Request({"type": "http", "method": method, "path": path, "headers": []})


class PushAgainstPowerDNSTests(unittest.TestCase):
    """The push, against PowerDNSProvider itself rather than a stand-in.

    The zone holds the record already. The read of the zone body fails, the sweep used to
    skip that zone in silence, and the push read the silence as "the name is not there"
    and wrote. PowerDNS writes a record *set*, so that write is a REPLACE, and the set it
    replaces is the one the read could not see.
    """

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "d56.test.db")
        models.init_db()
        self.addCleanup(self._restore)

        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")
        self.patches: list = []
        for p in (
            patch.object(sync_api, "require_auth", lambda _r, scope=None: None),
            patch.object(sync_api, "create_provider", lambda _row: self.provider),
        ):
            p.start()
            self.addCleanup(p.stop)

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (5, 'PowerDNS', 'powerdns', 'http://pdns:8081', '', 'key', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'dns',
                       5, '198.51.100.7', 'manual')"""
        )
        conn.commit()
        conn.close()

    def _restore(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _serve(self, *, zone_body_ok: bool):
        self.patched: list = []

        def _get(url, **_kw):
            if url.endswith("/zones"):
                return _response(200, [{"id": "example.com.", "name": "example.com."}])
            if not zone_body_ok:
                return _response(403, {})
            return _response(200, {"rrsets": [{
                "name": "app.example.com.", "type": "A", "ttl": 300,
                "records": [{"content": "198.51.100.7", "disabled": False},
                            {"content": "198.51.100.8", "disabled": False}],
            }]})

        def _patch(url, **kwargs):
            self.patched.append(kwargs.get("json"))
            return _response(204, {})

        self.provider.session.get = MagicMock(side_effect=_get)
        self.provider.session.patch = MagicMock(side_effect=_patch)

    def test_drift_reports_a_read_it_could_not_do_not_a_missing_record(self) -> None:
        """The record is right there. The zone body would not open.

        The sweep used to skip that zone, `/drift` read the silence as the record being
        gone, and the operator was shown `missing_dns_rewrite` on a service that was
        serving. The push guard one layer down keeps a refused read from destroying the
        record set, but nothing kept it from misreporting one.
        """
        self._serve(zone_body_ok=False)

        drift = sync_api.service_drift(1, _request())

        kinds = {issue["type"] for issue in drift["issues"]}
        self.assertIn("dns_check_failed", kinds, drift)
        self.assertNotIn("missing_dns_rewrite", kinds, drift)

    def test_drift_on_a_zone_it_can_read_reports_nothing(self) -> None:
        """The record is there and correct, so a readable zone drifts on nothing."""
        self._serve(zone_body_ok=True)

        drift = sync_api.service_drift(1, _request())

        kinds = {issue["type"] for issue in drift["issues"]}
        self.assertNotIn("dns_check_failed", kinds, drift)
        self.assertNotIn("missing_dns_rewrite", kinds, drift)

    def test_a_zone_it_could_not_read_never_becomes_a_write(self) -> None:
        """The set-level guard, held in place while the listing above it changes."""
        self._serve(zone_body_ok=False)

        result = sync_api.push_service(1, _request("POST"))

        self.assertEqual(self.patched, [])
        self.assertFalse(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
