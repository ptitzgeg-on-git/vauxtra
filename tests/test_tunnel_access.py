"""Which tunnel routes Cloudflare Access protects, and what the token may know about it.

Measured on 2026-11-14 with the tunnel token of a 1.6.1 instance, on an account holding
eighteen Access applications: `GET /accounts/{id}/access/apps` answered 200, success, an
empty list. These tests pin the consequence: an empty listing is `unverified`, never "every
route is open", and an Access redirect seen by the probe turns it into `missing`.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import app.db as db
from app import models
from app.api import providers as providers_api
from app.api import services as services_api
from app.api import sync as sync_api
from app.providers import cloudflare_access as access
from app.providers.base import ProviderListingRefused
from app.providers.cloudflare_tunnel import CloudflareTunnelProvider

ALLOW_ME = {"id": "p-me", "name": "Moi", "decision": "allow", "include": [{"email": {"email": "a@b.c"}}]}
BYPASS_IPS = {"id": "p-ip", "name": "IP OK", "decision": "bypass", "include": [{"ip": {"ip": "203.0.113.7/32"}}]}
BYPASS_ALL = {"id": "p-all", "name": "Public", "decision": "bypass", "include": [{"everyone": {}}]}
ALLOW_ALL = {"id": "p-pin", "name": "Any mail", "decision": "allow", "include": [{"everyone": {}}]}


def _app(domain, *policies, app_id="a1", destinations=None):
    body = {"id": app_id, "name": domain, "domain": domain, "policies": list(policies)}
    if destinations is not None:
        body["destinations"] = destinations
    return body


class ClassifyHostTests(unittest.TestCase):
    def test_an_application_on_the_name_protects_it(self):
        info = access.classify_host("board.example.com", [_app("board.example.com", BYPASS_IPS, ALLOW_ME)])
        self.assertEqual((info["state"], info["policies"]), ("protected", ["IP OK", "Moi"]))

    def test_a_route_no_application_covers_is_open(self):
        info = access.classify_host("listo.example.com", [_app("board.example.com", ALLOW_ME)])
        self.assertEqual((info["state"], info["reason"]), ("open", "no_application"))

    def test_a_wildcard_covers_every_subdomain_but_not_the_apex(self):
        apps = [_app("*.example.com", ALLOW_ME)]
        self.assertEqual(access.classify_host("deep.app.example.com", apps)["state"], "protected")
        self.assertEqual(access.classify_host("example.com", apps)["state"], "open")

    def test_the_exact_application_wins_over_the_wildcard(self):
        """Access resolves the overlap by specificity, and so must the panel."""
        apps = [_app("*.example.com", ALLOW_ME, app_id="w"), _app("pub.example.com", BYPASS_ALL, app_id="x")]
        info = access.classify_host("pub.example.com", apps)
        self.assertEqual((info["state"], info["reason"]), ("open", "everyone_bypass"))

    def test_a_bypass_for_a_list_of_addresses_is_restrictive(self):
        """Access denies what no policy matches: an IP bypass alone keeps strangers out."""
        info = access.classify_host("nas.example.com", [_app("nas.example.com", BYPASS_IPS)])
        self.assertEqual(info["state"], "protected")

    def test_an_allow_for_everyone_lets_anyone_with_a_mailbox_in(self):
        info = access.classify_host("app.example.com", [_app("app.example.com", ALLOW_ALL)])
        self.assertEqual((info["state"], info["reason"]), ("partial", "everyone_allow"))

    def test_an_application_on_a_path_leaves_the_rest_of_the_host(self):
        info = access.classify_host("app.example.com", [_app("app.example.com/admin", ALLOW_ME)])
        self.assertEqual((info["state"], info["reason"]), ("partial", "path_only"))

    def test_destinations_are_read_as_well_as_the_domain(self):
        app = _app("first.example.com", ALLOW_ME,
                   destinations=[{"type": "public", "uri": "first.example.com"},
                                 {"type": "public", "uri": "second.example.com"}])
        self.assertEqual(access.classify_host("second.example.com", [app])["state"], "protected")


class CoversOnlyTests(unittest.TestCase):
    """The one shape Vauxtra may delete: the application it wrote, unchanged."""

    def test_the_application_vauxtra_creates_covers_its_host_alone(self):
        app = access.new_application_body("listo.example.com", ["p-ip"])
        self.assertTrue(access.covers_only(app, "Listo.Example.com."))
        self.assertFalse(access.covers_only(app, "board.example.com"))

    def test_a_second_destination_added_in_the_dashboard_is_not_alone(self):
        app = access.new_application_body("listo.example.com", ["p-ip"])
        app["destinations"].append({"type": "public", "uri": "board.example.com"})
        self.assertFalse(access.covers_only(app, "listo.example.com"))

    def test_a_wildcard_or_a_path_is_not_the_host(self):
        self.assertFalse(access.covers_only(_app("*.example.com"), "listo.example.com"))
        self.assertFalse(access.covers_only(_app("listo.example.com/admin"), "listo.example.com"))


class PolicySummaryTests(unittest.TestCase):
    def test_a_policy_is_shown_by_name_and_decision_never_by_its_rules(self):
        summary = access.summarize_policy({**BYPASS_IPS, "app_count": 18})
        self.assertEqual(summary, {"id": "p-ip", "name": "IP OK", "decision": "bypass",
                                   "everyone": False, "app_count": 18})
        self.assertNotIn("203.0.113.7", json.dumps(summary))


class _Responses:
    """A stand-in for `_request_detailed`, answering by path."""

    def __init__(self, by_path):
        self.by_path = by_path
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        answer = self.by_path[path]
        return answer(kwargs) if callable(answer) else answer


def _ok(result):
    return {"ok": True, "status": 200, "result": result, "errors": []}


REFUSED = {"ok": False, "status": 403, "result": None, "errors": [{"code": 10000, "message": "Authentication error"}]}


class AccessOverviewTests(unittest.TestCase):
    def _provider(self, by_path):
        provider = CloudflareTunnelProvider("", "acc", "token", {"tunnel_id": "t1"})
        provider._request_detailed = _Responses(by_path)
        return provider

    def test_an_empty_listing_is_unverified_not_open(self):
        provider = self._provider({"/accounts/acc/access/apps": _ok([]),
                                   "/accounts/acc/access/policies": _ok([])})
        overview = provider.access_overview()
        self.assertEqual((overview["permission"], overview["reason"]), ("unverified", "empty_listing"))

    def test_a_refusal_is_a_missing_permission(self):
        provider = self._provider({"/accounts/acc/access/apps": REFUSED})
        self.assertEqual(provider.access_overview()["permission"], "missing")

    def test_a_listing_is_read_to_the_last_page(self):
        pages = {1: [_app(f"h{n}.example.com", ALLOW_ME, app_id=str(n)) for n in range(50)],
                 2: [_app("last.example.com", ALLOW_ME, app_id="last")]}
        provider = self._provider({
            "/accounts/acc/access/apps": lambda kw: _ok(pages[kw["params"]["page"]]),
            "/accounts/acc/access/policies": _ok([BYPASS_IPS]),
        })
        overview = provider.access_overview()
        self.assertEqual((overview["permission"], len(overview["apps"])), ("ok", 51))
        self.assertEqual([p["name"] for p in overview["policies"]], ["IP OK"])

    def test_reading_an_application_that_was_deleted_says_it_is_gone(self):
        """Measured on 2026-11-15: 404, code 11021, on an id deleted the minute before."""
        gone = {"ok": False, "status": 404, "result": None,
                "errors": [{"code": 11021, "message": "access.api.error.unknown_application"}]}
        provider = self._provider({"/accounts/acc/access/apps/app-1": gone})
        found = provider.get_access_app("app-1")
        self.assertEqual((found["ok"], found["gone"], found["refused"]), (False, True, False))

    def test_deleting_an_application_sends_one_delete_on_its_id(self):
        provider = self._provider({"/accounts/acc/access/apps/app-1": _ok({"id": "app-1"})})
        deleted = provider.delete_access_app("app-1")
        self.assertEqual([c[:2] for c in provider._request_detailed.calls],
                         [("DELETE", "/accounts/acc/access/apps/app-1")])
        self.assertTrue(deleted["ok"])

    def test_a_refusal_to_delete_is_not_read_as_gone(self):
        provider = self._provider({"/accounts/acc/access/apps/app-1": REFUSED})
        deleted = provider.delete_access_app("app-1")
        self.assertEqual((deleted["ok"], deleted["gone"], deleted["refused"]), (False, False, True))

    def test_a_stored_id_that_is_not_an_id_never_reaches_the_api(self):
        """The column comes back from a backup: a path there would aim the token elsewhere."""
        provider = self._provider({})
        self.assertFalse(provider.get_access_app("../../zones/z1/dns_records")["ok"])
        self.assertFalse(provider.delete_access_app("x/y")["ok"])
        self.assertEqual(provider._request_detailed.calls, [])

    def test_creating_an_application_sends_one_host_and_the_chosen_policies(self):
        provider = self._provider({"/accounts/acc/access/apps": _ok({"id": "new-app"})})
        created = provider.create_access_app("Listo.Example.com", ["p-ip", "p-me"])
        method, _path, kwargs = provider._request_detailed.calls[0]
        self.assertEqual((method, created["ok"], created["id"]), ("POST", True, "new-app"))
        self.assertEqual(kwargs["json"]["domain"], "listo.example.com")
        self.assertEqual(kwargs["json"]["policies"], [{"id": "p-ip", "precedence": 1},
                                                      {"id": "p-me", "precedence": 2}])


class _FakeTunnel:
    HOST_ID_IS_HOSTNAME = True

    def __init__(self, overview, hosts=("board.example.com", "listo.example.com"), create=None,
                 apps=None, delete=None, withdraw=True):
        self.overview = overview
        self.hosts = hosts
        self.create = create or {"ok": True, "refused": False, "status": 200, "id": "app-9", "errors": []}
        self.created = []
        # Access applications by id: an app body, or a whole answer of `get_access_app`.
        self.apps = apps or {}
        self.delete = delete or {"ok": True, "gone": False, "refused": False, "status": 200, "errors": []}
        self.withdraw = withdraw
        self.read = []
        self.deleted = []

    def access_overview(self):
        return dict(self.overview)

    def list_hosts(self):
        if self.hosts is None:
            raise ProviderListingRefused("no")
        return [{"id": h} for h in self.hosts]

    def create_access_app(self, hostname, policy_ids):
        self.created.append((hostname, policy_ids))
        return self.create

    def delete_host(self, host_id):
        if not self.withdraw:
            return False
        self.hosts = tuple(h for h in self.hosts if h != host_id)
        return True

    def get_access_app(self, app_id):
        self.read.append(app_id)
        answer = self.apps.get(app_id)
        if answer is None:
            return {"ok": False, "gone": True, "refused": False, "status": 404, "app": {}}
        if "gone" in answer:
            return answer
        return {"ok": True, "gone": False, "refused": False, "status": 200, "app": answer}

    def delete_access_app(self, app_id):
        self.deleted.append(app_id)
        return self.delete


def _request(method="GET", path="/"):
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class AccessRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)
        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "access.test.db")
        models.init_db()
        conn = models.get_db()
        conn.execute("INSERT INTO providers (id, name, type, url, username, password, extra, enabled) "
                     "VALUES (1, 'Tunnel', 'cloudflare_tunnel', '', 'acc', 'x', '{}', 1)")
        conn.executemany(
            "INSERT INTO services (subdomain, domain, target_ip, target_port, expose_mode, "
            "tunnel_hostname, tunnel_provider_id, status_detail, enabled) VALUES (?,?,?,?,?,?,?,?,1)",
            [("board", "example.com", "localhost", 8080, "tunnel", "", 1, "edge_access:302"),
             ("listo", "example.com", "localhost", 8081, "tunnel", "", 1, "edge_ok:200")],
        )
        conn.commit()
        conn.close()
        self._auth = patch.object(providers_api, "require_auth", lambda _req, scope=None: None)
        self._auth.start()

    def tearDown(self):
        self._auth.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _list(self, fake):
        with patch.object(providers_api, "create_provider", lambda _row: fake):
            return providers_api.list_tunnel_access(_request())["items"][0]

    def test_an_empty_listing_contradicted_by_the_edge_is_a_missing_permission(self):
        """Exactly the prod reading: no application listed, board redirected to Access."""
        item = self._list(_FakeTunnel({"permission": "unverified", "reason": "empty_listing",
                                       "apps": [], "policies": []}))
        self.assertEqual((item["permission"], item["reason"]), ("missing", "access_observed"))
        self.assertEqual({h["state"] for h in item["hosts"]}, {"unknown"})
        self.assertEqual(item["read_permission"], access.READ_PERMISSION)

    def test_a_readable_account_classifies_every_route(self):
        item = self._list(_FakeTunnel({
            "permission": "ok", "reason": "",
            "apps": [_app("board.example.com", BYPASS_IPS, ALLOW_ME)],
            "policies": [access.summarize_policy(BYPASS_IPS)],
        }))
        states = {h["hostname"]: h["state"] for h in item["hosts"]}
        self.assertEqual(states, {"board.example.com": "protected", "listo.example.com": "open"})
        self.assertEqual(item["counts"]["open"], 1)
        # The rules of the policies never leave: no address, no e-mail.
        body = json.dumps(item)
        self.assertNotIn("203.0.113.7", body)
        self.assertNotIn("a@b.c", body)

    def test_a_refused_ingress_listing_falls_back_on_the_routes_vauxtra_knows(self):
        item = self._list(_FakeTunnel({"permission": "ok", "reason": "", "apps": [], "policies": []},
                                      hosts=None))
        self.assertEqual([h["hostname"] for h in item["hosts"]], ["board.example.com", "listo.example.com"])

    def _protect(self, fake, hostname="listo.example.com", policy_ids=("p-ip",)):
        with patch.object(providers_api, "create_provider", lambda _row: fake):
            return providers_api.protect_tunnel_route(
                1, _request("POST"), providers_api.AccessProtectIn(hostname=hostname, policy_ids=list(policy_ids)),
            )

    def test_protecting_a_route_creates_its_application(self):
        fake = _FakeTunnel({"permission": "ok", "reason": "", "apps": [_app("board.example.com", ALLOW_ME)],
                            "policies": [access.summarize_policy(BYPASS_IPS)]})
        out = self._protect(fake, hostname="Listo.Example.com.")
        self.assertEqual(fake.created, [("listo.example.com", ["p-ip"])])
        self.assertEqual(out["application_id"], "app-9")

    def test_a_name_outside_the_tunnel_is_refused(self):
        fake = _FakeTunnel({"permission": "ok", "reason": "", "apps": [], "policies": []})
        with self.assertRaises(HTTPException) as ctx:
            self._protect(fake, hostname="bank.example.org")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(fake.created, [])

    def test_a_route_already_protected_is_not_duplicated(self):
        fake = _FakeTunnel({"permission": "ok", "reason": "", "apps": [_app("listo.example.com", ALLOW_ME)],
                            "policies": []})
        with self.assertRaises(HTTPException) as ctx:
            self._protect(fake)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_a_token_without_the_edit_permission_says_which_one_to_add(self):
        fake = _FakeTunnel({"permission": "missing", "reason": "refused", "apps": [], "policies": []},
                           create={"ok": False, "refused": True, "status": 403, "id": "", "errors": []})
        with self.assertRaises(HTTPException) as ctx:
            self._protect(fake)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn(access.EDIT_PERMISSION, ctx.exception.detail)


def _created_app(hostname, app_id="app-9"):
    return {**access.new_application_body(hostname, ["p-ip"]), "id": app_id}


class AccessFollowsTheRouteTests(AccessRouteTests):
    """Deleting a service deletes the Access application Vauxtra created for it, and only that.

    Measured on 2026-11-15 in prod: a route created with a policy and then deleted left its
    application in the account, listed nowhere in the panel.
    """

    OVERVIEW = {"permission": "ok", "reason": "", "apps": [], "policies": []}

    def setUp(self):
        super().setUp()
        self._svc_auth = patch.object(services_api, "require_auth", lambda _req, scope=None: None)
        self._svc_auth.start()

    def tearDown(self):
        self._svc_auth.stop()
        super().tearDown()

    def _column(self, subdomain, column):
        conn = models.get_db()
        try:
            return conn.execute(f"SELECT {column} FROM services WHERE subdomain=?",  # noqa: S608
                                (subdomain,)).fetchone()[column]
        finally:
            conn.close()

    def _with(self, fake):
        return (patch.object(services_api, "create_provider", lambda _row: fake),
                patch.object(sync_api, "create_provider", lambda _row: fake))

    def _delete(self, fake, subdomain="listo"):
        a, b = self._with(fake)
        with a, b:
            return services_api.delete_service(self._column(subdomain, "id"), _request("DELETE"))

    def _bulk_delete(self, fake, *subdomains):
        ids = [self._column(s, "id") for s in subdomains]
        a, b = self._with(fake)
        with a, b:
            return services_api.bulk_action(
                services_api._BulkActionBody(ids=ids, action="delete"), _request("POST"),
            )

    def _protected(self, **kwargs):
        fake = _FakeTunnel(dict(self.OVERVIEW), **kwargs)
        self._protect(fake)
        return fake

    def test_protecting_a_route_records_the_application_on_its_service(self):
        out = self._protect(_FakeTunnel(dict(self.OVERVIEW)))
        self.assertEqual(out["recorded_on"], 1)
        self.assertEqual((self._column("listo", "access_app_id"), self._column("board", "access_app_id")),
                         ("app-9", ""))

    def test_deleting_the_service_deletes_its_application(self):
        fake = self._protected(apps={"app-9": _created_app("listo.example.com")})
        self.assertEqual(self._delete(fake), {"ok": True, "errors": []})
        self.assertEqual(fake.deleted, ["app-9"])
        self.assertNotIn("listo.example.com", fake.hosts)

    def test_a_route_still_served_keeps_its_application(self):
        """The login goes last: never off a route that still answers."""
        fake = self._protected(apps={"app-9": _created_app("listo.example.com")}, withdraw=False)
        errors = self._delete(fake)["errors"]
        self.assertEqual(fake.deleted, [])
        self.assertIn("Access application left in place: the route is still served", errors)

    def test_an_application_edited_to_cover_more_is_left_in_place(self):
        app = _created_app("listo.example.com")
        app["destinations"].append({"type": "public", "uri": "board.example.com"})
        fake = self._protected(apps={"app-9": app})
        errors = self._delete(fake)["errors"]
        self.assertEqual(fake.deleted, [])
        self.assertEqual(errors, ["Access application left in place: "
                                  "listo.example.com no longer covers this route alone"])

    def test_an_application_already_gone_is_not_a_failure(self):
        fake = self._protected(apps={})
        self.assertEqual(self._delete(fake), {"ok": True, "errors": []})
        self.assertEqual((fake.read, fake.deleted), (["app-9"], []))

    def test_a_token_that_may_not_delete_says_which_permission_to_add(self):
        fake = self._protected(apps={"app-9": _created_app("listo.example.com")},
                               delete={"ok": False, "gone": False, "refused": True, "status": 403, "errors": []})
        errors = self._delete(fake)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn(access.EDIT_PERMISSION, errors[0])

    def test_a_route_protected_by_hand_is_never_touched(self):
        """No id recorded, no call: what the dashboard holds is the operator's."""
        fake = _FakeTunnel(dict(self.OVERVIEW), apps={"hand": _created_app("board.example.com", "hand")})
        self.assertEqual(self._delete(fake, "board"), {"ok": True, "errors": []})
        self.assertEqual((fake.read, fake.deleted), ([], []))

    def test_an_id_another_service_still_carries_stays(self):
        fake = self._protected(apps={"app-9": _created_app("listo.example.com")})
        conn = models.get_db()
        conn.execute("UPDATE services SET access_app_id='app-9' WHERE subdomain='board'")
        conn.commit()
        conn.close()
        self.assertEqual(self._delete(fake), {"ok": True, "errors": []})
        self.assertEqual(fake.deleted, [])

    def test_a_bulk_delete_removes_it_as_one_delete_would(self):
        fake = self._protected(apps={"app-9": _created_app("listo.example.com")})
        result = self._bulk_delete(fake, "board", "listo")
        self.assertEqual((result["affected"], result["errors"]), (2, []))
        self.assertEqual(fake.deleted, ["app-9"])

    def test_a_bulk_delete_names_the_route_it_left_protected(self):
        fake = self._protected(apps={"app-9": _created_app("listo.example.com")}, withdraw=False)
        errors = self._bulk_delete(fake, "listo")["errors"]
        self.assertIn("listo.example.com: Access application left in place: the route is still served", errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
