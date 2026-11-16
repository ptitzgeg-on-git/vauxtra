"""A tunnel route is never published, nor renamed, ahead of the login that protects it.

Two failures this guards against. The panel published a route and protected it in a second
request, so the name answered without a login in between, and for good when that request
failed. And a rename moved the route while its Access application stayed on the name the
service had left: the new name answered without a login, and nothing said so.

The fake account below checks, after every write, that each guarded name a tunnel serves
is behind an application. The tests make each step fail in turn and hold that invariant,
and they check that a refused save writes nothing at any provider.
"""

import copy
import json
import os
import tempfile
import unittest
from fnmatch import fnmatchcase
from unittest.mock import patch

import pydantic
from fastapi import HTTPException
from starlette.requests import Request

import app.db as db
from app import models
from app.api import providers as providers_api
from app.api import services as services_api
from app.api import sync as sync_api
from app.providers import cloudflare_access as access
from app.providers.base import ProviderListingRefused

ACCOUNT = "0123456789abcdef0123456789abcdef"
TUNNEL_A = "12345678-1234-1234-1234-123456789abc"
TUNNEL_B = "87654321-4321-4321-4321-cba987654321"
TEAM = {"id": "p-team", "name": "Team", "decision": "allow",
        "include": [{"email_domain": {"domain": "example.com"}}]}
OPS = {"id": "p-ops", "name": "Ops", "decision": "allow",
       "include": [{"email_domain": {"domain": "example.com"}}]}
# Weaker than the two above: anyone without a login, and anyone who logs in.
OPEN = {"id": "p-open", "name": "Open", "decision": "bypass", "include": [{"everyone": {}}]}
ANYONE = {"id": "p-all", "name": "All", "decision": "allow", "include": [{"everyone": {}}]}
EDIT = access.EDIT_PERMISSION
READ = access.READ_PERMISSION

# What changes what a provider holds. `create_access_app` is not here: a refused save may
# ask for an application and be refused it, and the snapshot shows that nothing was made.
WRITE_CALLS = {"create_host", "update_host", "delete_host", "delete_access_app"}
ACCESS_CALLS = {"access_overview", "create_access_app", "get_access_app", "delete_access_app"}


def _request(method, path):
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _body(subdomain, tunnel=1, **fields):
    values = {"subdomain": subdomain, "domain": "example.com", "target_ip": "192.168.1.10",
              "target_port": 8080, "expose_mode": "tunnel", "tunnel_provider_id": tunnel}
    return services_api.ServiceIn(**{**values, **fields})


class _Account:
    """One Cloudflare account, which both tunnels of the tests belong to.

    `change` runs after every write that went through, and records each guarded name a
    tunnel serves with no application in front of it. That list is the invariant: it stays
    empty whatever step fails.
    """

    def __init__(self):
        self.apps: dict[str, dict] = {}
        self.policies = [TEAM, OPS]
        self.served: dict[int, set[str]] = {1: set(), 2: set()}
        # Tunnels whose last rule sends every other name to a service, as a catch-all does.
        self.catch_all: set[int] = set()
        self.calls: list[tuple] = []
        self.writes: list[tuple] = []
        self.fail: dict = {}
        self.guarded: set[str] = set()
        self.violations: list[tuple] = []
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"app-{self._next}"

    def application(self, hostname, *policies, app_id, session="24h", **fields):
        """An application already in the account, in the shape Vauxtra creates."""
        body = access.new_application_body(hostname, [], session)
        body["policies"] = [
            {"precedence": i + 1, "reusable": True, **p} for i, p in enumerate(policies)
        ]
        body.update(fields)
        body["id"] = app_id
        self.apps[app_id] = body
        return body

    def change(self, *what):
        self.writes.append(what)
        apps = list(self.apps.values())
        for key in sorted(self.served):
            for name in sorted(self.served[key] & self.guarded):
                if access.classify_host(name, apps)["state"] != "protected":
                    self.violations.append((what, name))

    def snapshot(self):
        return copy.deepcopy((self.apps, self.served))


