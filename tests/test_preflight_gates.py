"""What the preflight may forbid, and what it may only warn about.

A check marked `blocking` greys out "Create route": the Expose panel disables the button
while `summary.blocking_failures` is above zero, and it offers nothing to press instead. So
`blocking` is a promise about the save route, and it says the save would be refused too.

`target_reachable` made that promise and never held it. `add_service` opens no socket to the
target -- `_service_target_reachable` has exactly one caller, and it is the preflight -- so
the configuration the panel refused to submit is one the API writes to the proxy and to the
DNS server without a word. A Vauxtra that cannot see the target's VLAN while the reverse
proxy can, a firewall that only opens for the proxy, a backend still booting, a name only
the proxy's Docker network resolves: each of those was a dead end with a red badge and no
way past it.

The second hole is the opposite shape. The preflight checked one provider out of N: the
extra targets fed a "at least one target is set" boolean and nothing else, and the primary
DNS provider was never dialled at all, only read out of the database. Those live in
`test_multi_sync_targets.py`, next to the rest of the multi-target subject; what this file
holds is the rule that was missing: a preflight check blocks only what POST /api/services
refuses, and everything else is a warning the operator may save through.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import services as services_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _FakeProvider:
    """Answers the preflight's probe, and accepts the writes the save route makes.

    `answers` is the probe alone: a provider can be perfectly reachable and still be the
    wrong one, so the writes succeed either way unless a test says otherwise.
    """

    def __init__(self, *, answers: bool = True):
        self.answers = answers
        self.hosts: list[dict] = []
        self.rewrites: list[dict] = []

    def test_connection(self) -> bool:
        return self.answers

    # -- proxy ----------------------------------------------------------------------------
    def find_best_certificate(self, _domain):
        return None

    def list_hosts(self):
        return self.hosts

    def create_host(self, domain, ip, port, scheme, websocket, cert_id):
        host = {"id": 101, "domains": [domain], "host": ip, "port": port}
        self.hosts.append(host)
        return host

    # -- dns ------------------------------------------------------------------------------
    def list_rewrites(self):
        return self.rewrites

    def add_rewrite(self, domain, ip):
        self.rewrites.append({"domain": domain, "answer": ip})
        return True


class _PreflightTestCase(unittest.TestCase):
    PUBLIC_IP = "198.51.100.7"

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "preflight.test.db")
        models.init_db()

        self.providers: dict[int, _FakeProvider] = {}
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda row: self.providers[row["id"]]),
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
    def _add_provider(self, pid: int, name: str, ptype: str, *, answers: bool = True) -> None:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?,?,?,'http://x','u','p','{}',1)""",
            (pid, name, ptype),
        )
        conn.commit()
        conn.close()
        self.providers[pid] = _FakeProvider(answers=answers)

    def _fields(self, **overrides) -> dict:
        fields = {
            "subdomain": "vault",
            "domain": "example.com",
            "target_ip": "10.0.0.9",
            "target_port": 8080,
            "forward_scheme": "http",
            "dns_ip": self.PUBLIC_IP,
        }
        fields.update(overrides)
        return fields

    def _preflight(self, **overrides) -> dict:
        return services_api.preflight_service(
            _request("POST", "/api/services/preflight"),
            services_api.ServicePreflightIn(**self._fields(**overrides), service_id=None),
        )

    def _save(self, **overrides) -> tuple[int, dict]:
        response = services_api.add_service(
            _request("POST", "/api/services"), services_api.ServiceIn(**self._fields(**overrides))
        )
        return response.status_code, json.loads(bytes(response.body))

    def _check(self, result: dict, name: str) -> dict:
        matching = [c for c in result["checks"] if c["name"] == name]
        self.assertEqual(len(matching), 1, f"{name}: {[c['name'] for c in result['checks']]}")
        return matching[0]

    def _dead_target(self):
        return patch.object(
            services_api, "_service_target_reachable", lambda _h, _p, timeout=2.0: (False, "timed out")
        )

    def _live_target(self):
        return patch.object(
            services_api,
            "_service_target_reachable",
            lambda _h, _p, timeout=2.0: (True, "Reachable in 3.0 ms"),
        )


