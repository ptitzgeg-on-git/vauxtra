"""Unit tests for DesecProvider — all HTTP calls are mocked.

deSEC's `records` field is the whole record set, so a PATCH carrying one address deletes
every other address on that name. The fake below stores the sets and applies the writes,
which is what makes a lost sibling record visible to a test.

The second thing under test is the difference between "no records" and "we could not
ask": `_get_all` answers `[]` for the first and `None` for the second, and a rate-limited
account must never be read as an empty one.
"""

import unittest
from unittest.mock import MagicMock

import requests

from app.config import PROVIDER_TIMEOUT, encrypt_secret
from app.providers.base import TimeoutSession
from app.providers.desec import DEFAULT_API, DesecProvider
from app.providers.factory import PROVIDER_TYPES, create_provider


def _response(status_code: int = 200, json_data=None, links=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = {} if json_data is None else json_data
    # `links` is a real dict here because the provider must not walk a MagicMock: see
    # `test_a_mock_shaped_links_attribute_does_not_loop`.
    r.links = links if links is not None else {}
    return r


class _FakeDesec:
    """The deSEC endpoints the provider calls, over record sets it actually mutates."""

    def __init__(self, domains: dict):
        # {"example.dedyn.io": {"minimum_ttl": 3600, "rrsets": {("www", "A"): [...]}}}
        self.domains = domains
        self.writes: list[tuple] = []

    @staticmethod
    def _path(url: str) -> list[str]:
        return [p for p in url.split("/api/v1/", 1)[1].split("/") if p]

    def _domain(self, name: str) -> dict | None:
        return self.domains.get(name)

    def get(self, url: str, *_a, **_kw):
        parts = self._path(url)
        if parts == ["domains"]:
            return _response(200, [
                {"name": n, "minimum_ttl": d.get("minimum_ttl", 3600)}
                for n, d in self.domains.items()
            ])
        if len(parts) == 3 and parts[0] == "domains" and parts[2] == "rrsets":
            domain = self._domain(parts[1])
            if domain is None:
                return _response(404)
            return _response(200, [
                {"subname": sub, "type": rtype, "ttl": entry["ttl"], "records": entry["records"]}
                for (sub, rtype), entry in domain["rrsets"].items()
            ])
        if len(parts) == 5 and parts[2] == "rrsets":
            domain = self._domain(parts[1])
            if domain is None:
                return _response(404)
            sub = "" if parts[3] == "@" else parts[3]
            entry = domain["rrsets"].get((sub, parts[4]))
            if entry is None:
                return _response(404)
            return _response(200, {"subname": sub, "type": parts[4],
                                   "ttl": entry["ttl"], "records": entry["records"]})
        return _response(404)

    def post(self, url: str, json=None, *_a, **_kw):
        parts = self._path(url)
        domain = self._domain(parts[1])
        if domain is None:
            return _response(404)
        body = json or {}
        key = (body.get("subname", ""), body.get("type"))
        self.writes.append(("POST", parts[1], key, tuple(body.get("records") or ())))
        if key in domain["rrsets"]:
            # `subname` is write-once: deSEC answers 400 rather than replacing.
            return _response(400, {"detail": "already exists"})
        domain["rrsets"][key] = {"ttl": body.get("ttl"), "records": list(body.get("records") or [])}
        return _response(201, body)

    def patch(self, url: str, json=None, *_a, **_kw):
        parts = self._path(url)
        domain = self._domain(parts[1])
        sub = "" if parts[3] == "@" else parts[3]
        key = (sub, parts[4])
        body = json or {}
        self.writes.append(("PATCH", parts[1], key, tuple(body.get("records") or ())))
        if domain is None or key not in domain["rrsets"]:
            return _response(404)
        domain["rrsets"][key] = {"ttl": body.get("ttl"), "records": list(body.get("records") or [])}
        return _response(200, body)

    def delete(self, url: str, *_a, **_kw):
        parts = self._path(url)
        domain = self._domain(parts[1])
        sub = "" if parts[3] == "@" else parts[3]
        key = (sub, parts[4])
        self.writes.append(("DELETE", parts[1], key, ()))
        if domain is not None:
            domain["rrsets"].pop(key, None)
        # DELETE answers 204 whether or not the set was there.
        return _response(204)

    def records(self, domain: str, subname: str, rtype: str) -> list[str] | None:
        entry = self.domains.get(domain, {}).get("rrsets", {}).get((subname, rtype))
        return list(entry["records"]) if entry else None


def _domain(minimum_ttl: int = 3600, **rrsets) -> dict:
    """`www_A=["10.0.0.1"]` reads as the `(www, A)` record set."""
    parsed = {}
    for key, values in rrsets.items():
        sub, _, rtype = key.rpartition("_")
        parsed[("" if sub == "apex" else sub, rtype)] = {"ttl": minimum_ttl, "records": list(values)}
    return {"minimum_ttl": minimum_ttl, "rrsets": parsed}


def _wire(provider: DesecProvider, fake: _FakeDesec) -> _FakeDesec:
    provider.session.get = MagicMock(side_effect=fake.get)
    provider.session.post = MagicMock(side_effect=fake.post)
    provider.session.patch = MagicMock(side_effect=fake.patch)
    provider.session.delete = MagicMock(side_effect=fake.delete)
    return fake


def _provider(username: str = "") -> DesecProvider:
    return DesecProvider("", username, "token")


class TestDesecConfiguration(unittest.TestCase):

    def test_a_blank_url_falls_back_to_the_public_api(self):
        self.assertEqual(_provider().url, DEFAULT_API)

    def test_a_bare_host_gains_the_api_path(self):
        self.assertEqual(DesecProvider("https://desec.example", "", "t").url,
                         "https://desec.example/api/v1")

    def test_an_url_that_already_carries_the_api_path_is_left_alone(self):
        self.assertEqual(DesecProvider("https://desec.example/api/v1/", "", "t").url,
                         "https://desec.example/api/v1")

    def test_the_token_travels_as_an_authorization_header(self):
        self.assertEqual(_provider().session.headers["Authorization"], "Token token")

    def test_the_token_is_trimmed(self):
        self.assertEqual(DesecProvider("", "", "  tok  ").session.headers["Authorization"],
                         "Token tok")

    def test_every_call_carries_a_timeout(self):
        p = _provider()
        self.assertIsInstance(p.session, TimeoutSession)
        self.assertEqual(p.session.timeout, PROVIDER_TIMEOUT)


class TestDesecPagination(unittest.TestCase):
    """`_get_all` must distinguish an empty collection from a refused one."""

    def setUp(self) -> None:
        self.provider = _provider()

    def test_pages_are_followed_to_the_end(self):
        first = _response(200, [{"name": "a.dedyn.io"}],
                          links={"next": {"url": "https://desec.io/api/v1/domains/?cursor=2"}})
        second = _response(200, [{"name": "b.dedyn.io"}])
        self.provider.session.get = MagicMock(side_effect=[first, second])
        got = self.provider._get_all("https://desec.io/api/v1/domains/")
        self.assertEqual([d["name"] for d in got], ["a.dedyn.io", "b.dedyn.io"])

    def test_an_empty_collection_is_an_empty_list(self):
        self.provider.session.get = MagicMock(return_value=_response(200, []))
        self.assertEqual(self.provider._get_all("https://desec.io/api/v1/domains/"), [])

    def test_a_rate_limited_read_is_unknown_not_empty(self):
        """429 must not read as "this account has no records"."""
        self.provider.session.get = MagicMock(return_value=_response(429, {"detail": "throttled"}))
        self.assertIsNone(self.provider._get_all("https://desec.io/api/v1/domains/"))

    def test_a_network_error_is_unknown(self):
        self.provider.session.get = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertIsNone(self.provider._get_all("https://desec.io/api/v1/domains/"))

    def test_a_body_that_is_not_a_list_is_unknown(self):
        self.provider.session.get = MagicMock(return_value=_response(200, {"detail": "nope"}))
        self.assertIsNone(self.provider._get_all("https://desec.io/api/v1/domains/"))

    def test_a_body_that_is_not_json_is_unknown(self):
        r = _response(200)
        r.json.side_effect = ValueError("no JSON")
        self.provider.session.get = MagicMock(return_value=r)
        self.assertIsNone(self.provider._get_all("https://desec.io/api/v1/domains/"))

    def test_a_mock_shaped_links_attribute_does_not_loop(self):
        """A bare MagicMock answers `.links["next"]` forever; the guard stops that."""
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = [{"name": "a.dedyn.io"}]
        self.provider.session.get = MagicMock(return_value=r)
        self.assertEqual(self.provider._get_all("https://desec.io/api/v1/domains/"),
                         [{"name": "a.dedyn.io"}])
        self.assertEqual(self.provider.session.get.call_count, 1)

    def test_a_runaway_cursor_stops_at_the_page_cap(self):
        loop = _response(200, [{"name": "a.dedyn.io"}],
                         links={"next": {"url": "https://desec.io/api/v1/domains/?cursor=x"}})
        self.provider.session.get = MagicMock(return_value=loop)
        self.provider._get_all("https://desec.io/api/v1/domains/")
        self.assertEqual(self.provider.session.get.call_count, 20)


class TestDesecDomains(unittest.TestCase):

    def test_domains_are_cached_between_calls(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        p._list_domains()
        p._list_domains()
        self.assertEqual(p.session.get.call_count, 1)
        self.assertEqual(len(fake.domains), 1)

    def test_refresh_asks_again(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        p._list_domains()
        p._list_domains(refresh=True)
        self.assertEqual(p.session.get.call_count, 2)

    def test_a_refused_read_is_not_cached_as_empty(self):
        p = _provider()
        p.session.get = MagicMock(return_value=_response(429))
        self.assertIsNone(p._list_domains())
        self.assertIsNone(p._domains)

    def test_a_configured_domain_narrows_the_account(self):
        p = _provider("b.dedyn.io")
        _wire(p, _FakeDesec({"a.dedyn.io": _domain(), "b.dedyn.io": _domain()}))
        self.assertEqual([d["name"] for d in p._list_domains()], ["b.dedyn.io"])

    def test_a_configured_domain_the_listing_omits_is_still_used(self):
        """A token scoped to one domain may not be allowed to list it."""
        p = _provider("scoped.dedyn.io")
        _wire(p, _FakeDesec({}))
        self.assertEqual([d["name"] for d in p._list_domains()], ["scoped.dedyn.io"])

    def test_find_domain_picks_the_longest_match(self):
        p = _provider()
        _wire(p, _FakeDesec({"dedyn.io": _domain(), "a.dedyn.io": _domain()}))
        self.assertEqual(p._find_domain("www.a.dedyn.io")["name"], "a.dedyn.io")

    def test_find_domain_matches_the_apex(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertEqual(p._find_domain("a.dedyn.io")["name"], "a.dedyn.io")

    def test_find_domain_refuses_a_suffix_that_is_not_a_label_boundary(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertIsNone(p._find_domain("nota.dedyn.io"))

    def test_subname_is_empty_at_the_apex(self):
        self.assertEqual(DesecProvider._subname("a.dedyn.io", "a.dedyn.io"), "")
        self.assertEqual(DesecProvider._subname("www.a.dedyn.io", "a.dedyn.io"), "www")
        self.assertEqual(DesecProvider._subname("x.y.a.dedyn.io", "a.dedyn.io"), "x.y")

    def test_the_ttl_never_falls_below_the_domain_floor(self):
        self.assertEqual(DesecProvider._ttl({"minimum_ttl": 7200}), 7200)
        self.assertEqual(DesecProvider._ttl({"minimum_ttl": 60}), 3600)
        self.assertEqual(DesecProvider._ttl({}), 3600)
        self.assertEqual(DesecProvider._ttl({"minimum_ttl": "oops"}), 3600)

    def test_the_apex_is_addressed_as_an_at_sign(self):
        p = _provider()
        self.assertTrue(p._rrset_url("a.dedyn.io", "", "A").endswith("/rrsets/@/A/"))
        self.assertTrue(p._rrset_url("a.dedyn.io", "www", "A").endswith("/rrsets/www/A/"))


class TestDesecListRewrites(unittest.TestCase):

    def test_managed_types_are_reported_in_the_stored_shape(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain(
            www_A=["10.0.0.1"], v6_AAAA=["2001:db8::1"],
            alias_CNAME=["Origin.Example.Com."], apex_A=["10.0.0.9"],
        )}))
        got = {(r["domain"], r["answer"], r["type"]) for r in p.list_rewrites()}
        self.assertEqual(got, {
            ("www.a.dedyn.io", "10.0.0.1", "A"),
            ("v6.a.dedyn.io", "2001:db8::1", "AAAA"),
            ("alias.a.dedyn.io", "origin.example.com", "CNAME"),
            ("a.dedyn.io", "10.0.0.9", "A"),
        })

    def test_unmanaged_types_are_skipped(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain(apex_NS=["ns1.desec.io."], apex_TXT=["v=spf1 -all"])}))
        self.assertEqual(p.list_rewrites(), [])

    def test_every_value_of_a_multi_valued_set_is_listed(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1", "10.0.0.2"])}))
        self.assertEqual([r["answer"] for r in p.list_rewrites()], ["10.0.0.1", "10.0.0.2"])


class TestDesecAddRewrite(unittest.TestCase):

    def test_a_new_name_is_created_with_post(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertTrue(p.add_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertEqual(fake.records("a.dedyn.io", "www", "A"), ["10.0.0.1"])
        self.assertEqual(fake.writes[0][0], "POST")

    def test_adding_an_address_keeps_the_one_already_there(self):
        """A bare PATCH of `["10.0.0.2"]` would delete .1 — the merge is the point."""
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        self.assertTrue(p.add_rewrite("www.a.dedyn.io", "10.0.0.2"))
        self.assertEqual(fake.records("a.dedyn.io", "www", "A"), ["10.0.0.1", "10.0.0.2"])

    def test_a_value_already_present_writes_nothing(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        self.assertTrue(p.add_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertEqual(fake.writes, [])

    def test_the_apex_is_written_with_an_empty_subname(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertTrue(p.add_rewrite("a.dedyn.io", "10.0.0.1"))
        self.assertEqual(fake.records("a.dedyn.io", "", "A"), ["10.0.0.1"])

    def test_an_ipv6_value_lands_in_its_own_set(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        self.assertTrue(p.add_rewrite("www.a.dedyn.io", "2001:db8::1"))
        self.assertEqual(fake.records("a.dedyn.io", "www", "AAAA"), ["2001:db8::1"])
        self.assertEqual(fake.records("a.dedyn.io", "www", "A"), ["10.0.0.1"])

    def test_a_cname_replaces_rather_than_extends(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(alias_CNAME=["old.example.com."])}))
        self.assertTrue(p.add_rewrite("alias.a.dedyn.io", "new.example.com"))
        self.assertEqual(fake.records("a.dedyn.io", "alias", "CNAME"), ["new.example.com."])

    def test_the_domain_floor_is_respected_on_a_new_set(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(minimum_ttl=7200)}))
        p.add_rewrite("www.a.dedyn.io", "10.0.0.1")
        self.assertEqual(fake.domains["a.dedyn.io"]["rrsets"][("www", "A")]["ttl"], 7200)

    def test_a_name_outside_every_domain_is_refused(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertFalse(p.add_rewrite("www.example.com", "10.0.0.1"))
        self.assertEqual(fake.writes, [])

    def test_a_blank_value_is_refused(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertFalse(p.add_rewrite("www.a.dedyn.io", "   "))
        self.assertEqual(fake.writes, [])

    def test_an_unreadable_record_set_is_never_written_over(self):
        """A 500 on the read means we do not know the siblings; writing would drop them."""
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        p.session.get = MagicMock(side_effect=lambda url, *a, **kw:
                                  _response(500) if "/rrsets/www/" in url else fake.get(url))
        self.assertFalse(p.add_rewrite("www.a.dedyn.io", "10.0.0.2"))
        self.assertEqual(fake.writes, [])

    def test_a_rejected_write_is_reported_as_a_failure(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        p.session.post = MagicMock(return_value=_response(403, {"detail": "read-only token"}))
        self.assertFalse(p.add_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertIsNone(fake.records("a.dedyn.io", "www", "A"))

    def test_a_network_error_on_the_write_is_reported_as_a_failure(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        p.session.post = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertFalse(p.add_rewrite("www.a.dedyn.io", "10.0.0.1"))


class TestDesecDeleteRewrite(unittest.TestCase):

    def test_the_last_value_removes_the_record_set(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        self.assertTrue(p.delete_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertIsNone(fake.records("a.dedyn.io", "www", "A"))
        self.assertEqual(fake.writes[-1][0], "DELETE")

    def test_removing_one_value_keeps_the_others(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1", "10.0.0.2"])}))
        self.assertTrue(p.delete_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertEqual(fake.records("a.dedyn.io", "www", "A"), ["10.0.0.2"])

    def test_deleting_what_is_not_there_succeeds_without_writing(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertTrue(p.delete_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertEqual(fake.writes, [])

    def test_deleting_a_value_absent_from_an_existing_set_writes_nothing(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        self.assertTrue(p.delete_rewrite("www.a.dedyn.io", "10.0.0.9"))
        self.assertEqual(fake.writes, [])
        self.assertEqual(fake.records("a.dedyn.io", "www", "A"), ["10.0.0.1"])

    def test_a_cname_is_matched_in_its_absolute_form(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(alias_CNAME=["origin.example.com."])}))
        self.assertTrue(p.delete_rewrite("alias.a.dedyn.io", "origin.example.com"))
        self.assertIsNone(fake.records("a.dedyn.io", "alias", "CNAME"))

    def test_an_unreadable_record_set_is_never_deleted(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        p.session.get = MagicMock(side_effect=lambda url, *a, **kw:
                                  _response(500) if "/rrsets/www/" in url else fake.get(url))
        self.assertFalse(p.delete_rewrite("www.a.dedyn.io", "10.0.0.1"))
        self.assertEqual(fake.writes, [])

    def test_a_name_outside_every_domain_is_refused(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertFalse(p.delete_rewrite("www.example.com", "10.0.0.1"))


class TestDesecUpdateRewrite(unittest.TestCase):

    def test_update_moves_the_value_and_leaves_no_orphan(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain(www_A=["10.0.0.1"])}))
        self.assertTrue(p.update_rewrite("www.a.dedyn.io", "10.0.0.1", "www.a.dedyn.io", "10.0.0.2"))
        self.assertEqual(fake.records("a.dedyn.io", "www", "A"), ["10.0.0.2"])


class TestDesecDiagnostics(unittest.TestCase):

    def _codes(self, result: dict) -> list[str]:
        return [c["detail_code"] for c in result["checks"]]

    def test_a_refused_token_stops_at_the_first_check(self):
        p = _provider()
        p.session.get = MagicMock(return_value=_response(401, {"detail": "invalid token"}))
        result = p.validate_permissions()
        self.assertFalse(result["ok"])
        self.assertEqual(self._codes(result), ["login_failed"])

    def test_a_working_token_with_domains_passes(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        result = p.validate_permissions()
        self.assertTrue(result["ok"])
        self.assertEqual(self._codes(result), ["login_ok", "zones_found"])

    def test_an_account_without_domains_warns_without_blocking(self):
        p = _provider()
        _wire(p, _FakeDesec({}))
        result = p.validate_permissions()
        self.assertTrue(result["ok"])
        self.assertIn("zones_none", self._codes(result))
        self.assertTrue(result["warnings"])

    def test_a_hostname_hint_inside_a_domain_matches(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        result = p.validate_permissions(hostname_hint="www.a.dedyn.io")
        self.assertIn("zone_match", self._codes(result))
        self.assertEqual(result["warnings"], [])

    def test_a_hostname_hint_outside_every_domain_warns_without_blocking(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        result = p.validate_permissions(hostname_hint="www.example.com")
        self.assertIn("zone_missing", self._codes(result))
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])

    def test_the_write_probe_cleans_up_after_itself(self):
        p = _provider()
        fake = _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        result = p.validate_permissions(write_probe=True)
        self.assertIn("write_ok", self._codes(result))
        self.assertEqual(fake.domains["a.dedyn.io"]["rrsets"], {})

    def test_a_read_only_token_fails_the_write_probe(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        p.session.post = MagicMock(return_value=_response(403, {"detail": "read-only"}))
        result = p.validate_permissions(write_probe=True)
        self.assertFalse(result["ok"])
        self.assertIn("write_denied", self._codes(result))

    def test_health_reports_the_domain_count(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain(), "b.dedyn.io": _domain()}))
        self.assertEqual(p.health_status(), {"ok": True, "status": "healthy", "zones_visible": 2})

    def test_health_is_down_when_the_api_does_not_answer(self):
        p = _provider()
        p.session.get = MagicMock(side_effect=requests.RequestException("boom"))
        self.assertEqual(p.health_status(), {"ok": False, "status": "down", "zones_visible": 0})

    def test_connection_follows_the_domain_listing(self):
        p = _provider()
        _wire(p, _FakeDesec({"a.dedyn.io": _domain()}))
        self.assertTrue(p.test_connection())

    def test_connection_fails_on_a_refused_token(self):
        p = _provider()
        p.session.get = MagicMock(return_value=_response(401))
        self.assertFalse(p.test_connection())


class TestDesecRegistration(unittest.TestCase):

    def test_the_type_is_declared_as_public_dns(self):
        meta = PROVIDER_TYPES["desec"]
        self.assertEqual(meta["category"], "dns")
        self.assertTrue(meta["available"])
        self.assertEqual(meta["capabilities"], {
            "proxy": False,
            "dns": True,
            "public_dns": True,
            "supports_auto_public_target": True,
            "supports_tunnel": False,
        })
        self.assertEqual(meta["pass_label"], "API Token")
        self.assertEqual(len(meta["guided_steps"]), 3)

    def test_it_is_a_second_public_dns_provider_beside_cloudflare(self):
        """The point of the provider: a public-DNS setup that is not Cloudflare."""
        public = [t for t, m in PROVIDER_TYPES.items() if m["capabilities"]["public_dns"]]
        self.assertIn("cloudflare", public)
        self.assertIn("desec", public)

    def test_create_provider_builds_one_from_a_row(self):
        p = create_provider({
            "type": "desec", "url": "", "username": "a.dedyn.io",
            "password": encrypt_secret("tok"), "extra": None,
        })
        self.assertIsInstance(p, DesecProvider)
        self.assertEqual(p.url, DEFAULT_API)
        self.assertEqual(p.session.headers["Authorization"], "Token tok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
