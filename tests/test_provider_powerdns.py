"""Unit tests for PowerDNSProvider — all HTTP calls are mocked.

The fake below is stateful on purpose. PowerDNS `changetype: REPLACE` overwrites the
whole record set for a `(name, type)` pair, so the bug worth catching is not a malformed
request but a well-formed one that silently drops a sibling address. A fake that only
records calls cannot see that; this one applies the patch and lets the test read the
zone back.
"""

import unittest
from unittest.mock import MagicMock

import requests

from app.config import PROVIDER_TIMEOUT, encrypt_secret
from app.providers.base import TimeoutSession
from app.providers.factory import PROVIDER_TYPES, create_provider
from app.providers.powerdns import PowerDNSProvider


def _response(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = {} if json_data is None else json_data
    return r


def _rrset(name: str, rtype: str, *contents: str, ttl: int = 3600, disabled: bool = False) -> dict:
    return {
        "name": name,
        "type": rtype,
        "ttl": ttl,
        "records": [{"content": c, "disabled": disabled} for c in contents],
    }


class _FakePowerDNS:
    """The subset of the API the provider touches, with the zone state it mutates."""

    def __init__(self, zones: dict, *, with_ids: bool = True):
        self.zones = zones
        self.with_ids = with_ids
        self.patches: list[dict] = []

    # Routing is by URL, not by call order: the provider is free to read the zone list
    # and a zone body in whichever order its logic needs.
    def get(self, url: str, *_a, **_kw):
        if url.endswith("/zones"):
            return _response(200, [
                ({"id": z, "name": z} if self.with_ids else {"name": z})
                for z in self.zones
            ])
        if "/zones/" in url:
            zone_id = url.split("/zones/", 1)[1]
            if zone_id not in self.zones:
                return _response(404)
            return _response(200, {"id": zone_id, "name": zone_id, "rrsets": self.zones[zone_id]})
        return _response(200, {"id": "localhost", "type": "Server"})

    def patch(self, url: str, json=None, *_a, **_kw):
        self.patches.append(json)
        zone_id = url.split("/zones/", 1)[1]
        if zone_id not in self.zones:
            return _response(404)
        for rrset in (json or {}).get("rrsets", []):
            name, rtype = rrset["name"], rrset["type"]
            kept = [s for s in self.zones[zone_id]
                    if not (s["name"] == name and s["type"] == rtype)]
            if rrset["changetype"] == "REPLACE":
                kept.append({"name": name, "type": rtype,
                             "ttl": rrset["ttl"], "records": rrset["records"]})
            self.zones[zone_id] = kept
        return _response(204)

    def contents(self, zone_id: str, name: str, rtype: str) -> list[str] | None:
        """The values currently held by a record set, or None when it does not exist."""
        for rrset in self.zones.get(zone_id, []):
            if rrset["name"] == name and rrset["type"] == rtype:
                return [r["content"] for r in rrset["records"]]
        return None


def _wire(provider: PowerDNSProvider, fake: _FakePowerDNS) -> _FakePowerDNS:
    provider.session.get = MagicMock(side_effect=fake.get)
    provider.session.patch = MagicMock(side_effect=fake.patch)
    return fake


class TestPowerDNSHelpers(unittest.TestCase):

    def test_fqdn_adds_the_root_dot_once(self):
        self.assertEqual(PowerDNSProvider._fqdn("a.home.lab"), "a.home.lab.")
        self.assertEqual(PowerDNSProvider._fqdn("a.home.lab."), "a.home.lab.")
        self.assertEqual(PowerDNSProvider._fqdn("  a.home.lab  "), "a.home.lab.")

    def test_fqdn_of_nothing_is_the_root(self):
        self.assertEqual(PowerDNSProvider._fqdn(""), ".")

    def test_relative_strips_the_dot_and_lowercases(self):
        self.assertEqual(PowerDNSProvider._relative("A.Home.Lab."), "a.home.lab")

    def test_record_type_follows_the_value(self):
        self.assertEqual(PowerDNSProvider._record_type("192.168.1.10"), "A")
        self.assertEqual(PowerDNSProvider._record_type("2001:db8::1"), "AAAA")
        self.assertEqual(PowerDNSProvider._record_type("origin.example.com"), "CNAME")

    def test_only_a_cname_target_is_made_absolute(self):
        self.assertEqual(PowerDNSProvider._content("CNAME", "origin.example.com"), "origin.example.com.")
        self.assertEqual(PowerDNSProvider._content("A", "192.168.1.10"), "192.168.1.10")

    def test_server_id_defaults_to_localhost(self):
        self.assertEqual(PowerDNSProvider("http://pdns:8081", "", "k").server_id, "localhost")
        self.assertEqual(PowerDNSProvider("http://pdns:8081", "  ", "k").server_id, "localhost")
        self.assertEqual(PowerDNSProvider("http://pdns:8081", "ns1", "k").server_id, "ns1")

    def test_trailing_slash_in_the_url_does_not_double_up(self):
        p = PowerDNSProvider("http://pdns:8081/", "", "k")
        self.assertEqual(p._api("/zones"), "http://pdns:8081/api/v1/servers/localhost/zones")

    def test_api_key_travels_as_a_header(self):
        p = PowerDNSProvider("http://pdns:8081", "", "  secret  ")
        self.assertEqual(p.session.headers["X-API-Key"], "secret")


class TestPowerDNSConnection(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def test_connection_succeeds_on_a_server_object(self):
        self.provider.session.get = MagicMock(return_value=_response(200, {"id": "localhost"}))
        self.assertTrue(self.provider.test_connection())

    def test_connection_fails_on_a_bad_key(self):
        self.provider.session.get = MagicMock(return_value=_response(401, {}))
        self.assertFalse(self.provider.test_connection())

    def test_connection_fails_when_the_body_is_not_a_server(self):
        """A reverse proxy answering 200 with its own page is not PowerDNS."""
        self.provider.session.get = MagicMock(return_value=_response(200, []))
        self.assertFalse(self.provider.test_connection())

    def test_connection_fails_on_an_empty_server_object(self):
        self.provider.session.get = MagicMock(return_value=_response(200, {}))
        self.assertFalse(self.provider.test_connection())

    def test_connection_fails_on_a_network_error(self):
        self.provider.session.get = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertFalse(self.provider.test_connection())

    def test_connection_fails_on_a_body_that_is_not_json(self):
        r = _response(200)
        r.json.side_effect = ValueError("no JSON")
        self.provider.session.get = MagicMock(return_value=r)
        self.assertFalse(self.provider.test_connection())

    def test_connection_fails_without_a_url(self):
        """Nothing is sent: an empty base would resolve to a relative path."""
        p = PowerDNSProvider("", "", "key")
        p.session.get = MagicMock()
        self.assertFalse(p.test_connection())
        p.session.get.assert_not_called()


class TestPowerDNSZones(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def test_find_zone_picks_the_longest_match(self):
        _wire(self.provider, _FakePowerDNS({"lab.": [], "home.lab.": []}))
        self.assertEqual(self.provider._find_zone("a.home.lab"), "home.lab.")

    def test_find_zone_matches_the_apex_itself(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertEqual(self.provider._find_zone("home.lab"), "home.lab.")

    def test_find_zone_refuses_a_suffix_that_is_not_a_label_boundary(self):
        """`nothome.lab` ends with `home.lab` as text but is not inside that zone."""
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertIsNone(self.provider._find_zone("nothome.lab"))

    def test_find_zone_returns_none_when_nothing_covers_the_name(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertIsNone(self.provider._find_zone("a.example.com"))

    def test_zone_id_falls_back_to_the_name(self):
        """Older API versions omit `id` from the list view."""
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}, with_ids=False))
        self.assertEqual(self.provider._find_zone("a.home.lab"), "home.lab.")

    def test_a_refused_zone_list_is_empty_not_an_error(self):
        self.provider.session.get = MagicMock(return_value=_response(403, {}))
        self.assertEqual(self.provider._list_zones(), [])

    def test_a_zone_list_that_is_not_a_list_is_empty(self):
        self.provider.session.get = MagicMock(return_value=_response(200, {"error": "nope"}))
        self.assertEqual(self.provider._list_zones(), [])


class TestPowerDNSListRewrites(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def test_managed_types_are_reported_in_the_stored_shape(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
            _rrset("v6.home.lab.", "AAAA", "2001:db8::1"),
            _rrset("alias.home.lab.", "CNAME", "App.Home.Lab."),
        ]}))
        got = {(r["domain"], r["answer"], r["type"]) for r in self.provider.list_rewrites()}
        self.assertEqual(got, {
            ("app.home.lab", "192.168.1.10", "A"),
            ("v6.home.lab", "2001:db8::1", "AAAA"),
            ("alias.home.lab", "app.home.lab", "CNAME"),
        })

    def test_unmanaged_types_are_skipped(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("home.lab.", "SOA", "ns1.home.lab. hostmaster.home.lab. 1 10800 3600 604800 3600"),
            _rrset("home.lab.", "NS", "ns1.home.lab."),
            _rrset("home.lab.", "TXT", "v=spf1 -all"),
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertEqual(self.provider.list_rewrites(),
                         [{"domain": "app.home.lab", "answer": "192.168.1.10", "type": "A"}])

    def test_a_disabled_record_is_not_a_rewrite(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("off.home.lab.", "A", "192.168.1.10", disabled=True),
        ]}))
        self.assertEqual(self.provider.list_rewrites(), [])

    def test_every_value_of_a_multi_valued_set_is_listed(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10", "192.168.1.11"),
        ]}))
        self.assertEqual([r["answer"] for r in self.provider.list_rewrites()],
                         ["192.168.1.10", "192.168.1.11"])

    def test_records_are_gathered_across_zones(self):
        _wire(self.provider, _FakePowerDNS({
            "home.lab.": [_rrset("app.home.lab.", "A", "192.168.1.10")],
            "other.lab.": [_rrset("app.other.lab.", "A", "192.168.1.20")],
        }))
        self.assertEqual({r["domain"] for r in self.provider.list_rewrites()},
                         {"app.home.lab", "app.other.lab"})