class ADeadTargetIsAWarningTests(_PreflightTestCase):
    """The gate the panel enforced, against the refusal the API never makes."""

    def test_a_target_that_does_not_answer_does_not_bar_the_route(self):
        self._add_provider(1, "NPM", "npm")

        with self._dead_target():
            result = self._preflight(proxy_provider_id=1)

        check = self._check(result, "target_reachable")
        self.assertFalse(check["ok"], "the socket was supposed to be refused")
        self.assertFalse(check["blocking"], "a dead target greyed out a button the API never needed")
        self.assertEqual(result["summary"]["blocking_failures"], 0)
        self.assertEqual(result["summary"]["warnings"], 1)
        self.assertTrue(result["ok"])

    def test_the_save_route_accepts_what_the_preflight_dislikes(self):
        """The rule itself, written down: the preflight blocks only what the save refuses.

        Same body through both routes. The preflight calls the target dead and the API
        answers 201 without ever asking, which is why that check cannot be a gate.
        """
        self._add_provider(1, "NPM", "npm")

        with self._dead_target():
            result = self._preflight(proxy_provider_id=1)
            status, body = self._save(proxy_provider_id=1)

        self.assertFalse(self._check(result, "target_reachable")["ok"])
        self.assertEqual(status, 201)
        self.assertEqual(body["errors"], [])

    def test_a_target_that_answers_still_passes(self):
        """The witness. A check that failed on everything would satisfy the test above."""
        self._add_provider(1, "NPM", "npm")

        with self._live_target():
            result = self._preflight(proxy_provider_id=1)

        check = self._check(result, "target_reachable")
        self.assertTrue(check["ok"])
        self.assertEqual(result["summary"]["warnings"], 0)
        self.assertEqual(result["summary"]["blocking_failures"], 0)

    def test_a_hostname_already_served_is_still_a_gate(self):
        """The other witness: making one check a warning must not disarm the rest.

        `add_service` really does answer 409 for a hostname another service holds, so that
        one has earned its red badge and keeps it.
        """
        self._add_provider(1, "NPM", "npm")
        with self._live_target():
            self._save(proxy_provider_id=1)
            result = self._preflight(proxy_provider_id=1)

        check = self._check(result, "public_host_conflict")
        self.assertFalse(check["ok"])
        self.assertTrue(check["blocking"])
        self.assertEqual(result["summary"]["blocking_failures"], 1)
        self.assertFalse(result["ok"])


class ThePrimaryDnsProviderIsDialledTests(_PreflightTestCase):
    """The proxy was probed and the DNS server was not, though both receive the route.

    `proxy_provider` and `dns_provider` only read a row out of the database, so a DNS server
    that had moved, lost its token or closed its port read "Ready: AdGuard" in green. The
    one thing that would have said otherwise, `test_connection()`, was asked of the proxy
    alone. A warning, not a gate: the save route publishes to it and reports the refusal
    afterwards, so the operator may go ahead knowing what is likely to come back.
    """

    def test_a_dns_provider_that_does_not_answer_is_named(self):
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard", answers=False)

        with self._live_target():
            result = self._preflight(proxy_provider_id=1, dns_provider_id=2)

        check = self._check(result, "dns_connection")
        self.assertFalse(check["ok"])
        self.assertFalse(check["blocking"])
        self.assertEqual(check["detail_key"], "dns_failed")
        self.assertEqual(check["detail_params"]["name"], "AdGuard")
        self.assertEqual(result["summary"]["warnings"], 1)
        self.assertEqual(result["summary"]["blocking_failures"], 0)

    def test_a_dns_provider_that_answers_raises_nothing(self):
        """The witness for this one."""
        self._add_provider(1, "NPM", "npm")
        self._add_provider(2, "AdGuard", "adguard")

        with self._live_target():
            result = self._preflight(proxy_provider_id=1, dns_provider_id=2)

        check = self._check(result, "dns_connection")
        self.assertTrue(check["ok"])
        self.assertEqual(check["detail_params"]["name"], "AdGuard")
        self.assertEqual(result["summary"]["warnings"], 0)

    def test_no_dns_provider_means_no_dns_probe(self):
        """Nothing to dial, nothing to say: a DNS-less route must not grow an empty line."""
        self._add_provider(1, "NPM", "npm")

        with self._live_target():
            result = self._preflight(proxy_provider_id=1)

        self.assertEqual([c for c in result["checks"] if c["name"] == "dns_connection"], [])


if __name__ == "__main__":
    unittest.main()