class _Tunnel:
    """A Cloudflare Tunnel provider over `_Account`, failing where `account.fail` says.

    `fail` maps a method, or `(tunnel key, method)`, to a way of failing: `refused` and
    `error` answer as Cloudflare does, `raise` raises, `partial` writes the route and then
    reports a failure (the ingress went in, the DNS record did not), and `sticky` is an
    `update_host` whose removal of the old rule fails without a word, as the real
    `_delete_ingress_rule` can. A listing of the account can also be `missing`,
    `unverified` or `truncated`, and a creation `malformed`: an answer that says ok and
    hands back an id without the shape of one.

    A name in `served` may be a wildcard, written as an ingress rule writes it.
    """

    HOST_ID_IS_HOSTNAME = True

    def __init__(self, account: _Account, key: int):
        self.account, self.key = account, key

    def _mode(self, method, *args):
        self.account.calls.append((self.key, method, *args))
        fail = self.account.fail
        return fail.get((self.key, method), fail.get(method))

    def _serve(self, hostname, on):
        served = self.account.served[self.key]
        if (hostname in served) == on:
            return
        if on:
            served.add(hostname)
        else:
            served.discard(hostname)
        self.account.change(self.key, "serve" if on else "unserve", hostname)

    def health_status(self):
        self._mode("health_status")
        return {"ok": True, "status": "healthy"}

    def list_hosts(self):
        if self._mode("list_hosts") == "refused":
            raise ProviderListingRefused("Cloudflare would not return the tunnel configuration")
        return [{"id": h, "domains": [h]} for h in sorted(self.account.served[self.key])]

    def serving_rule(self, hostname):
        if self._mode("serving_rule") == "refused":
            raise ProviderListingRefused("Cloudflare would not return the tunnel configuration")
        served = self.account.served[self.key]
        if hostname in served:
            return {"hostname": hostname}
        for pattern in sorted(served):
            if fnmatchcase(hostname, pattern):
                return {"hostname": pattern}
        return {"hostname": ""} if self.key in self.account.catch_all else None

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        mode = self._mode("create_host", domain)
        if mode == "refused":
            return None
        self._serve(domain, True)
        if mode == "partial":
            return None
        if mode == "raise":
            raise RuntimeError("the DNS record timed out")
        return {"id": domain, "domain": domain}

    def update_host(self, host_id, domain, ip, port, scheme="http", websocket=False,
                    cert_id=None):
        mode = self._mode("update_host", host_id, domain)
        # As the real provider does it: the old rule goes first, then the new one is written.
        if host_id != domain and mode != "sticky":
            self._serve(host_id, False)
        if mode == "refused":
            return False
        self._serve(domain, True)
        if mode == "partial":
            return False
        if mode == "raise":
            raise RuntimeError("the DNS record timed out")
        return True

    def delete_host(self, host_id):
        mode = self._mode("delete_host", host_id)
        if mode == "refused":
            return False
        if mode == "raise":
            raise RuntimeError("Cloudflare timed out")
        self._serve(host_id, False)
        return True

    def access_overview(self):
        mode = self._mode("access_overview")
        if mode == "raise":
            raise RuntimeError("Cloudflare timed out")
        reasons = {"missing": ("missing", "refused"), "error": ("error", "unreachable"),
                   "unverified": ("unverified", "empty_listing"),
                   "truncated": ("error", "truncated")}
        if mode in reasons:
            permission, reason = reasons[mode]
            return {"permission": permission, "reason": reason, "apps": [], "policies": []}
        apps = copy.deepcopy(list(self.account.apps.values()))
        return {
            "permission": "ok" if apps else "unverified",
            "reason": "" if apps else "empty_listing",
            "apps": apps,
            "policies": [access.summarize_policy(p) for p in self.account.policies],
        }

    def create_access_app(self, hostname, policy_ids, session_duration="24h"):
        mode = self._mode("create_access_app", hostname, tuple(policy_ids), session_duration)
        if mode == "raise":
            raise RuntimeError("Cloudflare timed out")
        if mode in ("refused", "error"):
            return {"ok": False, "refused": mode == "refused",
                    "status": 403 if mode == "refused" else 500, "id": "", "errors": []}
        if mode == "malformed":
            return {"ok": True, "refused": False, "status": 200, "id": "../apps", "errors": []}
        known = {p["id"]: p for p in self.account.policies}
        if any(p not in known for p in policy_ids):
            return {"ok": False, "refused": False, "status": 400, "id": "", "errors": []}
        body = access.new_application_body(hostname, list(policy_ids), session_duration)
        body["policies"] = [{**known[p["id"]], **p, "reusable": True} for p in body["policies"]]
        body["id"] = self.account.new_id()
        self.account.apps[body["id"]] = body
        self.account.change(self.key, "create_access_app", hostname)
        return {"ok": True, "refused": False, "status": 200, "id": body["id"], "errors": []}

    def get_access_app(self, app_id):
        mode = self._mode("get_access_app", app_id)
        if mode == "raise":
            raise RuntimeError("Cloudflare timed out")
        if mode in ("refused", "error"):
            return {"ok": False, "gone": False, "refused": mode == "refused",
                    "status": 403 if mode == "refused" else 500, "app": {}}
        app = self.account.apps.get(app_id)
        if app is None:
            return {"ok": False, "gone": True, "refused": False, "status": 404, "app": {}}
        return {"ok": True, "gone": False, "refused": False, "status": 200,
                "app": copy.deepcopy(app)}

    def delete_access_app(self, app_id):
        mode = self._mode("delete_access_app", app_id)
        if mode == "raise":
            raise RuntimeError("Cloudflare timed out")
        if mode in ("refused", "error"):
            return {"ok": False, "gone": False, "refused": mode == "refused",
                    "status": 403 if mode == "refused" else 500, "errors": []}
        if self.account.apps.pop(app_id, None) is None:
            return {"ok": False, "gone": True, "refused": False, "status": 404, "errors": []}
        self.account.change(self.key, "delete_access_app", app_id)
        return {"ok": True, "gone": False, "refused": False, "status": 200, "errors": []}


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)
        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "follows.test.db")
        models.init_db()
        conn = models.get_db()
        conn.executemany(
            "INSERT INTO providers (id, name, type, url, username, password, extra, enabled) "
            "VALUES (?, ?, ?, ?, ?, 'x', ?, 1)",
            [(1, "Home", "cloudflare_tunnel", "", ACCOUNT, json.dumps({"tunnel_id": TUNNEL_A})),
             (2, "Spare", "cloudflare_tunnel", "", ACCOUNT, json.dumps({"tunnel_id": TUNNEL_B})),
             (3, "NPM", "npm", "http://192.168.1.20:81", "admin@example.com", "{}")],
        )
        conn.commit()
        conn.close()
        self._new_account()

        def _provider(row):
            # Provider 3 has no fake: a call made through it raises, and no test expects one.
            return self.tunnels[row["id"]]

        for target in (services_api, sync_api):
            patcher = patch.object(target, "create_provider", _provider)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(services_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _new_account(self):
        self.account = _Account()
        self.tunnels = {key: _Tunnel(self.account, key) for key in (1, 2)}

    def _seed(self, subdomain, tunnel=1, app_id="", enabled=True, tunnel_hostname=""):
        conn = models.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO services (subdomain, domain, target_ip, target_port, expose_mode, "
                "tunnel_hostname, tunnel_provider_id, access_app_id, enabled) "
                "VALUES (?, 'example.com', '192.168.1.10', 8080, 'tunnel', ?, ?, ?, ?)",
                (subdomain, tunnel_hostname, tunnel, app_id, int(enabled)),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _sql(self, statement, *params):
        conn = models.get_db()
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def _post(self, body):
        resp = services_api.add_service(_request("POST", "/api/services"), body)
        return resp.status_code, json.loads(resp.body)

    def _put(self, sid, body):
        return services_api.update_service(sid, _request("PUT", f"/api/services/{sid}"), body)

    def _rows(self):
        conn = models.get_db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM services ORDER BY id")]
        finally:
            conn.close()

    def _logs(self):
        conn = models.get_db()
        try:
            return [(r["level"], r["message"])
                    for r in conn.execute("SELECT level, message FROM logs ORDER BY id")]
        finally:
            conn.close()

    def _access_calls(self):
        return [c for c in self.account.calls if c[1] in ACCESS_CALLS]

    def _state(self, host):
        return access.classify_host(host, list(self.account.apps.values()))["state"]

    def _refusal(self, call, *args):
        """Run a save that must be refused, and check that it wrote nothing anywhere."""
        rows, state = self._rows(), self.account.snapshot()
        writes, seen = list(self.account.writes), len(self.account.calls)
        with self.assertRaises(HTTPException) as caught:
            call(*args)
        self.assertEqual(self.account.writes, writes)
        self.assertEqual(self.account.snapshot(), state)
        self.assertEqual(self._rows(), rows)
        self.assertEqual([c for c in self.account.calls[seen:] if c[1] in WRITE_CALLS], [])
        return caught.exception.status_code, caught.exception.detail


class ThePoliciesFieldIsCleanedAndBoundedTests(unittest.TestCase):
    def test_ids_are_trimmed_and_sent_once(self):
        body = _body("new", access_policy_ids=[" p-ops ", "p-team", "", "  ", "p-ops"])
        self.assertEqual(body.access_policy_ids, ["p-ops", "p-team"])

    def test_a_value_that_is_not_an_id_is_refused(self):
        # Each one is written into a request to Cloudflare.
        for bad in ("../apps", "p team", "x/y", "a" * 65, "p?x=1"):
            with self.subTest(bad=bad):
                with self.assertRaises(pydantic.ValidationError) as caught:
                    _body("new", access_policy_ids=[bad])
                self.assertIn("Invalid Access policy id", str(caught.exception))

    def test_the_number_of_policies_is_bounded_once_duplicates_are_dropped(self):
        many = [f"p-{i}" for i in range(26)]
        with self.assertRaises(pydantic.ValidationError) as caught:
            _body("new", access_policy_ids=many)
        self.assertIn("At most 25 Access policies per service", str(caught.exception))
        self.assertEqual(len(_body("new", access_policy_ids=many[:25]).access_policy_ids), 25)
        twice = _body("new", access_policy_ids=[*many[:25], "p-0"])
        self.assertEqual(len(twice.access_policy_ids), 25)

    def test_policies_are_refused_outside_tunnel_mode(self):
        proxied = {"expose_mode": "proxy_dns", "tunnel_provider_id": None, "proxy_provider_id": 3}
        with self.assertRaises(pydantic.ValidationError) as caught:
            _body("new", access_policy_ids=["p-team"], **proxied)
        self.assertIn("Access policies apply to tunnel mode only", str(caught.exception))
        self.assertEqual(_body("new", access_policy_ids=[], **proxied).access_policy_ids, [])

    def test_the_preflight_takes_the_field_the_save_takes(self):
        # Its model forbids unknown keys, so a client that sends one body to both would
        # otherwise have its preflight refused.
        body = services_api.ServicePreflightIn(
            subdomain="new", domain="example.com", target_ip="192.168.1.10", target_port=8080,
            expose_mode="tunnel", tunnel_provider_id=1, service_id=4,
            access_policy_ids=[" p-team "],
        )
        self.assertEqual(body.access_policy_ids, ["p-team"])


class AServiceIsCreatedBehindItsLoginTests(_Case):
    """`POST /api/services` with `access_policy_ids`: the application first, then the route."""

    def setUp(self):
        super().setUp()
        # Something to read, so that the account answers `ok` and the checks run.
        self.account.application("other.example.com", TEAM, app_id="app-other")
        self.account.guarded = {"new.example.com"}

    def test_the_application_is_created_before_the_route_and_recorded(self):
        status, body = self._post(_body("new", access_policy_ids=["p-team", "p-ops"]))
        self.assertEqual((status, body["errors"]), (201, []))
        self.assertEqual(body["access"], {"application_id": "app-1", "hostname": "new.example.com",
                                          "policy_ids": ["p-team", "p-ops"]})
        self.assertEqual(self.account.writes, [(1, "create_access_app", "new.example.com"),
                                               (1, "serve", "new.example.com")])
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
        created = self.account.apps["app-1"]
        self.assertEqual([p["id"] for p in created["policies"]], ["p-team", "p-ops"])
        self.assertEqual(created["session_duration"], "24h")
        self.assertEqual(self.account.violations, [])
        self.assertIn(("info", "Access: application created for new.example.com (2 policies)"),
                      self._logs())

    def test_a_name_already_protected_is_refused_before_anything_is_written(self):
        own = ("new.example.com already has an Access application of its own (new.example.com), "
               "maybe left by a save that timed out. Edit it in Cloudflare, or delete it there "
               "and try again.")
        for app_id, pattern, said in (
            ("app-exact", "new.example.com", own),
            ("app-wild", "*.example.com", "new.example.com is already protected by Access"),
        ):
            with self.subTest(pattern=pattern):
                self.account.application(pattern, OPS, app_id=app_id)
                try:
                    got = self._refusal(self._post, _body("new", access_policy_ids=["p-team"]))
                finally:
                    del self.account.apps[app_id]
                self.assertEqual(got, (409, f"Nothing was created -- {said}"))
        self.assertEqual(self._rows(), [])

    def test_a_name_with_an_application_of_its_own_never_gets_a_second_one(self):
        """Whatever the first one lets in: two exact applications leave Cloudflare to choose."""
        self.account.policies.extend([OPEN, ANYONE])
        for policy in (OPEN, ANYONE):
            with self.subTest(policy=policy["id"]):
                self.account.application("new.example.com", policy, app_id="app-hand")
                try:
                    got = self._refusal(self._post, _body("new", access_policy_ids=["p-team"]))
                finally:
                    del self.account.apps["app-hand"]
                self.assertEqual(got[0], 409)
                self.assertIn("already has an Access application of its own (new.example.com)",
                              got[1])
        self.assertNotIn("create_access_app", [c[1] for c in self.account.calls])

    def test_a_wildcard_that_lets_anyone_in_does_not_stop_a_stricter_application(self):
        self.account.policies.append(ANYONE)
        self.account.application("*.example.com", ANYONE, app_id="app-wild")
        status, body = self._post(_body("new", access_policy_ids=["p-team"]))
        self.assertEqual((status, body["access"]["application_id"]), (201, "app-1"))
        self.assertEqual(self.account.violations, [])

    def test_a_policy_the_account_does_not_hold_is_named(self):
        got = self._refusal(self._post, _body("new", access_policy_ids=["p-team", "p-gone"]))
        self.assertEqual(got, (400, "Nothing was created -- unknown Access policy: p-gone"))

    def test_an_application_cloudflare_will_not_create_stops_the_save(self):
        for mode, status, said in (
            ("refused", 403, "Nothing was created -- the tunnel token may not create Access "
                             f"applications: add {EDIT}"),
            ("error", 502, "Nothing was created -- Cloudflare did not create the Access "
                           "application"),
            # Recorded, the id would be written into the path of the service's deletion.
            ("malformed", 502, "Nothing was created -- Cloudflare did not create the Access "
                               "application"),
            ("raise", 502, "Nothing was created -- Cloudflare timed out"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"create_access_app": mode}
                got = self._refusal(self._post, _body("new", access_policy_ids=["p-team"]))
                self.assertEqual(got, (status, said))
        self.assertEqual(self._rows(), [])

    def test_an_account_that_does_not_answer_stops_the_save(self):
        self.account.fail = {"access_overview": "raise"}
        got = self._refusal(self._post, _body("new", access_policy_ids=["p-team"]))
        self.assertEqual(got, (502, "Nothing was created -- Cloudflare timed out"))

    def test_an_account_the_token_cannot_read_stops_the_save_before_any_creation(self):
        # Both checks stand on the listing: without it, a second application could land on a
        # name, or a looser one in front of a stricter wildcard. A listing cut short is as
        # blind as a failed one.
        for mode, status, said in (
            ("missing", 403, f"the tunnel token may not read Access applications: add {READ}"),
            ("error", 502, "Cloudflare did not return the Access applications"),
            ("truncated", 502, "Cloudflare did not return the Access applications"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"access_overview": mode}
                got = self._refusal(self._post, _body("new", access_policy_ids=["p-team"]))
                self.assertEqual(got, (status, f"Nothing was created -- {said}"))
        self.assertNotIn("create_access_app", [c[1] for c in self.account.calls])

    def test_an_account_without_a_single_application_still_gets_its_application(self):
        # An empty listing is what such an account answers. Cloudflare's answer to the
        # creation then decides, as on `POST /api/providers/{pid}/access/apps`.
        self.account.fail = {"access_overview": "unverified"}
        status, body = self._post(_body("new", access_policy_ids=["p-team"]))
        self.assertEqual((status, body["access"]["hostname"]), (201, "new.example.com"))
        self.assertEqual(self.account.violations, [])

    def test_a_provider_that_is_not_a_tunnel_is_refused_without_a_call(self):
        got = self._refusal(self._post, _body("new", tunnel=3, access_policy_ids=["p-team"]))
        self.assertEqual(got, (400, "Nothing was created -- Access policies need a Cloudflare "
                                    "Tunnel provider"))
        self.assertEqual(self.account.calls, [])

    def test_a_service_created_disabled_still_gets_its_application(self):
        # So that enabling it later publishes a name that is already behind a login.
        status, body = self._post(_body("new", enabled=False, access_policy_ids=["p-team"]))
        self.assertEqual((status, body["access"]["application_id"]), (201, "app-1"))
        self.assertEqual(self.account.writes, [(1, "create_access_app", "new.example.com")])
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")

    def test_a_route_that_fails_to_publish_keeps_its_application(self):
        # A failed `create_host` may still have written the ingress rule: the name can
        # answer, so the application stays and the row records it.
        for mode, said in (("refused", "Failed to create tunnel route"),
                           ("partial", "Failed to create tunnel route"),
                           ("raise", "the DNS record timed out")):
            with self.subTest(mode=mode):
                self.account.fail = {"create_host": mode}
                self.account.guarded.add(f"{mode}.example.com")
                status, body = self._post(_body(mode, access_policy_ids=["p-team"]))
                self.assertEqual((status, body["errors"]), (207, [said]))
                recorded = self._rows()[-1]["access_app_id"]
                self.assertEqual(recorded, body["access"]["application_id"])
                self.assertIn(recorded, self.account.apps)
        self.assertEqual(self.account.violations, [])

    def test_without_policies_nothing_is_asked_of_access(self):
        status, body = self._post(_body("plain"))
        self.assertEqual(status, 201)
        self.assertNotIn("access", body)
        self.assertEqual(self._access_calls(), [])
        self.assertEqual(self._rows()[0]["access_app_id"], "")

    # The race: a row holding the same (subdomain, domain) while it publishes another name
    # passes the check at the top of the route and trips the unique index at the INSERT,
    # after the application was created.
    TAKEN = ("new.example.com is already served by another service. Two services on one "
             "hostname push over each other -- edit that one, or choose another hostname.")

    def test_a_save_that_loses_the_race_removes_an_application_nothing_serves(self):
        self._seed("new", tunnel_hostname="elsewhere.example.com")
        with self.assertRaises(HTTPException) as caught:
            self._post(_body("new", enabled=False, access_policy_ids=["p-team"]))
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (409, self.TAKEN))
        self.assertNotIn("app-1", self.account.apps)
        self.assertIn(("info", "[Refused] Access application removed on Home: new.example.com"),
                      self._logs())

    def test_a_save_that_loses_the_race_keeps_the_application_of_a_served_name(self):
        self._seed("new", tunnel_hostname="elsewhere.example.com")
        with self.assertRaises(HTTPException) as caught:
            self._post(_body("new", access_policy_ids=["p-team"]))
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (409, self.TAKEN))
        self.assertIn("app-1", self.account.apps)
        self.assertEqual(self.account.violations, [])
        self.assertIn(("warning", "The Access application created for new.example.com belongs "
                                  "to no service now that the save was refused: Access "
                                  "application left in place: the route is still served"),
                      self._logs())

    def test_the_preflight_asks_access_nothing(self):
        body = services_api.ServicePreflightIn(
            subdomain="new", domain="example.com", target_ip="192.168.1.10", target_port=8080,
            expose_mode="tunnel", tunnel_provider_id=1, access_policy_ids=["p-team"],
        )
        with patch.object(services_api, "_service_target_reachable",
                          side_effect=AssertionError("a tunnel target is not probed")):
            services_api.preflight_service(_request("POST", "/api/services/preflight"), body)
        self.assertEqual(self.account.calls, [(1, "health_status")])


class ARenamedServiceTakesItsLoginAlongTests(_Case):
    """`PUT /api/services/{sid}` that moves a protected tunnel route to another name."""

    MOVED = {"application_id": "app-1", "hostname": "new.example.com",
             "policy_ids": ["p-team", "p-ops"]}
    LEFT = "app.example.com: Access application left in place"

    def setUp(self):
        super().setUp()
        self._arrange()

    def _arrange(self):
        """A service on app.example.com behind the application Vauxtra created for it."""
        self._sql("DELETE FROM services")
        self._new_account()
        self.sid = self._seed("app", app_id="app-old")
        # Precedences out of list order, to see that the new application keeps theirs.
        self.account.application("app.example.com", {**OPS, "precedence": 2},
                                 {**TEAM, "precedence": 1}, app_id="app-old", session="8h")
        self.account.served[1].add("app.example.com")
        self.account.guarded = {"app.example.com", "new.example.com"}

    def _refused_because(self, why):
        return (
            "Nothing was changed -- new.example.com would be published without Access: the "
            f"Access application recorded for app.example.com cannot follow the rename ({why}). "
            "Protect new.example.com in Cloudflare first, or send access_policy_ids for it."
        )

    def _moved(self, body, writes):
        result = self._put(self.sid, body)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["access"], self.MOVED)
        self.assertEqual(self.account.writes, writes)
        self.assertEqual(set(self.account.apps), {"app-1"})
        created = self.account.apps["app-1"]
        self.assertEqual([(p["id"], p["precedence"]) for p in created["policies"]],
                         [("p-team", 1), ("p-ops", 2)])
        self.assertEqual(created["session_duration"], "8h")
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
        self.assertEqual(self.account.violations, [])

    def test_a_rename_on_the_same_tunnel(self):
        self._moved(_body("new"), [(1, "create_access_app", "new.example.com"),
                                   (1, "unserve", "app.example.com"),
                                   (1, "serve", "new.example.com"),
                                   (1, "delete_access_app", "app-old")])
        self.assertIn(("info", "[Rename] Access application removed on Home: app.example.com"),
                      self._logs())

    def test_a_rename_onto_another_tunnel(self):
        self._moved(_body("new", tunnel=2), [(2, "create_access_app", "new.example.com"),
                                             (2, "serve", "new.example.com"),
                                             (1, "unserve", "app.example.com"),
                                             (1, "delete_access_app", "app-old")])

    def test_a_rename_through_the_tunnel_hostname(self):
        self._moved(_body("app", tunnel_hostname="new.example.com"),
                    [(1, "create_access_app", "new.example.com"),
                     (1, "unserve", "app.example.com"),
                     (1, "serve", "new.example.com"),
                     (1, "delete_access_app", "app-old")])

    def test_a_rename_that_also_disables_the_service(self):
        self._moved(_body("new", enabled=False), [(1, "create_access_app", "new.example.com"),
                                                  (1, "unserve", "app.example.com"),
                                                  (1, "delete_access_app", "app-old")])

    def test_a_move_to_another_tunnel_under_the_same_name_keeps_its_application(self):
        result = self._put(self.sid, _body("app", tunnel=2))
        self.assertEqual(result["errors"], [])
        self.assertNotIn("access", result)
        self.assertEqual(self.account.writes, [(2, "serve", "app.example.com"),
                                               (1, "unserve", "app.example.com")])
        self.assertEqual(self._access_calls(), [])
        self.assertEqual(self._rows()[0]["access_app_id"], "app-old")

    def test_an_application_without_a_policy_is_carried_closed(self):
        # Access refuses whoever no policy lets in: the copy is as closed as the original.
        self.account.apps["app-old"]["policies"] = []
        result = self._put(self.sid, _body("new"))
        self.assertEqual(result["access"]["policy_ids"], [])
        self.assertEqual(self.account.apps["app-1"]["policies"], [])
        self.assertEqual(self.account.violations, [])

    def test_a_route_that_fails_to_move_never_leaves_a_served_name_open(self):
        # The new application stays recorded whatever the route did: a failed `update_host`
        # may have written the new rule, and a retry no longer sees a rename.
        for mode, errors, apps in (
            ("refused", ["Failed to update tunnel route"], {"app-1"}),
            ("partial", ["Failed to update tunnel route"], {"app-1"}),
            ("raise", ["the DNS record timed out"], {"app-1"}),
            ("sticky", [f"{self.LEFT}: the route is still served"], {"app-old", "app-1"}),
        ):
            with self.subTest(mode=mode):
                self._arrange()
                self.account.fail = {"update_host": mode}
                result = self._put(self.sid, _body("new"))
                self.assertEqual(result["errors"], errors)
                self.assertEqual(set(self.account.apps), apps)
                self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
                self.assertEqual(self.account.violations, [])

    def test_each_later_step_that_fails_keeps_the_old_application(self):
        for fail, tunnel, errors in (
            ({(1, "delete_host"): "refused"}, 2,
             ["Failed to withdraw the previous tunnel route: app.example.com",
              f"{self.LEFT}: the route is still served"]),
            ({(2, "create_host"): "refused"}, 2,
             ["Failed to update tunnel route", f"{self.LEFT}: the route is still served"]),
            ({"serving_rule": "refused"}, 1,
             [f"{self.LEFT}: Cloudflare would not confirm the route is gone"]),
            ({"delete_access_app": "refused"}, 1,
             [f"{self.LEFT}: the tunnel token may not delete it, add {EDIT}"]),
            ({"delete_access_app": "error"}, 1,
             ["app.example.com: Failed to delete the Access application on Home"]),
            ({"delete_access_app": "raise"}, 1,
             ["app.example.com: Access (Home): Cloudflare timed out"]),
        ):
            with self.subTest(fail=fail):
                self._arrange()
                self.account.fail = fail
                result = self._put(self.sid, _body("new", tunnel=tunnel))
                self.assertEqual(result["errors"], errors)
                self.assertEqual(set(self.account.apps), {"app-old", "app-1"})
                self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
                self.assertEqual(self.account.violations, [])

    def test_an_application_deleted_in_the_dashboard_is_said_and_not_recreated(self):
        del self.account.apps["app-old"]
        result = self._put(self.sid, _body("new"))
        said = ("new.example.com is published without Access: the Access application recorded "
                "for app.example.com no longer exists in Cloudflare")
        self.assertEqual(result["errors"], [said])
        self.assertIn(("warning", said), self._logs())
        self.assertNotIn("access", result)
        self.assertEqual((self._rows()[0]["access_app_id"], self.account.apps), ("", {}))
        # The guard is not blind: it sees the one name published without a login, which had
        # none before the rename either.
        self.assertEqual(self.account.violations,
                         [((1, "serve", "new.example.com"), "new.example.com")])

    def test_a_disabled_service_is_told_its_new_name_will_open_once_enabled(self):
        del self.account.apps["app-old"]
        result = self._put(self.sid, _body("new", enabled=False))
        self.assertEqual(result["errors"], [
            "new.example.com will be published without Access once enabled: the Access "
            "application recorded for app.example.com no longer exists in Cloudflare"])
        self.assertEqual(self.account.violations, [])

    def test_an_application_edited_in_the_dashboard_stays_when_the_new_name_is_covered(self):
        # It stays in Cloudflare, and on the row only while it covers the new name: a row
        # never claims a login that stands in front of other names than its own.
        untracked = ("warning", "Access: the application app-old, recorded for app.example.com, "
                                "is no longer tracked by this service and stays in Cloudflare")
        for also, wildcard, kept in (("new.example.com", False, "app-old"),
                                     ("other.example.com", True, "")):
            with self.subTest(also=also):
                self._arrange()
                self._sql("DELETE FROM logs")
                self.account.apps["app-old"]["destinations"].append({"type": "public",
                                                                     "uri": also})
                if wildcard:
                    self.account.application("*.example.com", TEAM, app_id="app-wild")
                result = self._put(self.sid, _body("new"))
                self.assertEqual(result["errors"], [])
                self.assertNotIn("access", result)
                self.assertEqual(self._rows()[0]["access_app_id"], kept)
                self.assertEqual(untracked in self._logs(), not kept)
                self.assertIn("app-old", self.account.apps)
                self.assertNotIn("create_access_app", [c[1] for c in self.account.calls])
                self.assertEqual(self.account.violations, [])

    def test_an_application_that_cannot_follow_refuses_a_rename_that_would_open_the_name(self):
        edited = {"type": "public", "uri": "other.example.com"}
        for why, arrange in (
            ("it was changed in the Cloudflare dashboard",
             lambda: self.account.apps["app-old"]["destinations"].append(edited)),
            ("it uses policies that are not reusable",
             lambda: self.account.apps["app-old"]["policies"][0].update(reusable=False)),
            ("Vauxtra cannot read its policies",
             lambda: self.account.apps["app-old"]["policies"][0].pop("id")),
        ):
            with self.subTest(why=why):
                self._arrange()
                arrange()
                got = self._refusal(self._put, self.sid, _body("new"))
                self.assertEqual(got, (409, self._refused_because(why)))

    def test_a_recorded_id_there_is_no_way_to_ask_about_refuses_the_rename(self):
        # Neither counts as an application that is gone: that would let the new name open.
        for change in ("UPDATE services SET access_app_id='../apps'",
                       "UPDATE services SET tunnel_provider_id=NULL"):
            with self.subTest(change=change):
                self._arrange()
                self._sql(change)
                got = self._refusal(self._put, self.sid, _body("new"))
                self.assertEqual(got, (409, self._refused_because("Vauxtra cannot read it")))
                # An id without the shape of one is never sent to Cloudflare.
                self.assertNotIn("get_access_app", [c[1] for c in self.account.calls])

    def test_an_account_that_cannot_be_read_refuses_the_rename_it_would_have_to_check(self):
        self.account.apps["app-old"]["destinations"].append({"type": "public",
                                                             "uri": "other.example.com"})
        for mode, status, said in (
            ("missing", 403, "Nothing was changed -- the tunnel token may not read Access "
                             f"applications: add {READ}"),
            ("error", 502, "Nothing was changed -- Cloudflare did not return the Access "
                           "applications"),
            ("truncated", 502, "Nothing was changed -- Cloudflare did not return the Access "
                               "applications"),
            ("unverified", 409, self._refused_because("it was changed in the Cloudflare "
                                                      "dashboard")),
            ("raise", 502, "Nothing was changed -- Cloudflare timed out"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"access_overview": mode}
                self.assertEqual(self._refusal(self._put, self.sid, _body("new")),
                                 (status, said))

    def test_a_recorded_application_that_cannot_be_read_refuses_the_rename(self):
        for mode, status, said in (
            ("refused", 403, "Nothing was changed -- the tunnel token may not read the Access "
                             f"application of app.example.com: add {READ}"),
            ("error", 502, "Nothing was changed -- Cloudflare did not return the Access "
                           "application of app.example.com"),
            ("raise", 502, "Nothing was changed -- Cloudflare timed out"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"get_access_app": mode}
                self.assertEqual(self._refusal(self._put, self.sid, _body("new")),
                                 (status, said))

    def test_a_new_application_cloudflare_will_not_create_refuses_the_rename(self):
        for mode, status, said in (
            ("refused", 403, "Nothing was changed -- the tunnel token may not create Access "
                             f"applications: add {EDIT}"),
            ("error", 502, "Nothing was changed -- Cloudflare did not create the Access "
                           "application"),
            ("malformed", 502, "Nothing was changed -- Cloudflare did not create the Access "
                               "application"),
            ("raise", 502, "Nothing was changed -- Cloudflare timed out"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"create_access_app": mode}
                self.assertEqual(self._refusal(self._put, self.sid, _body("new")),
                                 (status, said))

    def test_a_rename_that_loses_the_race_keeps_the_application_of_the_name_it_published(self):
        self._seed("new", tunnel_hostname="other.example.com")
        with self.assertRaises(HTTPException) as caught:
            self._put(self.sid, _body("new"))
        self.assertEqual(caught.exception.status_code, 409)
        row = self._rows()[0]
        self.assertEqual((row["subdomain"], row["access_app_id"]), ("app", "app-old"))
        self.assertEqual(set(self.account.apps), {"app-old", "app-1"})
        self.assertEqual(self.account.violations, [])

    def test_a_rename_without_a_recorded_application_only_reads_the_account(self):
        # For a login written by hand on the old name: there is none, and nothing is created.
        self._sql("UPDATE services SET access_app_id=''")
        del self.account.apps["app-old"]
        self.account.guarded = set()
        result = self._put(self.sid, _body("new"))
        self.assertEqual(result["errors"], [])
        self.assertNotIn("access", result)
        self.assertEqual(self._access_calls(), [(1, "access_overview")])

    def test_an_edit_that_keeps_the_name_asks_access_nothing(self):
        result = self._put(self.sid, _body("app", target_port=9090))
        self.assertEqual(result["errors"], [])
        self.assertEqual(self._access_calls(), [])
        self.assertEqual(self._rows()[0]["access_app_id"], "app-old")
        self.assertEqual(self.account.violations, [])


class AnEditSendsItsOwnPoliciesTests(_Case):
    """`PUT /api/services/{sid}` with `access_policy_ids`."""

    def setUp(self):
        super().setUp()
        self.sid = self._seed("app")
        self.account.served[1].add("app.example.com")
        self.account.application("other.example.com", TEAM, app_id="app-other")

    def _record(self, app_id):
        self._sql("UPDATE services SET access_app_id=? WHERE id=?", app_id, self.sid)

    def test_policies_sent_on_an_edit_protect_the_name_before_the_route_is_rewritten(self):
        result = self._put(self.sid, _body("app", target_port=9090, access_policy_ids=["p-team"]))
        self.assertEqual(result["access"], {"application_id": "app-1",
                                            "hostname": "app.example.com",
                                            "policy_ids": ["p-team"]})
        methods = [c[1] for c in self.account.calls]
        self.assertLess(methods.index("create_access_app"), methods.index("update_host"))
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")

    def test_a_name_whose_recorded_application_is_alive_is_refused(self):
        self.account.application("app.example.com", TEAM, app_id="app-old")
        self._record("app-old")
        got = self._refusal(self._put, self.sid, _body("app", access_policy_ids=["p-ops"]))
        self.assertEqual(got, (409, "Nothing was changed -- app.example.com is already "
                                    "protected by Access (app.example.com)"))

    def test_a_name_another_application_protects_is_refused(self):
        self.account.application("app.example.com", OPS, app_id="app-hand")
        got = self._refusal(self._put, self.sid, _body("app", access_policy_ids=["p-team"]))
        self.assertEqual(got, (409, "Nothing was changed -- app.example.com already has an "
                                    "Access application of its own (app.example.com), maybe "
                                    "left by a save that timed out. Edit it in Cloudflare, or "
                                    "delete it there and try again."))

    def test_a_name_whose_own_application_lets_anyone_in_gets_no_second_one(self):
        self.account.policies.append(ANYONE)
        self.account.application("app.example.com", ANYONE, app_id="app-hand")
        got = self._refusal(self._put, self.sid, _body("app", access_policy_ids=["p-team"]))
        self.assertEqual(got[0], 409)
        self.assertIn("already has an Access application of its own", got[1])

    def test_an_account_the_token_cannot_read_protects_nothing(self):
        for mode, status, said in (
            ("missing", 403, f"the tunnel token may not read Access applications: add {READ}"),
            ("error", 502, "Cloudflare did not return the Access applications"),
            ("truncated", 502, "Cloudflare did not return the Access applications"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"access_overview": mode}
                got = self._refusal(self._put, self.sid,
                                    _body("app", access_policy_ids=["p-team"]))
                self.assertEqual(got, (status, f"Nothing was changed -- {said}"))
        self.assertNotIn("create_access_app", [c[1] for c in self.account.calls])

    def test_a_recorded_id_that_covers_another_name_does_not_count_as_protection(self):
        # The id was never the login of this name: it is let go, said, and the name protected.
        self._record("app-other")
        result = self._put(self.sid, _body("app", access_policy_ids=["p-ops"]))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["access"]["application_id"], "app-1")
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
        self.assertIn("app-other", self.account.apps)
        self.assertIn(("warning", "Access: the application app-other, recorded for "
                                  "app.example.com, is no longer tracked by this service and "
                                  "stays in Cloudflare"), self._logs())

    def test_a_recorded_application_deleted_in_the_dashboard_is_replaced(self):
        self._record("app-old")
        result = self._put(self.sid, _body("app", access_policy_ids=["p-team"]))
        self.assertEqual(result["access"]["application_id"], "app-1")
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")

    def test_a_recorded_application_that_cannot_be_read_is_not_covered_twice(self):
        self._record("app-old")
        self.account.fail = {"get_access_app": "refused"}
        got = self._refusal(self._put, self.sid, _body("app", access_policy_ids=["p-team"]))
        self.assertEqual(got, (403, "Nothing was changed -- the tunnel token may not read the "
                                    f"Access application of app.example.com: add {READ}"))

    def test_policies_sent_with_a_rename_win_over_the_ones_it_would_carry(self):
        self.account.application("app.example.com", TEAM, OPS, app_id="app-old", session="8h")
        self._record("app-old")
        self.account.guarded = {"app.example.com", "new.example.com"}
        result = self._put(self.sid, _body("new", access_policy_ids=["p-ops"]))
        self.assertEqual(result["access"], {"application_id": "app-1",
                                            "hostname": "new.example.com",
                                            "policy_ids": ["p-ops"]})
        created = self.account.apps["app-1"]
        self.assertEqual(([p["id"] for p in created["policies"]], created["session_duration"]),
                         (["p-ops"], "24h"))
        self.assertNotIn("app-old", self.account.apps)
        self.assertEqual(self.account.violations, [])


class AMoveReadsWhatAlreadyProtectsTheNewNameTests(_Case):
    """A rename reads the account before it creates the application of its new name.

    It used to create one blindly. Under a strict wildcard, the copy of a looser application
    won over the wildcard and opened a name that `POST /api/services` refuses to touch, and
    next to an application of the name's own it made a second one.
    """

    def setUp(self):
        super().setUp()
        self.account.policies.extend([OPEN, ANYONE])
        self.account.application("*.team.example.com", OPS, app_id="app-joker")

    def _service(self, policy):
        """A service on x.example.com, which the wildcard does not cover, behind `policy`."""
        status, body = self._post(_body("x", access_policy_ids=[policy]))
        self.assertEqual((status, body["errors"]), (201, []))
        self.account.guarded = {"b.team.example.com"}
        return self._rows()[0]["id"]

    def _move(self, sid, **fields):
        return self._put(sid, _body("b", domain="team.example.com", **fields))

    def test_a_looser_application_is_not_copied_in_front_of_a_stricter_wildcard(self):
        sid = self._service("p-open")
        got = self._refusal(self._post, _body("b", domain="team.example.com",
                                              access_policy_ids=["p-open"]))
        self.assertEqual(got, (409, "Nothing was created -- b.team.example.com is already "
                                    "protected by Access"))
        seen = len(self.account.calls)
        result = self._move(sid)
        self.assertEqual(result["errors"], [])
        self.assertNotIn("access", result)
        after = [c[1] for c in self.account.calls[seen:] if c[1] in ACCESS_CALLS]
        self.assertIn("access_overview", after)
        self.assertNotIn("create_access_app", after)
        self.assertEqual(self._rows()[0]["access_app_id"], "")
        self.assertNotIn("app-1", self.account.apps)
        logs = self._logs()
        self.assertIn(("info", "Access: no application created for b.team.example.com: "
                               "*.team.example.com already protects it at least as well as the "
                               "one recorded for x.example.com"), logs)
        self.assertIn(("info", "[Rename] Access application removed on Home: x.example.com"),
                      logs)
        self.assertEqual(self._state("b.team.example.com"), "protected")
        self.assertEqual(self.account.violations, [])

    def test_an_application_of_the_new_name_s_own_is_kept_rather_than_doubled(self):
        self.account.application("b.team.example.com", OPS, app_id="app-main")
        sid = self._service("p-team")
        result = self._move(sid)
        self.assertEqual(result["errors"], [])
        exact = sorted(i for i, a in self.account.apps.items()
                       if access.covers_only(a, "b.team.example.com"))
        self.assertEqual(exact, ["app-main"])
        self.assertEqual(self._rows()[0]["access_app_id"], "")
        self.assertNotIn("app-1", self.account.apps)
        self.assertIn(("info", "Access: no application created for b.team.example.com: "
                               "b.team.example.com already protects it at least as well as the "
                               "one recorded for x.example.com"), self._logs())
        self.assertEqual(self.account.violations, [])

    def test_an_application_of_the_new_name_s_own_that_lets_in_more_refuses_the_move(self):
        self.account.application("b.team.example.com", ANYONE, app_id="app-main")
        sid = self._service("p-team")
        self.assertEqual(self._refusal(self._move, sid), (
            409,
            "Nothing was changed -- b.team.example.com would be published less protected than "
            "x.example.com: it already has an Access application of its own "
            "(b.team.example.com) that lets in more than the one recorded for x.example.com. "
            "Tighten or delete b.team.example.com in Cloudflare first.",
        ))

    def test_at_equal_strength_the_service_keeps_its_own_policies(self):
        # The wildcard lets in Ops, the service Team: a route behind Team stays behind Team.
        sid = self._service("p-team")
        result = self._move(sid)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["access"], {"application_id": "app-2",
                                            "hostname": "b.team.example.com",
                                            "policy_ids": ["p-team"]})
        self.assertIn((1, "create_access_app", "b.team.example.com", ("p-team",), "24h"),
                      self.account.calls)
        self.assertEqual(self._rows()[0]["access_app_id"], "app-2")
        self.assertNotIn("app-1", self.account.apps)
        self.assertEqual(self.account.violations, [])

    def test_an_account_that_cannot_be_read_creates_nothing_on_a_move(self):
        sid = self._service("p-team")
        for mode, status, said in (
            ("missing", 403, f"the tunnel token may not read Access applications: add {READ}"),
            ("error", 502, "Cloudflare did not return the Access applications"),
            ("truncated", 502, "Cloudflare did not return the Access applications"),
        ):
            with self.subTest(mode=mode):
                self.account.fail = {"access_overview": mode}
                seen = len(self.account.calls)
                got = self._refusal(self._move, sid)
                self.assertEqual(got, (status, f"Nothing was changed -- {said}"))
                self.assertNotIn("create_access_app",
                                 [c[1] for c in self.account.calls[seen:]])
        # An empty listing is an account without a single application: the move goes on.
        self.account.fail = {"access_overview": "unverified"}
        self.assertEqual(self._move(sid)["access"]["hostname"], "b.team.example.com")


class ALoginSetByHandIsNotLeftBehindTests(_Case):
    """A rename on the tunnel of a name protected by an application no service records.

    The new name used to be published without that login, and nothing said so. A rename
    that would publish it less protected than the old one is now refused, as long as the
    account can be read: without that, nothing says Access is in use.
    """

    REFUSED = (
        "Nothing was changed -- b.example.com would be published without Access: "
        "a.example.com is protected by an Access application Vauxtra did not create "
        "(a.example.com). Protect b.example.com in Cloudflare first, or send "
        "access_policy_ids for it."
    )

    def setUp(self):
        super().setUp()
        self.account.policies.append(ANYONE)
        self.account.application("a.example.com", OPS, app_id="app-main")
        self.account.served[1] = {"a.example.com"}
        self.sid = self._seed("a")
        self.account.guarded = {"b.example.com"}

    def test_a_name_protected_by_hand_is_not_renamed_into_an_open_one(self):
        self.assertEqual(self._refusal(self._put, self.sid, _body("b")), (409, self.REFUSED))

    def test_a_name_partly_protected_by_hand_says_so(self):
        self.account.apps["app-main"]["policies"] = [{"precedence": 1, "reusable": True,
                                                      **ANYONE}]
        self.assertEqual(self._refusal(self._put, self.sid, _body("b")),
                         (409, self.REFUSED.replace("is protected by", "is partly protected by")))

    def test_a_new_name_less_protected_than_the_old_one_is_refused_too(self):
        self.account.application("b.example.com", ANYONE, app_id="app-loose")
        self.assertEqual(
            self._refusal(self._put, self.sid, _body("b")),
            (409, self.REFUSED.replace("would be published without Access",
                                       "would be published less protected than a.example.com")),
        )

    def test_a_new_name_protected_as_well_is_renamed_without_a_word(self):
        self.account.application("*.example.com", TEAM, app_id="app-wild")
        result = self._put(self.sid, _body("b"))
        self.assertEqual(result["errors"], [])
        self.assertNotIn("access", result)
        self.assertEqual(self._access_calls(), [(1, "access_overview")])
        self.assertEqual(self.account.served[1], {"b.example.com"})
        self.assertEqual(self.account.violations, [])

    def test_an_account_that_cannot_be_read_renames_as_before(self):
        # A Vauxtra that was given no Access permission must still be able to rename.
        self.account.guarded = set()
        for mode in ("missing", "error", "truncated", "unverified", "raise"):
            with self.subTest(mode=mode):
                self._sql("DELETE FROM services")
                sid = self._seed("a")
                self.account.served[1] = {"a.example.com"}
                self.account.fail = {"access_overview": mode}
                result = self._put(sid, _body("b"))
                self.assertEqual(result["errors"], [])
                self.assertEqual(self.account.served[1], {"b.example.com"})

    def test_a_recorded_application_that_is_gone_does_not_hide_the_one_set_by_hand(self):
        self._sql("UPDATE services SET access_app_id='app-gone' WHERE id=?", self.sid)
        self.assertEqual(self._refusal(self._put, self.sid, _body("b")), (409, self.REFUSED))

    def test_a_recorded_application_that_is_gone_is_said_covered_when_the_new_name_is(self):
        self._sql("UPDATE services SET access_app_id='app-gone' WHERE id=?", self.sid)
        del self.account.apps["app-main"]
        self.account.application("*.example.com", TEAM, app_id="app-wild")
        result = self._put(self.sid, _body("b"))
        self.assertEqual(result["errors"], [])
        self.assertIn(("info", "Access: the Access application recorded for a.example.com no "
                               "longer exists in Cloudflare; b.example.com is covered by "
                               "*.example.com"), self._logs())
        self.assertEqual(self._rows()[0]["access_app_id"], "")
        self.assertEqual(self.account.violations, [])


class AnIdKeptOutOfTheTunnelTests(_Case):
    """A service that left the tunnel for proxy_dns kept its Access id, and comes back.

    Nothing followed that application in between, so it is neither carried nor kept: the
    row lets the id go and says so. A new name is still held to what protected the old one.
    """

    UNTRACKED = ("warning", "Access: the application app-old, recorded for app.example.com, "
                            "is no longer tracked by this service and stays in Cloudflare")

    def setUp(self):
        super().setUp()
        self.sid = self._seed("app", app_id="app-old")
        self.account.application("app.example.com", TEAM, app_id="app-old")
        self._sql("UPDATE services SET expose_mode='proxy_dns', tunnel_provider_id=NULL, "
                  "proxy_provider_id=NULL, dns_provider_id=NULL, npm_host_id=NULL WHERE id=?",
                  self.sid)
        self.account.guarded = {"new.example.com"}

    def test_a_new_name_already_protected_lets_the_old_id_go(self):
        self.account.application("*.example.com", TEAM, app_id="app-wild")
        result = self._put(self.sid, _body("new"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(self._rows()[0]["access_app_id"], "")
        self.assertIn(self.UNTRACKED, self._logs())
        self.assertIn("app-old", self.account.apps)
        self.assertEqual(self.account.violations, [])

    def test_a_new_name_left_open_is_refused_and_says_whose_login_it_was(self):
        self.assertEqual(self._refusal(self._put, self.sid, _body("new")), (
            409,
            "Nothing was changed -- new.example.com would be published without Access: "
            "app.example.com is protected by the Access application this service recorded "
            "before it left the tunnel (app.example.com). Protect new.example.com in "
            "Cloudflare first, or send access_policy_ids for it.",
        ))

    def test_the_same_name_back_in_the_tunnel_lets_the_old_id_go(self):
        self.account.guarded = {"app.example.com"}
        result = self._put(self.sid, _body("app"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(self._rows()[0]["access_app_id"], "")
        self.assertIn(self.UNTRACKED, self._logs())
        self.assertIn("app-old", self.account.apps)
        self.assertEqual(self.account.violations, [])

    def test_policies_sent_on_the_way_back_protect_the_name_and_let_the_old_id_go(self):
        result = self._put(self.sid, _body("new", access_policy_ids=["p-ops"]))
        self.assertEqual(result["access"]["application_id"], "app-1")
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
        self.assertIn(self.UNTRACKED, self._logs())
        self.assertIn("app-old", self.account.apps)


class AWithdrawalSeesWhatTheTunnelStillAnswersForTests(_Case):
    """The application of a name the tunnel still answers for stays, whatever rule answers.

    `list_hosts` gives a wildcard rule as written and skips the last rule without a hostname,
    so a name whose own rule was gone looked unserved, and its application was deleted while
    the tunnel still sent the name to a service.
    """

    def _protected(self):
        status, _ = self._post(_body("a", access_policy_ids=["p-team"]))
        self.assertEqual(status, 201)
        return self._rows()[0]["id"]

    def test_a_wildcard_rule_keeps_the_application_of_the_name_it_serves(self):
        sid = self._protected()
        self.account.served[1].add("*.example.com")
        result = self._put(sid, _body("b"))
        self.assertEqual(result["errors"], [
            "a.example.com: Access application left in place: the tunnel still answers for "
            "this name through its rule for *.example.com"])
        self.assertIn("app-1", self.account.apps)

    def test_a_catch_all_rule_keeps_it_too(self):
        sid = self._protected()
        self.account.catch_all.add(1)
        result = self._put(sid, _body("b"))
        self.assertEqual(result["errors"], [
            "a.example.com: Access application left in place: the tunnel still answers for "
            "this name through its catch-all rule"])
        self.assertIn("app-1", self.account.apps)

    def test_a_save_that_loses_the_race_keeps_the_application_a_wildcard_serves(self):
        self._seed("new", tunnel_hostname="elsewhere.example.com")
        self.account.served[1].add("*.example.com")
        with self.assertRaises(HTTPException):
            self._post(_body("new", enabled=False, access_policy_ids=["p-team"]))
        self.assertIn("app-1", self.account.apps)
        self.assertIn(("warning", "The Access application created for new.example.com belongs "
                                  "to no service now that the save was refused: Access "
                                  "application left in place: the tunnel still answers for "
                                  "this name through its rule for *.example.com"), self._logs())


class ARowNeverKeepsAnIdThatDoesNotCoverItsNameTests(_Case):
    """An application edited in the dashboard stays where it is, and the row lets its id go.

    The row used to keep it on a rename, and the id then stood for a protection the name did
    not have: once the wildcard that covered the name was gone, protecting it was refused as
    already done.
    """

    def setUp(self):
        super().setUp()
        self.sid = self._seed("app", app_id="app-old")
        self.account.application("app.example.com", OPS, TEAM, app_id="app-old")
        self.account.served[1].add("app.example.com")
        self.account.apps["app-old"]["destinations"].append({"type": "public",
                                                             "uri": "other.example.com"})
        self.account.application("*.example.com", TEAM, app_id="app-wild")
        self.account.guarded = {"new.example.com"}

    def test_a_name_the_dashboard_left_open_can_still_be_protected(self):
        result = self._put(self.sid, _body("new"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(self._rows()[0]["access_app_id"], "")
        self.assertIn(("warning", "Access: the application app-old, recorded for "
                                  "app.example.com, is no longer tracked by this service and "
                                  "stays in Cloudflare"), self._logs())
        self.assertIn("app-old", self.account.apps)

        # The wildcard removed in the dashboard: the name is served, and open.
        del self.account.apps["app-wild"]
        self.assertEqual(self._state("new.example.com"), "open")
        result = self._put(self.sid, _body("new", access_policy_ids=["p-ops"]))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["access"]["application_id"], "app-1")
        self.assertEqual(self._rows()[0]["access_app_id"], "app-1")
        self.assertEqual(self._state("new.example.com"), "protected")
        self.assertEqual(self.account.violations, [])


class ThePanelSecondRequestNeverAddsASecondApplicationTests(_Case):
    """`POST /api/providers/{pid}/access/apps` after a rename has carried the application.

    The panel still protects in a second request. Once a rename had carried the route's
    application, that request put a second one on the same name whenever the first let
    anyone log in, and the row then recorded only the second.
    """

    def setUp(self):
        super().setUp()
        for name, value in (("create_provider", lambda row: self.tunnels[row["id"]]),
                            ("require_auth", lambda _req, scope=None: None)):
            patcher = patch.object(providers_api, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.account.policies.append(ANYONE)
        self.account.application("other.example.com", TEAM, app_id="app-other")

    def _protect(self, host, policies):
        return providers_api.protect_tunnel_route(
            1, _request("POST", "/api/providers/1/access/apps"),
            providers_api.AccessProtectIn(hostname=host, policy_ids=policies),
        )

    def _renamed(self, policy):
        """A route protected from the panel on a.example.com, then renamed to b.example.com."""
        status, _ = self._post(_body("a"))
        self.assertEqual(status, 201)
        self._protect("a.example.com", [policy])
        self._put(self._rows()[0]["id"], _body("b"))
        return self._rows()[0]["access_app_id"]

    def _refused(self, carried):
        got = self._refusal(self._protect, "b.example.com", ["p-ops"])
        self.assertEqual(got, (409, "b.example.com already has an Access application of its "
                                    "own (b.example.com), maybe left by a save that timed out. "
                                    "Edit it in Cloudflare, or delete it there and try again."))
        exact = [i for i, a in self.account.apps.items() if access.covers_only(a, "b.example.com")]
        self.assertEqual(exact, [carried])
        self.assertEqual(self._rows()[0]["access_app_id"], carried)

    def test_a_carried_application_that_keeps_strangers_out(self):
        self._refused(self._renamed("p-team"))

    def test_a_carried_application_that_lets_anyone_log_in(self):
        self._refused(self._renamed("p-all"))


if __name__ == "__main__":
    unittest.main()