class TestPowerDNSAddRewrite(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def test_a_new_name_gets_its_record_set(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertTrue(self.provider.add_rewrite("app.home.lab", "192.168.1.10"))
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"), ["192.168.1.10"])

    def test_adding_an_address_keeps_the_one_already_there(self):
        """The whole point of the read-modify-write: REPLACE would drop .10."""
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertTrue(self.provider.add_rewrite("app.home.lab", "192.168.1.11"))
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"),
                         ["192.168.1.10", "192.168.1.11"])

    def test_a_value_already_present_writes_nothing(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertTrue(self.provider.add_rewrite("app.home.lab", "192.168.1.10"))
        self.assertEqual(fake.patches, [])

    def test_an_existing_ttl_is_preserved(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10", ttl=60),
        ]}))
        self.provider.add_rewrite("app.home.lab", "192.168.1.11")
        self.assertEqual(fake.patches[0]["rrsets"][0]["ttl"], 60)

    def test_a_new_set_gets_the_default_ttl(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.provider.add_rewrite("app.home.lab", "192.168.1.10")
        self.assertEqual(fake.patches[0]["rrsets"][0]["ttl"], 3600)

    def test_an_ipv6_value_lands_in_the_aaaa_set(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertTrue(self.provider.add_rewrite("app.home.lab", "2001:db8::1"))
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "AAAA"), ["2001:db8::1"])
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"), ["192.168.1.10"])

    def test_a_cname_replaces_rather_than_extends(self):
        """A name carries at most one CNAME; appending would build a rejected set."""
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("alias.home.lab.", "CNAME", "old.example.com."),
        ]}))
        self.assertTrue(self.provider.add_rewrite("alias.home.lab", "new.example.com"))
        self.assertEqual(fake.contents("home.lab.", "alias.home.lab.", "CNAME"),
                         ["new.example.com."])

    def test_a_name_outside_every_zone_is_refused(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertFalse(self.provider.add_rewrite("app.example.com", "192.168.1.10"))
        self.assertEqual(fake.patches, [])

    def test_a_blank_value_is_refused_and_not_written_to_the_root(self):
        """`_fqdn("")` is ".", the DNS root -- a blank value must not become a CNAME to it."""
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertFalse(self.provider.add_rewrite("app.home.lab", "   "))
        self.assertEqual(fake.patches, [])

    def test_a_rejected_patch_is_reported_as_a_failure(self):
        self.provider.session.get = MagicMock(
            side_effect=_FakePowerDNS({"home.lab.": []}).get)
        self.provider.session.patch = MagicMock(return_value=_response(422, {"error": "nope"}))
        self.assertFalse(self.provider.add_rewrite("app.home.lab", "192.168.1.10"))

    def test_a_network_error_on_the_patch_is_reported_as_a_failure(self):
        self.provider.session.get = MagicMock(
            side_effect=_FakePowerDNS({"home.lab.": []}).get)
        self.provider.session.patch = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertFalse(self.provider.add_rewrite("app.home.lab", "192.168.1.10"))


class TestPowerDNSDeleteRewrite(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def test_the_last_value_removes_the_record_set(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertTrue(self.provider.delete_rewrite("app.home.lab", "192.168.1.10"))
        self.assertIsNone(fake.contents("home.lab.", "app.home.lab.", "A"))
        self.assertEqual(fake.patches[0]["rrsets"][0]["changetype"], "DELETE")

    def test_removing_one_value_keeps_the_others(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10", "192.168.1.11"),
        ]}))
        self.assertTrue(self.provider.delete_rewrite("app.home.lab", "192.168.1.10"))
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"), ["192.168.1.11"])

    def test_deleting_what_is_not_there_succeeds_without_writing(self):
        """The caller asked for an absent record; the postcondition already holds."""
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertTrue(self.provider.delete_rewrite("app.home.lab", "192.168.1.10"))
        self.assertEqual(fake.patches, [])

    def test_deleting_a_value_absent_from_an_existing_set_writes_nothing(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertTrue(self.provider.delete_rewrite("app.home.lab", "192.168.1.99"))
        self.assertEqual(fake.patches, [])
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"), ["192.168.1.10"])

    def test_a_cname_is_matched_in_its_absolute_form(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": [
            _rrset("alias.home.lab.", "CNAME", "origin.example.com."),
        ]}))
        self.assertTrue(self.provider.delete_rewrite("alias.home.lab", "origin.example.com"))
        self.assertIsNone(fake.contents("home.lab.", "alias.home.lab.", "CNAME"))

    def test_a_name_outside_every_zone_is_refused(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertFalse(self.provider.delete_rewrite("app.example.com", "192.168.1.10"))


class _RefusesTheZoneBody(_FakePowerDNS):
    """Lists zones normally, but will not hand over the contents of one.

    This is not a contrived shape. A PowerDNS API key scoped to a subset of zones lists
    them all and 403s on the body of the others; a server under load 500s; a proxy in
    front of it returns HTML. In every one of those cases the zone list still answers.
    """

    def __init__(self, zones: dict, *, status: int = 403, network_error: bool = False):
        super().__init__(zones)
        self.status = status
        self.network_error = network_error

    def get(self, url: str, *a, **kw):
        if "/zones/" in url:
            if self.network_error:
                raise requests.ConnectionError("connection reset")
            return _response(self.status, {"error": "Not allowed"})
        return super().get(url, *a, **kw)


class TestPowerDNSRefusedRead(unittest.TestCase):
    """A read the server refused is not a zone with nothing in it.

    PowerDNS writes a record *set*: `add_rewrite` reads what is there, appends, and sends
    the whole set back with `changetype: REPLACE`. So the value the read returns is the
    value the write preserves, and a read that flattens a 403 to an empty list makes the
    write delete every sibling address of the name -- the second A record of a
    round-robin, the AAAA nobody remembered -- and report success. deSEC already had this
    guard; this is the same one.
    """

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def test_add_refuses_rather_than_replacing_a_set_it_could_not_read(self):
        fake = _wire(self.provider, _RefusesTheZoneBody({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10", "192.168.1.11"),
        ]}))
        self.assertFalse(self.provider.add_rewrite("app.home.lab", "192.168.1.12"))
        # Nothing was sent, so nothing was lost. The caller sees the failure and can retry.
        self.assertEqual(fake.patches, [])

    def test_a_network_error_on_the_read_is_refused_too(self):
        fake = _wire(self.provider, _RefusesTheZoneBody(
            {"home.lab.": [_rrset("app.home.lab.", "A", "192.168.1.10")]}, network_error=True,
        ))
        self.assertFalse(self.provider.add_rewrite("app.home.lab", "192.168.1.11"))
        self.assertEqual(fake.patches, [])

    def test_a_server_error_on_the_read_is_refused_too(self):
        fake = _wire(self.provider, _RefusesTheZoneBody(
            {"home.lab.": [_rrset("app.home.lab.", "A", "192.168.1.10")]}, status=500,
        ))
        self.assertFalse(self.provider.add_rewrite("app.home.lab", "192.168.1.11"))
        self.assertEqual(fake.patches, [])

    def test_delete_does_not_claim_success_on_a_set_it_could_not_read(self):
        """This one destroys nothing, but it lies, and the lie is what gets acted on.

        `delete_rewrite` returning True is how the caller learns the record is gone. On a
        refused read the record is very much still there.
        """
        fake = _wire(self.provider, _RefusesTheZoneBody({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertFalse(self.provider.delete_rewrite("app.home.lab", "192.168.1.10"))
        self.assertEqual(fake.patches, [])

    def test_listing_still_skips_a_zone_it_cannot_read(self):
        """Listing is read-only, so a zone it cannot open simply contributes nothing."""
        _wire(self.provider, _RefusesTheZoneBody({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertEqual(self.provider.list_rewrites(), [])

    def test_an_empty_zone_is_still_writable(self):
        """The guard must not turn a genuinely empty zone into a failure."""
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        self.assertTrue(self.provider.add_rewrite("app.home.lab", "192.168.1.10"))
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"), ["192.168.1.10"])


class TestPowerDNSUpdateRewrite(unittest.TestCase):
    """`update_rewrite` is the base class's add-then-delete, over a real zone."""

    def test_update_moves_the_value_and_leaves_no_orphan(self):
        provider = PowerDNSProvider("http://pdns:8081", "", "key")
        fake = _wire(provider, _FakePowerDNS({"home.lab.": [
            _rrset("app.home.lab.", "A", "192.168.1.10"),
        ]}))
        self.assertTrue(provider.update_rewrite(
            "app.home.lab", "192.168.1.10", "app.home.lab", "192.168.1.20"))
        self.assertEqual(fake.contents("home.lab.", "app.home.lab.", "A"), ["192.168.1.20"])


class TestPowerDNSDiagnostics(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = PowerDNSProvider("http://pdns:8081", "", "key")

    def _codes(self, result: dict) -> list[str]:
        return [c["detail_code"] for c in result["checks"]]

    def test_a_refused_key_stops_at_the_first_check(self):
        self.provider.session.get = MagicMock(return_value=_response(401, {}))
        result = self.provider.validate_permissions()
        self.assertFalse(result["ok"])
        self.assertEqual(self._codes(result), ["login_failed"])

    def test_a_working_key_with_zones_passes(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        result = self.provider.validate_permissions()
        self.assertTrue(result["ok"])
        self.assertEqual(self._codes(result), ["login_ok", "zones_found"])

    def test_no_zones_warns_without_blocking(self):
        _wire(self.provider, _FakePowerDNS({}))
        result = self.provider.validate_permissions()
        self.assertTrue(result["ok"])
        self.assertIn("zones_none", self._codes(result))
        self.assertTrue(result["warnings"])

    def test_a_hostname_hint_inside_a_zone_matches(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        result = self.provider.validate_permissions(hostname_hint="app.home.lab")
        self.assertIn("zone_match", self._codes(result))
        self.assertEqual(result["warnings"], [])

    def test_a_hostname_hint_outside_every_zone_warns_without_blocking(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        result = self.provider.validate_permissions(hostname_hint="app.example.com")
        self.assertIn("zone_missing", self._codes(result))
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])

    def test_the_write_probe_cleans_up_after_itself(self):
        fake = _wire(self.provider, _FakePowerDNS({"home.lab.": []}))
        result = self.provider.validate_permissions(write_probe=True)
        self.assertIn("write_ok", self._codes(result))
        self.assertEqual(fake.zones["home.lab."], [])

    def test_a_read_only_key_fails_the_write_probe(self):
        self.provider.session.get = MagicMock(
            side_effect=_FakePowerDNS({"home.lab.": []}).get)
        self.provider.session.patch = MagicMock(return_value=_response(403, {}))
        result = self.provider.validate_permissions(write_probe=True)
        self.assertFalse(result["ok"])
        self.assertIn("write_denied", self._codes(result))

    def test_health_reports_the_zone_count(self):
        _wire(self.provider, _FakePowerDNS({"home.lab.": [], "other.lab.": []}))
        self.assertEqual(self.provider.health_status(),
                         {"ok": True, "status": "healthy", "zones_visible": 2})

    def test_health_is_down_when_the_api_does_not_answer(self):
        self.provider.session.get = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertEqual(self.provider.health_status(),
                         {"ok": False, "status": "down", "zones_visible": 0})


class TestPowerDNSRegistration(unittest.TestCase):

    def test_the_type_is_declared_as_local_dns(self):
        meta = PROVIDER_TYPES["powerdns"]
        self.assertEqual(meta["category"], "dns")
        self.assertTrue(meta["available"])
        self.assertEqual(meta["capabilities"], {
            "proxy": False,
            "dns": True,
            # Publicness depends on a registrar delegation the API cannot show us.
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        })
        self.assertEqual(meta["pass_label"], "API Key")
        self.assertEqual(len(meta["guided_steps"]), 3)

    def test_it_sits_between_technitium_and_desec(self):
        """The wizard derives its i18n keys from step position, so order is contractual."""
        order = list(PROVIDER_TYPES)
        self.assertEqual(order.index("powerdns"), order.index("technitium") + 1)
        self.assertEqual(order.index("desec"), order.index("powerdns") + 1)

    def test_create_provider_builds_one_from_a_row(self):
        p = create_provider({
            "type": "powerdns", "url": "http://pdns:8081/", "username": "ns1",
            "password": encrypt_secret("key"), "extra": None,
        })
        self.assertIsInstance(p, PowerDNSProvider)
        self.assertEqual(p.url, "http://pdns:8081")
        self.assertEqual(p.server_id, "ns1")
        self.assertEqual(p.session.headers["X-API-Key"], "key")

    def test_every_call_carries_a_timeout(self):
        p = PowerDNSProvider("http://pdns:8081", "", "key")
        self.assertIsInstance(p.session, TimeoutSession)
        self.assertEqual(p.session.timeout, PROVIDER_TIMEOUT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
