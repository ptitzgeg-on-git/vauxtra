"""A DNS listing that failed must not read as a zone that holds nothing.

`CloudflareProvider.list_rewrites` collected records across every zone and every managed
type inside one broad `except` that passed, and returned what it had gathered so far. The
answer a caller got back was a list, and there was nothing in it that said whether the API
had finished speaking. A timeout on the second zone, a revoked scope, a 5xx from the edge:
each came back as a shorter list, and a shorter list means exactly one thing to every
caller, which is that the record it was looking for is not there.

That is the reading the provider could not support. Five callers act on it:

- `GET /api/providers/{pid}/dns-records` shows the operator the inventory of a zone;
- the record delete route looks the answer up when the caller did not supply one, and
  answers 404 when it cannot find it;
- `/drift` reports `missing_dns_rewrite`, or, on a disabled service, stays quiet about a
  hostname that is still resolving;
- the push path reads the current value to correct drift, and writes when it finds none;
- the import scan lists what a provider holds so the operator can adopt it.

Every one of them had already been given an honest branch to take -- a 502, a
`dns_check_failed` issue, a logged provider, a fallback to the stored address. The handler
inside the provider is what made all five unreachable. These tests hold the provider to
raising, and then hold each caller to the branch it had written and never reached.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import providers as providers_api
from app.api import sync as sync_api


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _record(rid: str, name: str, content: str, rtype: str = "A", proxied: bool = False):
    r = MagicMock()
    r.id, r.name, r.content, r.type, r.proxied = rid, name, content, rtype, proxied
    return r


def _zone(zid: str, name: str):
    z = MagicMock()
    z.id, z.name = zid, name
    return z


def _cloudflare(zone_id: str = ""):
    """A CloudflareProvider whose SDK client is a mock, built like the sibling suite's."""
    mock_module = MagicMock()
    mock_client = MagicMock()
    mock_module.Cloudflare.return_value = mock_client
    import app.providers.cloudflare as cf_module

    orig = (cf_module._HAS_CF, cf_module._cf)
    cf_module._HAS_CF, cf_module._cf = True, mock_module
    try:
        provider = cf_module.CloudflareProvider(
            "https://api.cloudflare.com/client/v4", zone_id, "test_token", {}
        )
    finally:
        cf_module._HAS_CF, cf_module._cf = orig
    provider._client = mock_client
    return provider, mock_client


class _Truncated(Exception):
    """Stands in for whatever the SDK raises: a timeout, a 403, a 5xx from the edge."""


class CloudflareListingTests(unittest.TestCase):
    """The provider itself: a listing either finished or it did not."""

    def test_a_complete_listing_still_returns_every_managed_type(self) -> None:
        """The behaviour the handler was hiding behind is unchanged."""
        p, client = _cloudflare(zone_id="zone123")
        client.dns.records.list.side_effect = lambda zone_id, type: {
            "A": [_record("r1", "app.example.com", "198.51.100.7", "A", True)],
            "AAAA": [_record("r2", "app.example.com", "2001:db8::1", "AAAA")],
            "CNAME": [_record("r3", "cdn.example.com", "edge.example.net", "CNAME")],
        }[type]

        got = p.list_rewrites()

        self.assertEqual(
            {(r["domain"], r["answer"], r["type"]) for r in got},
            {
                ("app.example.com", "198.51.100.7", "A"),
                ("app.example.com", "2001:db8::1", "AAAA"),
                ("cdn.example.com", "edge.example.net", "CNAME"),
            },
        )
        self.assertTrue(got[0]["proxied"])

    def test_a_failure_partway_through_is_raised_not_returned_short(self) -> None:
        """The A records were already collected. Handing them back is the whole defect."""
        p, client = _cloudflare(zone_id="zone123")

        def _list(zone_id, type):
            if type == "A":
                return [_record("r1", "app.example.com", "198.51.100.7", "A")]
            raise _Truncated("504 from the edge")

        client.dns.records.list.side_effect = _list

        with self.assertRaises(_Truncated):
            p.list_rewrites()

    def test_a_failed_zone_discovery_is_raised_not_an_empty_account(self) -> None:
        """Without a configured zone id, the discovery call is the whole inventory."""
        p, client = _cloudflare()
        client.zones.list.side_effect = _Truncated("403 on the zone list")

        with self.assertRaises(_Truncated):
            p.list_rewrites()

    def test_a_zone_that_lists_but_will_not_read_is_not_an_empty_zone(self) -> None:
        """PowerDNS tolerates this case because it can name it. One broad handler cannot."""
        p, client = _cloudflare()
        client.zones.list.return_value = [_zone("z1", "example.com"), _zone("z2", "other.test")]
        seen: list = []

        def _list(zone_id, type):
            seen.append(zone_id)
            if zone_id == "z2":
                raise _Truncated("token has no DNS:Read on this zone")
            return [_record("r1", "app.example.com", "198.51.100.7", "A")] if type == "A" else []

        client.dns.records.list.side_effect = _list

        with self.assertRaises(_Truncated):
            p.list_rewrites()
        self.assertIn("z1", seen)


