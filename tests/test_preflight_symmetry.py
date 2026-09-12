"""The preflight and the save route, held against each other on the same body.

Two ways the pair had drifted, and one rule that covers both.

`blocking` is a claim about POST /api/services, not a severity: the Expose panel greys out
"Create route" while `summary.blocking_failures` is above zero and offers nothing else to
press. Four checks made that claim and none of them held it -- `provider_disabled` on the
primary proxy, the same on the primary DNS server, and `tunnel_health` in each of its three
shapes. Measured through both routes on the same body, every one of them answered
`201 {"errors": []}` with the proxy host created, the DNS rewrite written, or the tunnel
ingress rule published. They joined `target_reachable`, which the previous pass had already
demoted for exactly this reason, and the test below is written as one rule over the whole
corpus rather than as four assertions a fifth check could walk around.

The second drift runs the other way: the preflight went silent about a target the save route
really publishes on. It de-duplicated the extra provider ids against
`proxy_provider_id or tunnel_provider_id` regardless of mode, while `add_service` blanks the
column the mode does not use before comparing. A proxy_dns body carrying
`tunnel_provider_id: 5` and `extra_proxy_provider_ids: [5]` therefore drew no
`extra_proxy_provider` line at all, and POST answered 201 with provider 5 holding the new
host. `_primary_push_targets` is now the single answer both functions get, and the test
compares the extras the preflight names against the rows `set_push_targets` actually wrote.

A third drift, and the one the corpus could not see: the badge speaks for
`PUT /api/services/{sid}` exactly as it speaks for the POST, and every case here only ever
drove the POST. A symmetry test that drives one of two routes cannot find an asymmetry
between them. Measured on a service holding `198.51.100.7` and edited with the address field
cleared, `dns_target_resolution` reported `blocking_failures: 1, ok: false` and the PUT
answered `200 {"errors": []}` -- a blanket `dns_ip = old["dns_ip"]` stood in front of the
refusal and fired in manual mode, where nothing is detected and so nothing can blip. On
`{proxy_provider_id: null, dns_provider_id: null}` the POST answered 400 and the PUT answered
200, with the hostname moved and nothing anywhere left to serve it. The same drift ran the
other way too: the preflight resolved the public target from nothing while the PUT resolved
it from the row, so a service in auto mode that already held a target read red and saved
green. All three are closed, and the rule below runs every corpus case through both routes,
so a scenario added later covers the pair without being told to.

`_MAY_BLOCK` is keyed on the branch rather than on the check name, for the reason the
doctrine in `services.py` is: `dns_provider` carries `provider_missing` (a 400 from
`_unknown_references`) and `provider_disabled` (a 201) under one name, and a list keyed on
the name had to admit both or neither. It admitted neither, so a branch that blocks for real
sat in no list at all and the corpus agreed with a doctrine that did not describe the code.

Last, the type declaration the panel is built from: `frontend/src/types/api.ts` lists the
check names a build knows, and three names the server had started emitting were missing from
it. That list is compared here against names measured coming out of the API.
"""

import json
import os
import re
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import services as services_api
from app.api import sync as sync_api

_ROOT = Path(__file__).resolve().parent.parent
_API_TYPES = _ROOT / "frontend" / "src" / "types" / "api.ts"

#: Every check name the server may put in a preflight result. Measured, not read: the corpus
#: below drives the API and the test fails when it emits a name missing from this set, so a
#: new check lands here and in the TypeScript declaration together.
_SERVER_CHECK_NAMES = frozenset(
    {
        "public_host_conflict",
        "target_reachable",
        "https_port_hint",
        "tunnel_provider",
        "tunnel_health",
        "provider_target_required",
        "proxy_provider",
        "dns_provider",
        "proxy_connection",
        "dns_connection",
        "dns_target_resolution",
        "extra_proxy_provider",
        "extra_dns_provider",
    }
)

#: The `(check name, detail_key)` branches allowed to carry `blocking: True`, because both
#: save routes really refuse the bodies that fail them: 409 on a hostname already served, 400
#: with no provider target at all, 400 with a DNS provider and no address to write, and 400
#: through `_unknown_references` on a provider id -- primary or extra -- pointing at nothing.
#:
#: The pair, not the name. `provider_missing` and `provider_disabled` come out of
#: `_check_provider` under the same two names, and only the first is a refusal; keyed on the
#: name alone this list could not tell them apart, and the name it would have had to carry
#: for `provider_missing` would have licensed `provider_disabled` to block too. The ok
#: branches of the gates are here as well (`host_free`, `target_set`, `dns_resolved`): they
#: carry the flag, so a gate that stops being one is a line that disappears from this set.
_MAY_BLOCK = frozenset(
    {
        ("public_host_conflict", "host_free"),
        ("public_host_conflict", "host_taken"),
        ("provider_target_required", "target_set"),
        ("provider_target_required", "target_none"),
        ("dns_target_resolution", "dns_resolved"),
        ("dns_target_resolution", "dns_target_required"),
        ("dns_target_resolution", "dns_target_detection_failed"),
        ("proxy_provider", "provider_missing"),
        ("dns_provider", "provider_missing"),
        ("extra_proxy_provider", "provider_missing"),
        ("extra_dns_provider", "provider_missing"),
    }
)

