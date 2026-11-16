"""A Cloudflare id Vauxtra stores is written into an API path, so it must have an id's shape.

The account id and the tunnel id of a `cloudflare_tunnel` provider were interpolated as they
were stored: `/accounts/{account}/cfd_tunnel/{tunnel}/configurations`. A `write` key may change
both without bringing a new token, and `../` in either aimed the stored token at another
endpoint of the account. The zone id of the Cloudflare DNS provider is written the same way.

Two places hold the line: the write refuses a value of the wrong shape and says which one,
and the provider treats one it finds stored anyway (a restored backup can hold anything) as
absent, which every method already handles.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pydantic
from fastapi import HTTPException
from starlette.requests import Request

import app.db as db
from app import models
from app.api import providers as providers_api
from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

ACCOUNT = "0123456789abcdef0123456789abcdef"
TUNNEL = "12345678-1234-1234-1234-123456789abc"
ZONE = "fedcba9876543210fedcba9876543210"

# What a tampered or restored row may hold, each one a path of its own once interpolated.
MALFORMED = ("../../zones/z1", "acc", "x/y", f"{ACCOUNT}/", f"{TUNNEL}?a=1", "t1")


def _request(method="PUT", path="/api/providers/1"):
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class TheTunnelProviderTreatsAMalformedIdAsAbsentTests(unittest.TestCase):
    def _provider(self, account, tunnel):
        provider = CloudflareTunnelProvider("", account, "token", {"tunnel_id": tunnel})
        provider._request = MagicMock(return_value=None)
        return provider

    def test_a_malformed_account_or_tunnel_id_is_never_sent(self):
        for bad in MALFORMED:
            with self.subTest(bad=bad):
                provider = self._provider(bad, bad)
                self.assertEqual((provider.account_id, provider.tunnel_id), ("", ""))
                # Every route the provider has reads the tunnel configuration first.
                self.assertIsNone(provider._get_configuration())
                self.assertFalse(provider.create_host("app.example.com", "192.168.1.10", 80))
                provider._request.assert_not_called()

    def test_a_malformed_tunnel_id_under_a_good_account_falls_back_on_detection(self):
        provider = self._provider(ACCOUNT, "../x")
        provider._request.return_value = [{"id": TUNNEL, "name": "home"}]
        self.assertEqual(provider._resolve_tunnel_id(), TUNNEL)
        self.assertEqual(provider._request.call_args.args[1], f"/accounts/{ACCOUNT}/cfd_tunnel")

    def test_an_id_with_spaces_or_capitals_is_still_the_same_id(self):
        provider = self._provider(f"  {ACCOUNT.upper()} ", f" {TUNNEL.upper()}")
        self.assertEqual((provider.account_id, provider.tunnel_id), (ACCOUNT, TUNNEL))

    def test_extra_that_is_not_an_object_does_not_raise(self):
        provider = CloudflareTunnelProvider("", ACCOUNT, "token", "not an object")
        self.assertEqual(provider.tunnel_id, "")

    def test_a_detected_tunnel_of_the_wrong_shape_is_not_kept(self):
        provider = self._provider(ACCOUNT, "")
        provider._request.return_value = [{"id": "../../zones/z1", "name": "odd"}]
        self.assertEqual(provider._resolve_tunnel_id(), "")


OTHER_TUNNEL = "87654321-4321-4321-4321-cba987654321"
HOST = "app.example.com"
FOREIGN_RECORD = "a" * 32
OWN_RECORD = "b" * 32


def _cname(record_id, target):
    return {"id": record_id, "type": "CNAME", "name": HOST, "content": target, "proxied": True}


class _FakeCloudflare:
    """`_request` of the tunnel provider: the account's tunnels, its zones, one name's CNAMEs."""

    def __init__(self, tunnels, records, zones=None):
        self.tunnels = list(tunnels)
        self.records = list(records)
        self.zones = [{"id": ZONE, "name": "example.com"}] if zones is None else list(zones)
        self.written: list[tuple[str, str]] = []

    def __call__(self, method, path, **kw):
        if method == "GET" and path == f"/accounts/{ACCOUNT}/cfd_tunnel":
            return [{"id": t, "name": f"tunnel-{n}"} for n, t in enumerate(self.tunnels)]
        if method == "GET" and path.endswith("/configurations"):
            return {"config": {"ingress": [
                {"hostname": HOST, "service": "http://10.0.0.5:8080"},
                {"service": "http_status:404"},
            ]}}
        if method == "GET" and path == "/zones":
            name = (kw.get("params") or {}).get("name")
            return [z for z in self.zones if z["name"] == name]
        if method == "GET" and path.endswith("/dns_records"):
            return list(self.records)
        self.written.append((method, path))
        return {"id": path.rsplit("/", 1)[-1]}


class WithoutItsTunnelNoCnameIsDeletedTests(unittest.TestCase):
    """A CNAME of the name is the provider's to delete only when it aims at its own tunnel.

    A tunnel id of the wrong shape counts as absent, and with several tunnels in the account
    nothing says which one is this provider's. `delete_host` then deleted the name's CNAME
    whatever it aimed at, another tunnel or a host outside Cloudflare, and an empty id
    already did so before ids were held to their shape.
    """

    def _delete(self, tunnel_id, tunnels, records):
        provider = CloudflareTunnelProvider("", ACCOUNT, "token", {"tunnel_id": tunnel_id})
        fake = _FakeCloudflare(tunnels, records)
        provider._request = fake
        # Several tunnels: the provider says so in the journal, which is not under test.
        with patch("app.models.add_log"):
            done = provider.delete_host(HOST)
        return done, [path for method, path in fake.written if method == "DELETE"]

    def test_a_cname_aimed_elsewhere_is_never_deleted(self):
        two = [TUNNEL, OTHER_TUNNEL]
        for tunnel_id, tunnels in (("not-a-uuid", two), ("../x", two), ("", two),
                                   (TUNNEL, two), ("not-a-uuid", [TUNNEL])):
            with self.subTest(tunnel_id=tunnel_id, tunnels=len(tunnels)):
                _done, deleted = self._delete(
                    tunnel_id, tunnels, [_cname(FOREIGN_RECORD, "elsewhere.example.com")],
                )
                self.assertEqual(deleted, [])

    def test_without_its_tunnel_the_name_is_reported_as_not_withdrawn(self):
        for tunnel_id in ("not-a-uuid", ""):
            with self.subTest(tunnel_id=tunnel_id):
                done, deleted = self._delete(
                    tunnel_id, [TUNNEL, OTHER_TUNNEL],
                    [_cname(OWN_RECORD, f"{TUNNEL}.cfargotunnel.com")],
                )
                self.assertEqual((done, deleted), (False, []))

    def test_the_cname_of_its_own_tunnel_is_deleted(self):
        for tunnel_id, tunnels in ((TUNNEL, [TUNNEL, OTHER_TUNNEL]), ("not-a-uuid", [TUNNEL])):
            with self.subTest(tunnel_id=tunnel_id):
                done, deleted = self._delete(
                    tunnel_id, tunnels, [_cname(OWN_RECORD, f"{TUNNEL}.cfargotunnel.com")],
                )
                self.assertEqual((done, deleted),
                                 (True, [f"/zones/{ZONE}/dns_records/{OWN_RECORD}"]))


class IdsReadFromCloudflareHaveTheirShapeTests(unittest.TestCase):
    """An id read from an answer is written into the next path, so it is held to its shape."""

    def _provider(self, fake):
        provider = CloudflareTunnelProvider("", ACCOUNT, "token", {"tunnel_id": TUNNEL})
        provider._request = fake
        return provider

    def test_a_listed_zone_whose_id_has_not_the_shape_of_one_is_skipped(self):
        odd = {"id": "../../accounts/x", "name": "example.com"}
        alone = _FakeCloudflare([TUNNEL], [], zones=[odd])
        self.assertEqual(self._provider(alone)._find_zone(HOST), "")
        both = _FakeCloudflare([TUNNEL], [], zones=[odd, {"id": f" {ZONE.upper()} ", "name": "example.com"}])
        self.assertEqual(self._provider(both)._find_zone(HOST), ZONE)

    def test_a_record_whose_id_has_not_the_shape_of_one_is_never_written_to(self):
        for bad in ("../../zones/x", "rec-1", "", FOREIGN_RECORD[:-1], f"{OWN_RECORD}/x"):
            with self.subTest(bad=bad):
                fake = _FakeCloudflare([TUNNEL], [_cname(bad, "elsewhere.example.com")])
                self._provider(fake)._ensure_dns_record(HOST)
                fake.records = [_cname(bad, f"{TUNNEL}.cfargotunnel.com")]
                self._provider(fake)._delete_dns_record(HOST)
                self.assertEqual([m for m, _path in fake.written if m in ("PUT", "DELETE")], [])


class TheDnsProviderTreatsAMalformedZoneIdAsAbsentTests(unittest.TestCase):
    def _provider(self, zone_id):
        import app.providers.cloudflare as cf_module

        orig = (cf_module._HAS_CF, cf_module._cf)
        cf_module._HAS_CF, cf_module._cf = True, MagicMock()
        try:
            provider = cf_module.CloudflareProvider(
                "https://api.cloudflare.com/client/v4", zone_id, "token", {}
            )
        finally:
            cf_module._HAS_CF, cf_module._cf = orig
        provider._client = MagicMock()
        return provider

    def test_a_malformed_zone_id_is_looked_up_by_name_instead_of_sent(self):
        found = MagicMock()
        found.id, found.name = ZONE, "example.com"
        for bad in MALFORMED:
            with self.subTest(bad=bad):
                provider = self._provider(bad)
                provider._client.zones.list.side_effect = (
                    lambda name, per_page, _found=found: [_found] if name == "example.com" else []
                )
                self.assertEqual(provider._find_zone("app.example.com"), ZONE)

    def test_a_zone_id_of_the_right_shape_is_still_used_as_configured(self):
        provider = self._provider(f" {ZONE.upper()} ")
        self.assertEqual(provider._find_zone("app.example.com"), ZONE)
        provider._client.zones.list.assert_not_called()


class TheWriteRefusesAMalformedIdTests(unittest.TestCase):
    def _refused(self, model, **fields):
        with self.assertRaises(pydantic.ValidationError) as caught:
            model(name="CF", password="token", **fields)
        return str(caught.exception)

    def test_a_tunnel_provider_is_refused_a_malformed_account_or_tunnel_id(self):
        # `validate-draft` takes the same model and sends the ids to Cloudflare as well.
        for model in (providers_api.ProviderIn, providers_api.ProviderDraftValidationIn):
            for bad in MALFORMED:
                with self.subTest(model=model.__name__, bad=bad):
                    said = self._refused(model, type="cloudflare_tunnel", username=bad,
                                         extra={"tunnel_id": TUNNEL})
                    self.assertIn("The Cloudflare account ID must be 32 hexadecimal characters",
                                  said)
                    said = self._refused(model, type="cloudflare_tunnel", username=ACCOUNT,
                                         extra={"tunnel_id": bad})
                    self.assertIn("The Cloudflare tunnel ID must be a UUID", said)

    def test_a_dns_provider_is_refused_a_malformed_zone_id(self):
        said = self._refused(providers_api.ProviderIn, type="cloudflare", username="zone123")
        self.assertIn("The Cloudflare zone ID must be 32 hexadecimal characters", said)

    def test_an_empty_id_still_means_unset_and_other_types_are_not_judged(self):
        for fields in (
            {"type": "cloudflare_tunnel", "username": ACCOUNT, "extra": {}},
            {"type": "cloudflare_tunnel", "username": "", "extra": {"tunnel_id": "  "}},
            {"type": "cloudflare", "username": ""},
            {"type": "cloudflare", "username": ZONE.upper()},
            {"type": "npm", "username": "admin@example.com", "extra": {"tunnel_id": "t1"}},
        ):
            with self.subTest(fields=fields):
                providers_api.ProviderIn(name="CF", password="token", **fields)


class TheUpdateRefusesAMalformedIdTests(unittest.TestCase):
    """`PUT /api/providers/{pid}` carries no type, so the route judges what the body sends."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)
        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "ids.test.db")
        models.init_db()
        conn = models.get_db()
        # A row restored from a backup taken before the write refused anything.
        conn.execute("INSERT INTO providers (id, name, type, url, username, password, extra, "
                     "enabled) VALUES (1, 'Tunnel', 'cloudflare_tunnel', '', 'acc', 'x', "
                     "'{\"tunnel_id\": \"t1\"}', 1)")
        conn.commit()
        conn.close()
        self._auth = patch.object(providers_api, "require_auth", lambda _req, scope=None: None)
        self._auth.start()

    def tearDown(self):
        self._auth.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _stored(self):
        conn = models.get_db()
        try:
            row = conn.execute("SELECT name, username, extra FROM providers WHERE id=1").fetchone()
            return tuple(row)
        finally:
            conn.close()

    def test_a_malformed_id_sent_is_refused_and_nothing_is_written(self):
        before = self._stored()
        for body, said in (
            (providers_api.ProviderUpdate(name="Renamed", username="../../zones/z1"),
             "Nothing was changed -- the Cloudflare account ID must be 32 hexadecimal characters"),
            (providers_api.ProviderUpdate(username=ACCOUNT, extra={"tunnel_id": "../x"}),
             "Nothing was changed -- the Cloudflare tunnel ID must be a UUID"),
        ):
            with self.subTest(said=said):
                with self.assertRaises(HTTPException) as caught:
                    providers_api.update_provider(1, _request(), body)
                self.assertEqual((caught.exception.status_code, caught.exception.detail),
                                 (422, said))
                self.assertEqual(self._stored(), before)

    def test_well_formed_ids_are_saved(self):
        providers_api.update_provider(1, _request(), providers_api.ProviderUpdate(
            username=ACCOUNT, extra={"tunnel_id": TUNNEL}))
        self.assertEqual(self._stored()[1:], (ACCOUNT, f'{{"tunnel_id": "{TUNNEL}"}}'))

    def test_a_body_that_leaves_the_ids_out_still_saves_on_a_malformed_row(self):
        providers_api.update_provider(1, _request(), providers_api.ProviderUpdate(name="Renamed"))
        self.assertEqual(self._stored()[:2], ("Renamed", "acc"))


if __name__ == "__main__":
    unittest.main()