class _RaisingLister:
    """A DNS provider that cannot finish a listing, and writes when asked."""

    def __init__(self, calls: list):
        self._calls = calls

    def list_rewrites(self):
        self._calls.append(("list_rewrites",))
        raise _Truncated("the far end stopped answering")

    def add_rewrite(self, domain, ip):
        self._calls.append(("add_rewrite", domain, ip))
        return True

    def delete_rewrite(self, domain, ip):
        self._calls.append(("delete_rewrite", domain, ip))
        return True

    def find_best_certificate(self, _domain):
        return None

    def list_hosts(self):
        return []

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._calls.append(("create_host", domain))
        return {"id": 4242}

    def update_host(self, host_id, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._calls.append(("update_host", host_id, domain))
        return True

    def delete_host(self, host_id):
        self._calls.append(("delete_host", host_id))
        return True


class _CallerCase(unittest.TestCase):
    """The routes that read a listing, each against a provider that cannot produce one."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "listing.test.db")
        models.init_db()
        self.addCleanup(self._restore)

        self.calls: list = []
        self.provider = _RaisingLister(self.calls)
        for p in (
            patch.object(providers_api, "require_auth", lambda _r, scope=None: None),
            patch.object(providers_api, "create_provider", lambda _row: self.provider),
            patch.object(sync_api, "require_auth", lambda _r, scope=None: None),
            patch.object(sync_api, "create_provider", lambda _row: self.provider),
        ):
            p.start()
            self.addCleanup(p.stop)

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    def _restore(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _seed_service(self, *, enabled: int = 1) -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, ?, 'dns',
                       3, '198.51.100.7', 'manual')""",
            (enabled,),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid


class ListingFailureReachesTheCallerTests(_CallerCase):
    def test_the_records_route_answers_502_rather_than_an_empty_inventory(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            providers_api.list_dns_records(3, _request())

        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("Failed to list DNS records", str(caught.exception.detail))

    def test_the_delete_route_answers_502_rather_than_404(self) -> None:
        """404 says the record is not there. The provider never said that."""
        with self.assertRaises(HTTPException) as caught:
            providers_api.delete_dns_record(3, "app.example.com", _request("DELETE"))

        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn(("delete_rewrite", "app.example.com", None), self.calls)

    def test_drift_reports_the_failure_instead_of_a_missing_record(self) -> None:
        sid = self._seed_service()

        issues = sync_api.service_drift(sid, _request())["issues"]
        types = [i["type"] for i in issues]

        self.assertIn("dns_check_failed", types)
        self.assertNotIn("missing_dns_rewrite", types)

    def test_drift_on_a_disabled_service_does_not_call_the_zone_clean(self) -> None:
        """Silence here reads as "nothing is resolving any more", which is the worst read."""
        sid = self._seed_service(enabled=0)

        issues = sync_api.service_drift(sid, _request())["issues"]

        self.assertIn("dns_check_failed", [i["type"] for i in issues])

    def test_a_push_does_not_write_on_an_inventory_it_could_not_read(self) -> None:
        """The reason the handler had to go: the next step after the read is a write."""
        sid = self._seed_service()

        result = sync_api.push_service(sid, _request("POST"))

        self.assertFalse(result["ok"], result)
        self.assertTrue(any(e.startswith("DNS (AdGuard):") for e in result["errors"]), result)
        self.assertEqual([c[0] for c in self.calls if c[0] != "list_rewrites"], [])

    def test_the_withdrawal_falls_back_to_the_stored_address(self) -> None:
        """The one caller whose honest branch is not a refusal: it holds a value of its own."""
        sid = self._seed_service()
        conn = models.get_db()
        svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        errors = sync_api.withdraw_service_routes(conn, svc, sid)
        conn.commit()
        conn.close()

        self.assertEqual(errors, [])
        self.assertIn(("delete_rewrite", "app.example.com", "198.51.100.7"), self.calls)


class PushAgainstTheRealProviderTests(_CallerCase):
    """The same push, against CloudflareProvider itself rather than a stand-in.

    This is the shape the defect took in production. The zone already holds a CNAME for the
    hostname; the service now wants an A record. The listing fails before it reaches the
    record, so the push reads "nothing there", takes the creation branch, and puts an A
    beside the CNAME -- then answers `{"ok": true}` and writes `DNS synced` in the journal.
    """

    def setUp(self) -> None:
        super().setUp()
        self.provider, self.client = _cloudflare(zone_id="zone123")
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (4, 'Cloudflare', 'cloudflare', 'https://api.cloudflare.com',
                       'zone123', 'token', '{}', 1)"""
        )
        conn.commit()
        conn.close()

        stale = _record("old", "app.example.com", "edge.example.net", "CNAME")

        def _list(zone_id, type, name=None):
            if name is not None:
                # `add_rewrite` asks by name and type: an A lookup does not see the CNAME.
                return [stale] if type == "CNAME" else []
            raise _Truncated("504 before the zone was read out")

        self.client.dns.records.list.side_effect = _list

    def _seed_cloudflare_service(self) -> int:
        conn = models.get_db()
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'dns',
                       4, '198.51.100.7', 'manual')""",
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def test_a_truncated_listing_never_becomes_a_second_record(self) -> None:
        sid = self._seed_cloudflare_service()

        result = sync_api.push_service(sid, _request("POST"))

        self.client.dns.records.create.assert_not_called()
        self.client.dns.records.update.assert_not_called()
        self.assertFalse(result["ok"], result)
        self.assertTrue(any(e.startswith("DNS (Cloudflare):") for e in result["errors"]), result)

        conn = models.get_db()
        messages = [r["message"] for r in conn.execute("SELECT message FROM logs").fetchall()]
        conn.close()
        self.assertFalse(any("DNS synced" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