#: Failure codes the corpus has to reach for the rule above to mean anything. A scenario
#: deleted or defanged shows up here rather than as a suite that silently stops looking.
_MUST_EXERCISE = frozenset(
    {
        "provider_disabled",
        "tunnel_status",
        "tunnel_unreachable",
        "tunnel_check_failed",
        "target_unreachable",
        "host_taken",
        "target_none",
        "provider_missing",
        "dns_target_required",
        "dns_target_detection_failed",
    }
)


def _nothing():
    """A context manager that does nothing, so a corpus case reads the same either way."""
    return nullcontext()


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _FakeProvider:
    """Reachable or not, and it accepts every write either way.

    A provider can answer its probe and still be the wrong one, and it can be unreachable
    from Vauxtra and perfectly reachable from the proxy, so the probe and the writes are
    separate knobs. The writes recording what they received is the whole point: the question
    these tests ask is not what the API answered but what the provider ended up holding.
    """

    def __init__(self, *, answers: bool = True):
        self.answers = answers
        self.hosts: list[dict] = []
        self.rewrites: list[dict] = []

    def test_connection(self) -> bool:
        return self.answers

    def find_best_certificate(self, _domain):
        return None

    def list_hosts(self):
        return self.hosts

    def create_host(self, domain, ip, port, scheme, websocket, cert_id):
        host = {"id": 100 + len(self.hosts), "domains": [domain], "host": ip, "port": port}
        self.hosts.append(host)
        return host

    def update_host(self, host_id, domain, ip, port, scheme, websocket, cert_id):
        self.hosts.append({"id": host_id, "domains": [domain], "host": ip, "port": port})
        return True

    def list_rewrites(self):
        return self.rewrites

    def add_rewrite(self, domain, ip):
        self.rewrites.append({"domain": domain, "answer": ip})
        return True

    def holds(self, host: str) -> bool:
        host = host.lower()
        return any(host in [str(d).lower() for d in h.get("domains", [])] for h in self.hosts) or any(
            str(r.get("domain", "")).lower() == host for r in self.rewrites
        )


class _FakeTunnel(_FakeProvider):
    """A tunnel provider: the preflight prefers `health_status()` when the class has one."""

    def __init__(self, *, health: dict | None = None, raises: bool = False):
        super().__init__()
        self._health = health or {}
        self._raises = raises

    def health_status(self) -> dict:
        if self._raises:
            raise RuntimeError("health endpoint answered 404")
        return self._health


class _SymmetryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "symmetry.test.db")
        models.init_db()

        self.providers: dict[int, _FakeProvider] = {}
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda row: self.providers[row["id"]]),
            patch.object(sync_api, "create_provider", lambda row: self.providers[row["id"]]),
            patch.object(
                services_api,
                "_service_target_reachable",
                lambda _h, _p, timeout=2.0: (True, "Reachable in 3.0 ms"),
            ),
            # Auto mode reaches out to the WAN resolvers. Pinned to "nothing detected" so
            # the cases that run in auto mode measure the code and not the test machine's
            # internet connection.
            patch("app.public_target.detect_server_public_ip", lambda **kwargs: ""),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        import app.db as _app_db

        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- fixtures -------------------------------------------------------------------------
    def _add_provider(self, pid, name, ptype, provider=None, *, enabled=True) -> _FakeProvider:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?,?,?,'http://192.168.1.10','u','p','{}',?)""",
            (pid, name, ptype, int(enabled)),
        )
        conn.commit()
        conn.close()
        self.providers[pid] = provider or _FakeProvider()
        return self.providers[pid]

    def _fields(self, **overrides) -> dict:
        fields = {
            "subdomain": "vault",
            "domain": "example.com",
            "target_ip": "192.168.1.9",
            "target_port": 8080,
            "forward_scheme": "http",
            "dns_ip": "198.51.100.7",
        }
        fields.update(overrides)
        return fields

    def _preflight(self, service_id: int | None = None, **overrides) -> dict:
        """`service_id` is what the panel sends while editing, and it is not decoration.

        The save route it speaks for resolves the public target from the row it is about to
        overwrite; a preflight that always resolved from nothing answered for the creation
        route and guessed for the other one.
        """
        return services_api.preflight_service(
            _request("POST", "/api/services/preflight"),
            services_api.ServicePreflightIn(**self._fields(**overrides), service_id=service_id),
        )

    def _save(self, **overrides) -> tuple[int, dict]:
        response = services_api.add_service(
            _request("POST", "/api/services"), services_api.ServiceIn(**self._fields(**overrides))
        )
        return response.status_code, json.loads(bytes(response.body))

    def _save_refusal(self, **overrides) -> int | None:
        """The status POST /api/services refuses this body with, or None if it accepts it.

        201 and 207 are both acceptance: the row exists and the providers were addressed.
        """
        try:
            self._save(**overrides)
        except HTTPException as e:
            return e.status_code
        return None

    def _seed_service(self, dns_ip: str = "203.0.113.9", public_target_mode: str = "manual") -> int:
        """A service that already exists, for the update route to be pointed at.

        Inserted rather than saved: the corpus case owns the body under test, and half of
        those cases are bodies no save route accepts, so going through `add_service` for the
        row to edit would leave them with nothing to edit. It holds a public target of its
        own -- the address the update route used to fall back on and answer 200 with, and a
        different one from the body's, so a fallback shows up as the wrong value and not
        merely as a passing test. It sits on a hostname of its own, so a case about a
        hostname another service serves still has one to collide with.
        """
        conn = models.get_db()
        cursor = conn.execute(
            """INSERT INTO services (subdomain, domain, target_ip, target_port, forward_scheme,
                                     expose_mode, public_target_mode, dns_ip, enabled)
               VALUES ('seed','example.com','192.168.1.9',8080,'http','proxy_dns',?,?,1)""",
            (public_target_mode, dns_ip),
        )
        sid = int(cursor.lastrowid)
        conn.commit()
        conn.close()
        return sid

    def _update(self, sid: int, **overrides) -> dict:
        return services_api.update_service(
            sid,
            _request("PUT", f"/api/services/{sid}"),
            services_api.ServiceIn(**self._fields(**overrides)),
        )

    def _update_refusal(self, sid: int, **overrides) -> int | None:
        """The status PUT /api/services/{sid} refuses this body with, or None if it accepts."""
        try:
            self._update(sid, **overrides)
        except HTTPException as e:
            return e.status_code
        return None

    def _set_public_target_priority(self, priority: str) -> None:
        """The operator's own ranking of target sources, where the policy loader reads it."""
        conn = models.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('public_target_priority', ?)",
            (priority,),
        )
        conn.commit()
        conn.close()

    def _dead_target(self):
        return patch.object(
            services_api,
            "_service_target_reachable",
            lambda _h, _p, timeout=2.0: (False, "timed out"),
        )

    def _push_targets(self, sid: int) -> set[tuple[int, str]]:
        conn = models.get_db()
        rows = {
            (int(r["provider_id"]), r["role"])
            for r in conn.execute(
                "SELECT provider_id, role FROM service_push_targets WHERE service_id=?", (sid,)
            )
        }
        conn.close()
        return rows

    def _named(self, result: dict, name: str) -> list[dict]:
        return [c for c in result["checks"] if c["name"] == name]

    def _stored(self, sid: int) -> dict:
        """The row as it now stands. A refusal that changed something is not a refusal."""
        conn = models.get_db()
        row = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        conn.close()
        return dict(row)


# -- the corpus ---------------------------------------------------------------------------
# One entry per configuration the preflight has an opinion about. `setup` populates the
# providers and returns the body; both routes then receive that same body.


def _case_disabled_proxy(t):
    t._add_provider(1, "NPM", "npm", enabled=False)
    return {"proxy_provider_id": 1}


def _case_disabled_dns(t):
    t._add_provider(1, "NPM", "npm")
    t._add_provider(2, "AdGuard", "adguard", enabled=False)
    return {"proxy_provider_id": 1, "dns_provider_id": 2}


def _case_tunnel_reports_down(t):
    t._add_provider(2, "CF Tunnel", "cloudflare_tunnel", _FakeTunnel(health={"ok": False, "status": "down"}))
    return {
        "expose_mode": "tunnel",
        "tunnel_provider_id": 2,
        "tunnel_hostname": "vault.example.com",
    }


def _case_tunnel_unreachable(t):
    t._add_provider(2, "Traefik", "traefik", _FakeProvider(answers=False))
    return {
        "expose_mode": "tunnel",
        "tunnel_provider_id": 2,
        "tunnel_hostname": "vault.example.com",
    }


def _case_tunnel_check_raises(t):
    t._add_provider(2, "CF Tunnel", "cloudflare_tunnel", _FakeTunnel(raises=True))
    return {
        "expose_mode": "tunnel",
        "tunnel_provider_id": 2,
        "tunnel_hostname": "vault.example.com",
    }


def _case_proxy_that_does_not_answer(t):
    t._add_provider(1, "NPM", "npm", _FakeProvider(answers=False))
    return {"proxy_provider_id": 1}


def _case_hostname_already_served(t):
    t._add_provider(1, "NPM", "npm")
    t._save(proxy_provider_id=1)
    return {"proxy_provider_id": 1}


def _case_no_target_at_all(t):
    t._add_provider(1, "NPM", "npm")
    return {"proxy_provider_id": None, "dns_provider_id": None}


def _case_dns_without_an_address(t):
    t._add_provider(1, "NPM", "npm")
    t._add_provider(2, "AdGuard", "adguard")
    return {"proxy_provider_id": 1, "dns_provider_id": 2, "dns_ip": ""}


def _case_primary_proxy_points_at_nothing(t):
    """The branch that was in neither doctrine list: `provider_missing` on a primary.

    `_check_provider` blocks on it and is right to -- `_unknown_references` answers 400 on
    the same body -- but the check name it comes out under, `proxy_provider`, also carries
    `provider_disabled`, which is a warning. The two were listed together or not at all, and
    they were not at all.
    """
    t._add_provider(2, "AdGuard", "adguard")
    return {"proxy_provider_id": 404, "dns_provider_id": 2}


def _case_primary_dns_points_at_nothing(t):
    t._add_provider(1, "NPM", "npm")
    return {"proxy_provider_id": 1, "dns_provider_id": 404}


def _case_detection_found_nothing(t):
    """Auto mode, no WAN address, and a policy that does not fall back on the current one.

    The second failure shape of `dns_target_resolution`: `auto_unavailable` rather than an
    empty field. It has its own sentence because it has its own remedy, and both routes
    refuse it with 400.
    """
    t._add_provider(2, "AdGuard", "adguard")
    t._set_public_target_priority("server_public_ip")
    return {
        "proxy_provider_id": None,
        "dns_provider_id": 2,
        "public_target_mode": "auto",
        "dns_ip": "",
    }


def _case_extra_points_at_nothing(t):
    t._add_provider(1, "NPM", "npm")
    return {"proxy_provider_id": 1, "extra_proxy_provider_ids": [404]}


def _case_extra_dns_points_at_nothing(t):
    t._add_provider(1, "NPM", "npm")
    t._add_provider(2, "AdGuard", "adguard")
    return {"proxy_provider_id": 1, "dns_provider_id": 2, "extra_dns_provider_ids": [404]}


def _case_target_that_does_not_answer(t):
    t._add_provider(1, "NPM", "npm")
    return {"proxy_provider_id": 1}


def _case_everything_healthy(t):
    t._add_provider(1, "NPM", "npm")
    t._add_provider(2, "AdGuard", "adguard")
    t._add_provider(3, "NPM bis", "npm")
    return {
        "proxy_provider_id": 1,
        "dns_provider_id": 2,
        "extra_proxy_provider_ids": [3],
    }


def _case_healthy_tunnel(t):
    t._add_provider(2, "CF Tunnel", "cloudflare_tunnel", _FakeTunnel(health={"ok": True, "status": "healthy"}))
    return {
        "expose_mode": "tunnel",
        "tunnel_provider_id": 2,
        "tunnel_hostname": "vault.example.com",
    }


#: (label, setup, whether the save routes refuse the body, whether the target is dead).
#: Every entry is driven through each route in `_ROUTES`, so one line here is one scenario
#: held against the whole of the doctrine rather than against half of it.
_CORPUS = (
    ("a disabled primary proxy", _case_disabled_proxy, False, False),
    ("a disabled primary DNS server", _case_disabled_dns, False, False),
    ("a tunnel reporting ok: false", _case_tunnel_reports_down, False, False),
    ("a tunnel whose connection test fails", _case_tunnel_unreachable, False, False),
    ("a tunnel whose health endpoint raises", _case_tunnel_check_raises, False, False),
    ("a proxy that does not answer", _case_proxy_that_does_not_answer, False, False),
    ("a target that refuses the socket", _case_target_that_does_not_answer, False, True),
    ("a hostname another service serves", _case_hostname_already_served, True, False),
    ("no provider target at all", _case_no_target_at_all, True, False),
    ("a DNS provider with no address to write", _case_dns_without_an_address, True, False),
    ("automatic detection that found nothing", _case_detection_found_nothing, True, False),
    ("a primary proxy id pointing at nothing", _case_primary_proxy_points_at_nothing, True, False),
    ("a primary DNS id pointing at nothing", _case_primary_dns_points_at_nothing, True, False),
    ("an extra proxy id pointing at nothing", _case_extra_points_at_nothing, True, False),
    ("an extra DNS id pointing at nothing", _case_extra_dns_points_at_nothing, True, False),
    ("a healthy proxy, DNS server and spare proxy", _case_everything_healthy, False, False),
    ("a healthy tunnel", _case_healthy_tunnel, False, False),
)


def _via_create(t, _body):
    """POST /api/services. Nothing exists yet, so the preflight that speaks for it names no
    service, and that is exactly the `current_value=""` the route resolves from."""
    return None, t._save_refusal


def _via_update(t, _body):
    """PUT /api/services/{sid}. The row being edited is what the route resolves from, so it
    is what the preflight speaking for it has to be told about."""
    sid = t._seed_service()
    return sid, lambda **body: t._update_refusal(sid, **body)


#: The routes a `blocking` badge is a claim about. The rule below is run once per route per
#: corpus case: a route added here is measured against every scenario, and a scenario added
#: above is measured against every route, without either list knowing about the other.
_ROUTES = (
    ("POST /api/services", _via_create),
    ("PUT /api/services/{sid}", _via_update),
)


class TheBlockingBadgeIsAClaimAboutEverySaveRouteTests(_SymmetryTestCase):
    """One rule over the whole corpus times every route, so nothing slips past by half.

    The corpus used to be driven through `add_service` alone. A symmetry test that only ever
    drives one of the two routes cannot see an asymmetry between them, and did not: three
    branches blocked the creation route and waved the update route through.
    """

    def test_a_blocking_failure_means_every_save_route_refuses_the_same_body(self):
        exercised: set[str] = set()
        may_block: set[tuple[str, str]] = set()
        emitted: set[str] = set()
        verdicts: list[str] = []

        for label, setup, expected_refusal, dead_target in _CORPUS:
            for route_label, drive in _ROUTES:
                with self.subTest(case=label, route=route_label):
                    # Each case owns a clean database: one of them saves a service to occupy
                    # the hostname, and every case then runs its route on its own body.
                    self.tearDown()
                    self.setUp()

                    body = setup(self)
                    service_id, save = drive(self, body)
                    socket_probe = self._dead_target() if dead_target else _nothing()
                    with socket_probe:
                        result = self._preflight(service_id=service_id, **body)
                        emitted.update(c["name"] for c in result["checks"])
                        may_block.update(
                            (c["name"], c["detail_key"]) for c in result["checks"] if c["blocking"]
                        )
                        exercised.update(
                            c.get("detail_key", "") for c in result["checks"] if not c["ok"]
                        )

                        blocked = [
                            c["name"] for c in result["checks"] if c["blocking"] and not c["ok"]
                        ]
                        refusal = save(**body)
                    verdicts.append(
                        f"{label} via {route_label}: blocking={blocked} "
                        f"save={refusal or 'accepted'}"
                    )

                    self.assertEqual(
                        bool(blocked),
                        refusal is not None,
                        f"{label}: preflight blocked on {blocked} and {route_label} "
                        f"answered {refusal or 'with a saved service'}",
                    )
                    self.assertEqual(refusal is not None, expected_refusal, f"{label} via {route_label}")
                    self.assertEqual(result["ok"], refusal is None, f"{label} via {route_label}")

        self.assertTrue(
            _MUST_EXERCISE.issubset(exercised),
            f"the corpus stopped reaching {sorted(_MUST_EXERCISE - exercised)}\n"
            + "\n".join(verdicts),
        )
        self.assertEqual(
            may_block,
            set(_MAY_BLOCK),
            "a branch started or stopped claiming a save route would refuse it\n"
            + "\n".join(verdicts),
        )
        self.assertTrue(
            emitted.issubset(_SERVER_CHECK_NAMES),
            f"undeclared check names: {sorted(emitted - _SERVER_CHECK_NAMES)}",
        )

    def test_every_corpus_case_is_measured_against_every_save_route(self):
        """The witness for the loop above: a route silently dropped would still pass it.

        `_ROUTES` holding one entry, or a case skipped for one of them, leaves the rule
        satisfied and the asymmetry it exists to catch invisible again.
        """
        driven: set[tuple[str, str]] = set()

        for label, setup, _refuses, _dead in _CORPUS:
            for route_label, drive in _ROUTES:
                self.tearDown()
                self.setUp()
                body = setup(self)
                service_id, _save = drive(self, body)
                driven.add((label, route_label))
                self.assertEqual(
                    service_id is None,
                    route_label == "POST /api/services",
                    f"{route_label} named the wrong service to the preflight",
                )

        self.assertEqual(len(_ROUTES), 2, "a save route was added or dropped without the rule")
        self.assertEqual(
            driven,
            {(label, route) for label, _s, _r, _d in _CORPUS for route, _d2 in _ROUTES},
        )

    def test_a_dead_target_and_a_disabled_proxy_are_warnings_side_by_side(self):
        """The two halves of the same class, in one result, as the panel would show them."""
        self._add_provider(1, "NPM", "npm", enabled=False)

        with self._dead_target():
            result = self._preflight(proxy_provider_id=1)
            status, payload = self._save(proxy_provider_id=1)

        self.assertEqual(
            [(c["name"], c["blocking"]) for c in result["checks"] if not c["ok"]],
            [("target_reachable", False), ("proxy_provider", False)],
        )
        self.assertEqual(result["summary"]["blocking_failures"], 0)
        self.assertEqual(result["summary"]["warnings"], 2)
        self.assertTrue(result["ok"])
        self.assertEqual(status, 201)
        self.assertEqual(payload["errors"], [])
        self.assertTrue(
            self.providers[1].holds("vault.example.com"),
            "the disabled provider was told to publish; the badge said the save would refuse",
        )

    def test_a_healthy_configuration_still_passes_every_check(self):
        """The witness. A preflight that stopped blocking anything would satisfy the rule."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")

        result = self._preflight(proxy_provider_id=1, dns_provider_id=2)

        self.assertEqual([c["name"] for c in result["checks"] if not c["ok"]], [])
        self.assertEqual(result["summary"], {"blocking_failures": 0, "warnings": 0, "total": 8})
        self.assertTrue(result["ok"])
        self.assertEqual(self._save(proxy_provider_id=1, dns_provider_id=2)[0], 201)

    def test_a_primary_provider_that_no_longer_exists_keeps_its_badge_on_both_routes(self):
        """The blocking branch that sat in no doctrine list.

        `_check_provider` returns `provider_missing` with `blocking: True` for a primary id
        pointing at nothing, and it is right to: `_unknown_references` answers 400 on the
        same body through both save routes. It appeared in neither list in `services.py`, and
        `_MAY_BLOCK` -- keyed on the check name back then -- could not have held it without
        licensing `provider_disabled`, which comes out under that same name and is a warning.
        """
        self._add_provider(1, "NPM", "npm")
        sid = self._seed_service()
        body = {"proxy_provider_id": 1, "dns_provider_id": 404}

        result = self._preflight(service_id=sid, **body)
        check = self._named(result, "dns_provider")[0]

        self.assertEqual(
            (check["ok"], check["blocking"], check["detail_key"]),
            (False, True, "provider_missing"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(self._save_refusal(**body), 400)
        self.assertEqual(self._update_refusal(sid, **body), 400)

    def test_a_disabled_primary_provider_shares_that_name_and_is_still_no_gate(self):
        """The witness that keeps the doctrine keyed on the branch rather than on the name.

        `provider_disabled` and `provider_missing` are two branches of one check name. A list
        that named `dns_provider` to admit the second would have licensed the first, and the
        first was measured answering 201 with the DNS rewrite written.
        """
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard", enabled=False)

        result = self._preflight(proxy_provider_id=1, dns_provider_id=2)
        check = self._named(result, "dns_provider")[0]

        self.assertEqual(
            (check["ok"], check["blocking"], check["detail_key"]),
            (False, False, "provider_disabled"),
        )
        self.assertEqual(self._save(proxy_provider_id=1, dns_provider_id=2)[0], 201)
        self.assertTrue(
            self.providers[2].holds("vault.example.com"),
            "the disabled DNS server was written to; the badge would have said it refuses",
        )

    def test_a_hostname_already_served_keeps_its_badge(self):
        """The other witness: demoting one check must not disarm the gates that are earned."""
        self._add_provider(1, "NPM", "npm")
        self._save(proxy_provider_id=1)

        result = self._preflight(proxy_provider_id=1)
        check = self._named(result, "public_host_conflict")[0]

        self.assertFalse(check["ok"])
        self.assertTrue(check["blocking"])
        self.assertFalse(result["ok"])
        self.assertEqual(self._save_refusal(proxy_provider_id=1), 409)


class TheUpdateRouteRefusesWhatTheCreationRouteRefusesTests(_SymmetryTestCase):
    """`docs/HOWTO.md` promises "same 400 / 409 as the creation" for PUT /api/services/{sid}.

    The doc was right and the route was not. The corpus above now measures the pair over
    every scenario at once; what is held here is the one edit that cost the promise, in the
    values an operator would have seen.
    """

    def test_an_edit_that_empties_the_address_does_not_resurrect_the_stored_one(self):
        """The service holds `203.0.113.9`, the operator clears the field, and 200 came back.

        A blanket `dns_ip = old["dns_ip"]` stood in front of the refusal to absorb a
        detection blip, and fired in manual mode, where nothing is detected. The row came
        back holding the address that had just been deleted, on a body the preflight had
        marked `blocking_failures: 1` and POST refuses with 400.
        """
        self._add_provider(2, "AdGuard", "adguard")
        sid = self._seed_service(dns_ip="203.0.113.9")
        body = {"proxy_provider_id": None, "dns_provider_id": 2, "dns_ip": ""}

        result = self._preflight(service_id=sid, **body)
        check = self._named(result, "dns_target_resolution")[0]
        self.assertEqual(
            (check["ok"], check["blocking"], check["detail_key"]),
            (False, True, "dns_target_required"),
        )
        self.assertEqual(result["summary"]["blocking_failures"], 1)
        self.assertFalse(result["ok"])

        with self.assertRaises(HTTPException) as refused:
            self._update(sid, **body)

        self.assertEqual(refused.exception.status_code, 400)
        self.assertEqual(refused.exception.detail["detail_key"], "dns_target_required")
        self.assertEqual(self._save_refusal(**body), 400, "the two routes answer the same")
        self.assertEqual(self.providers[2].rewrites, [], "a refusal published nothing")
        self.assertEqual(
            (self._stored(sid)["subdomain"], self._stored(sid)["dns_ip"]),
            ("seed", "203.0.113.9"),
            "nothing was changed, hostname included",
        )

    def test_an_edit_down_to_no_provider_target_at_all_is_refused(self):
        """The guard `add_service` opens with, and `update_service` did not have.

        Measured before: POST 400, PUT `200 {"errors": []}` -- with the hostname moved onto
        the edited row and no provider anywhere left to serve it, while the preflight had
        `target_none` blocking and the panel's button greyed out.
        """
        sid = self._seed_service()
        body = {"proxy_provider_id": None, "dns_provider_id": None}

        result = self._preflight(service_id=sid, **body)
        check = self._named(result, "provider_target_required")[0]
        self.assertEqual(
            (check["ok"], check["blocking"], check["detail_key"]),
            (False, True, "target_none"),
        )

        self.assertEqual(self._update_refusal(sid, **body), 400)
        self.assertEqual(self._save_refusal(**body), 400)
        self.assertEqual(self._stored(sid)["subdomain"], "seed", "the hostname did not move")

    def test_a_preflight_for_an_edit_resolves_from_the_row_that_edit_resolves_from(self):
        """The same drift running the other way: a red badge over a PUT that answers 200.

        Auto mode, nothing detectable, and a service already holding `203.0.113.9`.
        `update_service` resolves through `current_value=old["dns_ip"]` and keeps that
        target; the preflight resolved from nothing whatever it was asked about, reported
        `dns_target_detection_failed` and greyed out a save that works. `service_id` is what
        tells the two calls apart, and they are the same call now.
        """
        self._add_provider(2, "AdGuard", "adguard")
        sid = self._seed_service(dns_ip="203.0.113.9", public_target_mode="auto")
        body = {
            "proxy_provider_id": None,
            "dns_provider_id": 2,
            "public_target_mode": "auto",
            "dns_ip": "",
        }

        result = self._preflight(service_id=sid, **body)
        check = self._named(result, "dns_target_resolution")[0]

        self.assertTrue(check["ok"], check["detail"])
        self.assertEqual(check["data"], {"resolved_target": "203.0.113.9", "source": "current"})
        self.assertEqual(result["summary"]["blocking_failures"], 0)
        self.assertIsNone(self._update_refusal(sid, **body))
        self.assertEqual(
            self.providers[2].rewrites,
            [{"domain": "vault.example.com", "answer": "203.0.113.9"}],
        )

        # The witness: a creation has no row to resolve from, so the same body still blocks
        # there. A preflight that had simply stopped looking would satisfy the assertions
        # above.
        self.assertFalse(self._preflight(**body)["ok"])
        self.assertEqual(self._save_refusal(subdomain="other", **body), 400)


class TheExtrasTheSaveRouteRecordsAreTheExtrasThePreflightNamesTests(_SymmetryTestCase):
    """`_primary_push_targets` answers both, so neither can call an id an extra alone."""

    def _extras_named_by_preflight(self, result: dict) -> set[tuple[int, str]]:
        by_name = {p["name"]: pid for pid, p in self._provider_names().items()}
        found = set()
        for check in result["checks"]:
            if check["name"] not in ("extra_proxy_provider", "extra_dns_provider"):
                continue
            role = "proxy" if check["name"] == "extra_proxy_provider" else "dns"
            params = check.get("detail_params") or {}
            pid = params.get("id") or by_name[params["name"]]
            found.add((int(pid), role))
        return found

    def _provider_names(self) -> dict[int, dict]:
        conn = models.get_db()
        rows = {int(r["id"]): {"name": r["name"]} for r in conn.execute("SELECT id, name FROM providers")}
        conn.close()
        return rows

    def _assert_agree(self, **body) -> dict:
        result = self._preflight(**body)
        _, payload = self._save(**body)
        self.assertEqual(
            self._extras_named_by_preflight(result),
            self._push_targets(payload["id"]),
            "the preflight and the save route disagree about which ids are extra targets",
        )
        return result

    def test_a_tunnel_id_carried_by_a_proxy_dns_body_does_not_hide_the_extra(self):
        """The measured case: the id is not the primary of a mode the body is not in.

        `add_service` stores `tunnel_provider_id` only in tunnel mode, so in proxy_dns mode
        provider 5 is a plain extra proxy: it receives the route and gets a
        `service_push_targets` row. The preflight used to compare against
        `proxy_provider_id or tunnel_provider_id` whatever the mode and drew no line at all.
        """
        self._add_provider(5, "NPM-DR", "npm")

        result = self._assert_agree(
            proxy_provider_id=None, tunnel_provider_id=5, extra_proxy_provider_ids=[5]
        )

        self.assertEqual(len(self._named(result, "extra_proxy_provider")), 1)
        self.assertTrue(self.providers[5].holds("vault.example.com"))

    def test_a_dns_id_carried_by_a_tunnel_body_does_not_hide_the_extra(self):
        """The mirror case, and it fails the other way round: the DNS column is blanked too."""
        self._add_provider(2, "CF Tunnel", "cloudflare_tunnel", _FakeTunnel(health={"ok": True}))
        self._add_provider(6, "AdGuard", "adguard", _FakeProvider(answers=False))

        result = self._assert_agree(
            expose_mode="tunnel",
            tunnel_provider_id=2,
            tunnel_hostname="vault.example.com",
            dns_provider_id=6,
            extra_dns_provider_ids=[6],
        )

        check = self._named(result, "extra_dns_provider")[0]
        self.assertFalse(check["ok"])
        self.assertEqual(check["detail_key"], "extra_provider_failed")

    def test_a_primary_repeated_as_an_extra_is_still_dropped_once(self):
        """The witness: the de-duplication itself must survive being made mode-aware."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")

        result = self._assert_agree(
            proxy_provider_id=1,
            dns_provider_id=2,
            extra_proxy_provider_ids=[1],
            extra_dns_provider_ids=[2],
        )

        self.assertEqual(self._named(result, "extra_proxy_provider"), [])
        self.assertEqual(self._named(result, "extra_dns_provider"), [])
        self.assertEqual(result["summary"]["warnings"], 0)

    def test_a_genuine_extra_is_named_on_both_sides(self):
        """The other witness: agreeing on an empty set would satisfy the two tests above."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")

        result = self._assert_agree(
            proxy_provider_id=1, dns_provider_id=2, extra_dns_provider_ids=[3]
        )

        self.assertEqual(len(self._named(result, "extra_dns_provider")), 1)
        self.assertTrue(self.providers[3].holds("vault.example.com"))


class TheTypeScriptDeclarationListsTheNamesTheServerEmitsTests(_SymmetryTestCase):
    """`PreflightCheck` is what the panel is built from; a name missing from it is invisible.

    `checkLabel` falls back to printing the raw name, so an undeclared check reaches the
    operator as `extra_dns_provider` in the list. Read from the file rather than trusted:
    the names on the left of this comparison come out of the running API.
    """

    def _declared_names(self) -> set[str]:
        """The sentence listing the names, and only it: the prose under it also uses ticks."""
        source = _API_TYPES.read_text(encoding="utf-8")
        sentence = re.search(r"Known names:(.*?)(?:\n\s*\*\s*\n|\*/)", source, re.S)
        self.assertIsNotNone(sentence, "the `Known names` sentence is gone from api.ts")
        return set(re.findall(r"`([a-z_]+)`", sentence.group(1)))

    def _emit_every_check_name(self) -> set[str]:
        names: set[str] = set()

        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")
        self._add_provider(3, "Technitium", "technitium")
        self._add_provider(4, "NPM bis", "npm")
        self._add_provider(5, "CF Tunnel", "cloudflare_tunnel", _FakeTunnel(health={"ok": True}))

        names.update(
            c["name"]
            for c in self._preflight(
                proxy_provider_id=1,
                dns_provider_id=2,
                extra_dns_provider_ids=[3],
                extra_proxy_provider_ids=[4],
                forward_scheme="https",
                target_port=80,
            )["checks"]
        )
        names.update(
            c["name"]
            for c in self._preflight(
                expose_mode="tunnel", tunnel_provider_id=5, tunnel_hostname="vault.example.com"
            )["checks"]
        )
        names.update(
            c["name"]
            for c in self._preflight(proxy_provider_id=None, extra_proxy_provider_ids=[404])["checks"]
        )
        return names

    def test_every_name_the_api_emits_is_declared_in_api_ts(self):
        emitted = self._emit_every_check_name()

        self.assertEqual(emitted, set(_SERVER_CHECK_NAMES), "the corpus stopped covering a check")
        self.assertEqual(
            sorted(emitted - self._declared_names()),
            [],
            "the panel's type declaration does not know these check names",
        )

    def test_the_declaration_does_not_list_names_the_api_never_emits(self):
        """The witness: a list padded with every plausible word would pass the test above."""
        declared = self._declared_names()

        self.assertTrue(declared, "no names were parsed out of the comment")
        self.assertEqual(sorted(declared - set(_SERVER_CHECK_NAMES)), [])


class TheCheckListIsKeyedOnMoreThanTheNameTests(unittest.TestCase):
    """Two extra proxies produce two list items called `extra_proxy_provider`.

    Measured on React 19.2.8: both <li> render, and nothing vanishes from the panel. What a
    shared key costs is identity, which is the only thing a key is for. React logs
    "Encountered two children with the same key", warns that such children "may be duplicated
    and/or omitted", and calls the behaviour unsupported and liable to change. Measured on the
    same two lines: swapping them left each row's state and DOM node on the other row, and a
    later change of keys left a stale third <li> standing for two lines of data. A panel whose
    one job is to say what the save will touch cannot show a row that belongs to another
    target. The two lines themselves are measured from the server side in
    `test_two_extra_proxies_produce_two_lines_of_one_name` below; what is checked here is that
    the component stopped keying on the name alone.
    """

    MODAL = _ROOT / "frontend" / "src" / "components" / "features" / "expose" / "ExposeModal.tsx"

    def test_the_preflight_list_key_is_not_the_check_name_alone(self):
        source = self.MODAL.read_text(encoding="utf-8")
        checks_map = re.search(r"preflight\.checks\.map\((.{0,4000}?)\n\s*\}\)\}", source, re.S)
        self.assertIsNotNone(checks_map, "the preflight check list is no longer a .map()")
        body = checks_map.group(1)

        self.assertNotIn("key={check.name}", body, "duplicate React keys: one per extra target")
        key = re.search(r"<li\s+key=\{(.+?)\}\s", body)
        self.assertIsNotNone(key, "the list items lost their key")
        self.assertIn("index", key.group(1), f"the key is still name-only: {key.group(1)}")


class TwoExtraTargetsReallyProduceTwoLinesTests(_SymmetryTestCase):
    def test_two_extra_proxies_produce_two_lines_of_one_name(self):
        """The measurement the key change rests on: the name is not unique in a result."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "NPM bis", "npm")
        self._add_provider(3, "NPM ter", "npm")

        result = self._preflight(proxy_provider_id=1, extra_proxy_provider_ids=[2, 3])
        lines = self._named(result, "extra_proxy_provider")

        self.assertEqual(len(lines), 2)
        self.assertEqual(
            [c["detail_params"]["name"] for c in lines],
            ["NPM bis", "NPM ter"],
        )


if __name__ == "__main__":
    unittest.main()
