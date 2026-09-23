"""An edit that moves a service says so when the previous route stays up.

Moving a service to another mode, another provider or another hostname publishes the new
route and then removes the old one. Those removals dropped the provider's answer, and an
exception reached the journal alone: a provider that refused left the previous route serving
the hostname behind a 200 with `errors: []`. The interface showed the service moved, and the
old holder went on answering for it. The bulk enable had the mirror gap: it wrote "DNS
re-added" whatever the DNS server answered.

Three rules come with the fix, and each has its witness here.

A refusal is read against the listing where the provider's False also means "nothing to
delete": NPM answers a 404 on an id it no longer holds, and the Cloudflare DNS integration
answers False when no record matched. Cloudflare Tunnel is the exception, and it is not read
against anything: its ingress half answers True for a rule that is already gone, so a False
there is always a failure, possibly of the DNS half the ingress listing cannot see.

Every provider exception that reaches `errors` is masked, as the journal already was. No
integration shipped today lets a credential through there: the two that authenticate in the
query string catch their `requests` errors on writes and mask their listing refusals. The
masking closes the path for the next one, since `requests` quotes the URL it was calling,
query string included.

A move to another proxy deletes the previous host once. 1.6.0 deleted it in the proxy block
and again with the former targets, whose failures it had just started putting in the answer:
NPM answers the second deletion with a 404, and every move to another proxy came back with an
error about a host that had just been removed.
"""

import itertools
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import services as services_api
from app.api import sync as sync_api

HOST = "app.example.com"
OLD_IP = "198.51.100.7"
NEW_IP = "198.51.100.8"

# What `raise_for_status()` says, credential included.
_BOOM = "503 Server Error for url: http://p/api/x?token=abc123&zone=x"
_MASKED = "503 Server Error for url: http://p/api/x?token=***&zone=x"

_PROVIDERS = (
    (1, "CF Tunnel", "cloudflare_tunnel"),
    (2, "NPM", "npm"),
    (3, "AdGuard", "adguard"),
    (4, "NPM 2", "npm"),
    (5, "Cloudflare", "cloudflare"),
    (6, "Traefik", "traefik"),
    (7, "Zoraxy", "zoraxy"),
    (8, "CF Tunnel 2", "cloudflare_tunnel"),
)


def _request(method: str = "PUT", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _Fake:
    """One provider held in memory, proxy and DNS at once, answering as the real ones do.

    `hosts` maps a host id to the hostname it serves, `records` holds (host, answer) pairs.
    A call named in `refuse` answers False and changes nothing, so what it was asked to
    remove is still listed afterwards; one named in `raise_on` raises the text `requests`
    would. `absent_is_success` is the answer to the removal of something not held: False for
    NPM and the Cloudflare DNS integration, True for Cloudflare Tunnel.
    """

    def __init__(
        self, *, hosts=None, records=(), refuse=(), raise_on=(), listing=None,
        absent_is_success=False, by_name=False,
    ):
        self.hosts = dict(hosts or {})
        self.records = list(records)
        self.refuse = set(refuse)
        self.raise_on = set(raise_on)
        self.listing = listing
        self.absent_is_success = absent_is_success
        # Read by `host_id_is_hostname` before the class registered for the row's type.
        self.HOST_ID_IS_HOSTNAME = by_name
        self.calls: list[tuple] = []
        self._ids = itertools.count(501)

    def _enter(self, name: str, *args) -> bool:
        self.calls.append((name, *args))
        if name in self.raise_on:
            raise RuntimeError(_BOOM)
        return name not in self.refuse

    def find_best_certificate(self, _domain):
        return None

    def create_host(self, domain, *_rest):
        if not self._enter("create_host", domain):
            return None
        host_id = domain if self.HOST_ID_IS_HOSTNAME else next(self._ids)
        self.hosts[host_id] = domain
        return {"id": host_id}

    def update_host(self, host_id, domain, *_rest):
        if not self._enter("update_host", host_id, domain) or host_id not in self.hosts:
            return False
        del self.hosts[host_id]
        self.hosts[domain if self.HOST_ID_IS_HOSTNAME else host_id] = domain
        return True

    def delete_host(self, host_id):
        if not self._enter("delete_host", host_id):
            return False
        if host_id in self.hosts:
            del self.hosts[host_id]
            return True
        return self.absent_is_success

    def toggle_host(self, host_id, enabled):
        return self._enter("toggle_host", host_id, enabled) and host_id in self.hosts

    def list_hosts(self):
        self._enter("list_hosts")
        if self.listing is not None:
            raise self.listing
        return [{"id": i, "domains": [name], "enabled": True} for i, name in self.hosts.items()]

    def add_rewrite(self, host, answer):
        if not self._enter("add_rewrite", host, answer):
            return False
        if (host, answer) not in self.records:
            self.records.append((host, answer))
        return True

    def delete_rewrite(self, host, answer):
        if not self._enter("delete_rewrite", host, answer):
            return False
        if (host, answer) in self.records:
            self.records.remove((host, answer))
            return True
        return self.absent_is_success

    def update_rewrite(self, old_host, old_answer, host, answer):
        if not self._enter("update_rewrite", old_host, host):
            return False
        if (old_host, old_answer) not in self.records:
            return False
        self.records.remove((old_host, old_answer))
        self.records.append((host, answer))
        return True

    def list_rewrites(self):
        self._enter("list_rewrites")
        if self.listing is not None:
            raise self.listing
        return [{"domain": h, "answer": a} for h, a in self.records]


class _EditTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import app.db as _app_db

        tmp = tempfile.TemporaryDirectory()
        orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(self._restore, orig)
        models.DATA_DIR = _app_db.DATA_DIR = tmp.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(tmp.name, "left.behind.test.db")
        models.init_db()

        self.fakes = {
            1: _Fake(absent_is_success=True, by_name=True),
            2: _Fake(hosts={77: HOST}),
            3: _Fake(records=[(HOST, OLD_IP)]),
            4: _Fake(),
            5: _Fake(),
            6: _Fake(refuse={"delete_host"}, hosts={"app@docker": HOST}),
            7: _Fake(by_name=True),
            8: _Fake(absent_is_success=True, by_name=True),
        }
        # Both modules: the edit calls the providers itself, and the withdrawal of the former
        # targets calls them from `app.api.sync`.
        for p in (
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda row: self.fakes[row["id"]]),
            patch.object(sync_api, "create_provider", lambda row: self.fakes[row["id"]]),
        ):
            p.start()
            self.addCleanup(p.stop)

        conn = models.get_db()
        for pid, name, ptype in _PROVIDERS:
            conn.execute(
                """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
                   VALUES (?, ?, ?, 'http://p', 'admin', 'pass', '{}', 1)""",
                (pid, name, ptype),
            )
        conn.commit()
        conn.close()

    @staticmethod
    def _restore(orig) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = orig

    # -- fixtures ---------------------------------------------------------------

    def _seed(self, **cols) -> int:
        row = {
            "subdomain": "app", "domain": "example.com", "target_ip": "10.0.0.9",
            "target_port": 8080, "forward_scheme": "http", "websocket": 0, "enabled": 1,
            "expose_mode": "proxy_dns", "proxy_provider_id": 2, "npm_host_id": 77,
            "dns_provider_id": 3, "dns_ip": OLD_IP, "public_target_mode": "manual",
        }
        row.update(cols)
        conn = models.get_db()
        cur = conn.execute(
            f"INSERT INTO services ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
        return sid

    def _seed_tunnel(self, **cols) -> int:
        # `dns_ip` is NOT NULL: a tunnel service holds an empty one.
        tunnel = {
            "expose_mode": "tunnel", "tunnel_provider_id": 1, "tunnel_hostname": HOST,
            "proxy_provider_id": None, "npm_host_id": None, "dns_provider_id": None,
            "dns_ip": "",
        }
        tunnel.update(cols)
        return self._seed(**tunnel)

    @staticmethod
    def _body(**fields) -> services_api.ServiceIn:
        body = {
            "subdomain": "app", "domain": "example.com", "target_ip": "10.0.0.9",
            "target_port": 8080, "forward_scheme": "http", "websocket": False,
            "enabled": True, "expose_mode": "proxy_dns", "proxy_provider_id": 2,
            "dns_provider_id": 3, "dns_ip": OLD_IP, "public_target_mode": "manual",
        }
        body.update(fields)
        return services_api.ServiceIn(**body)

    @classmethod
    def _tunnel_body(cls, **fields) -> services_api.ServiceIn:
        tunnel = {
            "expose_mode": "tunnel", "tunnel_provider_id": 1, "tunnel_hostname": HOST,
            "proxy_provider_id": None, "dns_provider_id": None, "dns_ip": "",
        }
        tunnel.update(fields)
        return cls._body(**tunnel)

    def _edit(self, sid: int, body) -> list[str]:
        return services_api.update_service(sid, _request("PUT"), body)["errors"]

    def _bulk(self, sid: int, action: str) -> list[str]:
        result = services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action=action), _request("POST")
        )
        return result["errors"]

    @staticmethod
    def _journal() -> list[tuple[str, str]]:
        conn = models.get_db()
        rows = [
            (r["level"], r["message"])
            for r in conn.execute("SELECT level, message FROM logs ORDER BY id")
        ]
        conn.close()
        return rows

    def _assert_masked(self, errors: list[str]) -> None:
        for text in [*errors, *(message for _level, message in self._journal())]:
            self.assertNotIn("abc123", text)


class ABulkEnableReadsTheAnswerTests(_EditTestCase):
    def test_a_refused_record_is_reported(self) -> None:
        sid = self._seed(enabled=0)
        self.fakes[3] = _Fake(refuse={"add_rewrite"})

        self.assertEqual(
            self._bulk(sid, "enable"), [f"Service {sid}: failed to re-publish the DNS record"]
        )

        journal = self._journal()
        self.assertIn(("error", f"DNS record not published: {HOST}"), journal)
        self.assertFalse([m for _l, m in journal if m.startswith("DNS re-added on enable")])

    def test_an_accepted_record_is_written_down(self) -> None:
        sid = self._seed(enabled=0)
        self.fakes[3] = _Fake()

        self.assertEqual(self._bulk(sid, "enable"), [])

        self.assertEqual(self.fakes[3].records, [(HOST, OLD_IP)])
        self.assertTrue(
            [m for _l, m in self._journal() if m.startswith("DNS re-added on enable")]
        )


class ABulkDisableMasksTheReasonTests(_EditTestCase):
    def _assert_one_masked(self, errors: list[str], prefix: str) -> None:
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith(prefix), errors)
        self.assertIn("token=***", errors[0])
        self._assert_masked(errors)

    def test_the_proxy_reason_is_masked(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, raise_on={"toggle_host"})

        self._assert_one_masked(self._bulk(sid, "disable"), f"Service {sid}: proxy state error")

    def test_the_dns_reason_is_masked(self) -> None:
        sid = self._seed()
        self.fakes[3] = _Fake(records=[(HOST, OLD_IP)], raise_on={"delete_rewrite"})

        self._assert_one_masked(self._bulk(sid, "disable"), f"Service {sid}: DNS state error")

    def test_the_tunnel_reason_is_masked(self) -> None:
        sid = self._seed_tunnel()
        self.fakes[1] = _Fake(hosts={HOST: HOST}, by_name=True, raise_on={"delete_host"})

        self._assert_one_masked(self._bulk(sid, "disable"), f"Service {sid}: tunnel state error")


class ASwitchToATunnelTests(_EditTestCase):
    """proxy_dns -> tunnel: the previous proxy host and DNS record have to go."""

    def test_a_refused_proxy_host_is_reported(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, refuse={"delete_host"})

        self.assertEqual(
            self._edit(sid, self._tunnel_body()),
            [f"Failed to withdraw the previous proxy host: {HOST}"],
        )
        self.assertIn(("error", f"Proxy host still published: {HOST}"), self._journal())

    def test_a_proxy_host_already_gone_is_not_reported(self) -> None:
        """NPM answers a 404, read as False, for an id it no longer holds."""
        sid = self._seed()
        self.fakes[2] = _Fake()

        self.assertEqual(self._edit(sid, self._tunnel_body()), [])
        self.assertIn(("delete_host", 77), self.fakes[2].calls)

    def test_a_listing_that_fails_does_not_clear_a_refusal(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(refuse={"delete_host"}, listing=RuntimeError("401"))

        self.assertEqual(
            self._edit(sid, self._tunnel_body()),
            [f"Failed to withdraw the previous proxy host: {HOST}"],
        )

    def test_a_refused_dns_record_is_reported(self) -> None:
        sid = self._seed()
        self.fakes[3] = _Fake(records=[(HOST, OLD_IP)], refuse={"delete_rewrite"})

        self.assertEqual(
            self._edit(sid, self._tunnel_body()),
            [f"Failed to withdraw the previous DNS record: {HOST}"],
        )

    def test_a_dns_record_already_gone_is_not_reported(self) -> None:
        """The Cloudflare DNS integration answers False when no record matched."""
        sid = self._seed()
        self.fakes[3] = _Fake()

        self.assertEqual(self._edit(sid, self._tunnel_body()), [])
        self.assertIn(("delete_rewrite", HOST, OLD_IP), self.fakes[3].calls)

    def test_a_read_only_proxy_is_left_alone(self) -> None:
        """Nothing was ever pushed to Traefik, and it refuses every deletion."""
        sid = self._seed(proxy_provider_id=6, npm_host_id="app@docker")

        self.assertEqual(self._edit(sid, self._tunnel_body()), [])
        self.assertEqual(self.fakes[6].calls, [])

    def test_a_proxy_keyed_on_the_name_is_asked_for_the_name(self) -> None:
        """Whatever an older rename left in `npm_host_id`."""
        sid = self._seed(proxy_provider_id=7, npm_host_id="old.example.com")
        self.fakes[7] = _Fake(hosts={HOST: HOST}, by_name=True)

        self.assertEqual(self._edit(sid, self._tunnel_body()), [])
        self.assertEqual(self.fakes[7].hosts, {})
        self.assertIn(("delete_host", HOST), self.fakes[7].calls)


class ALeaveFromATunnelTests(_EditTestCase):
    """The previous tunnel route, whether the service leaves tunnel mode or the tunnel."""

    def test_a_tunnel_that_raises_is_reported_masked(self) -> None:
        sid = self._seed_tunnel()
        self.fakes[1] = _Fake(hosts={HOST: HOST}, by_name=True, raise_on={"delete_host"})
        self.fakes[2] = _Fake()
        self.fakes[3] = _Fake()

        errors = self._edit(sid, self._body())

        self.assertEqual(errors, [f"Could not withdraw the previous tunnel route: {_MASKED}"])
        self._assert_masked(errors)
        # The failure is the tunnel's, and the proxy's own success line is still written.
        self.assertTrue(
            [m for _l, m in self._journal() if m.startswith(f"Proxy updated: {HOST}")]
        )

    def test_a_tunnel_refusal_is_reported_whatever_its_listing_shows(self) -> None:
        """A False from Cloudflare Tunnel is a failure: its ingress half answers True for a
        rule already gone, so the half that failed may be the DNS one, which the ingress
        listing cannot see."""
        sid = self._seed_tunnel()
        self.fakes[1] = _Fake(by_name=True, refuse={"delete_host"})
        self.fakes[2] = _Fake()
        self.fakes[3] = _Fake()

        self.assertEqual(
            self._edit(sid, self._body()),
            [f"Failed to withdraw the previous tunnel route: {HOST}"],
        )

    def test_a_move_to_another_tunnel_reports_the_first_one(self) -> None:
        sid = self._seed_tunnel()
        self.fakes[1] = _Fake(hosts={HOST: HOST}, by_name=True, refuse={"delete_host"})

        self.assertEqual(
            self._edit(sid, self._tunnel_body(tunnel_provider_id=8)),
            [f"Failed to withdraw the previous tunnel route: {HOST}"],
        )
        self.assertEqual(self.fakes[8].hosts, {HOST: HOST})

    def test_a_disable_on_another_tunnel_reports_the_first_one(self) -> None:
        sid = self._seed_tunnel()
        self.fakes[1] = _Fake(hosts={HOST: HOST}, by_name=True, refuse={"delete_host"})

        self.assertEqual(
            self._edit(sid, self._tunnel_body(tunnel_provider_id=8, enabled=False)),
            [f"Failed to withdraw the previous tunnel route: {HOST}"],
        )

    def test_a_tunnel_that_lets_go_is_not_reported(self) -> None:
        sid = self._seed_tunnel()
        self.fakes[1] = _Fake(hosts={HOST: HOST}, absent_is_success=True, by_name=True)

        self.assertEqual(self._edit(sid, self._tunnel_body(tunnel_provider_id=8)), [])
        self.assertEqual(self.fakes[1].hosts, {})


class AMoveToAnotherProviderTests(_EditTestCase):
    def test_a_move_to_another_proxy_deletes_the_host_once(self) -> None:
        sid = self._seed()

        self.assertEqual(self._edit(sid, self._body(proxy_provider_id=4)), [])

        self.assertEqual(self.fakes[2].calls.count(("delete_host", 77)), 1)
        self.assertEqual(self.fakes[2].hosts, {})
        self.assertEqual(list(self.fakes[4].hosts.values()), [HOST])

    def test_a_proxy_that_keeps_the_host_is_still_reported(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, refuse={"delete_host"})

        self.assertEqual(
            self._edit(sid, self._body(proxy_provider_id=4)),
            ["Former target: Failed to delete proxy host on NPM"],
        )

    def test_a_record_the_previous_dns_server_keeps_is_reported(self) -> None:
        sid = self._seed(dns_provider_id=5)
        self.fakes[3] = _Fake()
        self.fakes[5] = _Fake(records=[(HOST, OLD_IP)], refuse={"delete_rewrite"})

        errors = self._edit(sid, self._body(dns_provider_id=3, dns_ip=NEW_IP))

        self.assertIn(f"Failed to withdraw the previous DNS record: {HOST}", errors)
        self.assertEqual(self.fakes[3].records, [(HOST, NEW_IP)])

    def test_a_record_already_gone_from_the_previous_server_is_not_reported(self) -> None:
        sid = self._seed(dns_provider_id=5)
        self.fakes[3] = _Fake()

        self.assertEqual(self._edit(sid, self._body(dns_provider_id=3, dns_ip=NEW_IP)), [])

    def test_a_re_enable_at_a_new_address_is_not_reported(self) -> None:
        """The disable took the record: the one at the previous address is not there."""
        sid = self._seed(enabled=0)
        self.fakes[3] = _Fake()

        self.assertEqual(self._edit(sid, self._body(dns_ip=NEW_IP)), [])
        self.assertEqual(self.fakes[3].records, [(HOST, NEW_IP)])


class ADisableThatRaisesTests(_EditTestCase):
    def test_a_suspension_that_raises_is_in_the_answer(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, raise_on={"toggle_host"})

        errors = self._edit(sid, self._body(enabled=False))

        self.assertEqual(errors, [f"Could not change the proxy host's state: {_MASKED}"])
        self._assert_masked(errors)


class ADeletionTests(_EditTestCase):
    def test_a_record_already_withdrawn_is_not_reported(self) -> None:
        """A disabled service lost its record when it was disabled."""
        sid = self._seed(enabled=0, dns_provider_id=5)

        self.assertEqual(
            services_api.delete_service(sid, _request("DELETE")), {"ok": True, "errors": []}
        )
        self.assertIn(("delete_rewrite", HOST, OLD_IP), self.fakes[5].calls)

    def test_a_record_still_held_is_reported(self) -> None:
        sid = self._seed(enabled=0, dns_provider_id=5)
        self.fakes[5] = _Fake(records=[(HOST, OLD_IP)], refuse={"delete_rewrite"})

        self.assertEqual(
            services_api.delete_service(sid, _request("DELETE")),
            {"ok": True, "errors": ["Failed to delete DNS rewrite on Cloudflare"]},
        )

    def test_the_reasons_are_masked(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, raise_on={"delete_host"})
        self.fakes[3] = _Fake(records=[(HOST, OLD_IP)], raise_on={"delete_rewrite"})

        result = services_api.delete_service(sid, _request("DELETE"))

        self.assertEqual(
            result["errors"], [f"Proxy (NPM): {_MASKED}", f"DNS (AdGuard): {_MASKED}"]
        )
        self._assert_masked(result["errors"])


class APushMasksTheReasonTests(_EditTestCase):
    """The push and its dry-run put a provider's exception in `errors` the same way."""

    def setUp(self) -> None:
        super().setUp()
        p = patch.object(sync_api, "require_auth", lambda _req, scope=None: None)
        p.start()
        self.addCleanup(p.stop)

    def _push(self, sid: int) -> list[str]:
        return sync_api.push_service(sid, _request("POST"))["errors"]

    def _plan(self, sid: int) -> list[str]:
        return sync_api.dry_run_push_service(sid, _request("POST"))["errors"]

    def test_a_push_masks_both_reasons(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, raise_on={"update_host"})
        self.fakes[3] = _Fake(raise_on={"add_rewrite"})

        errors = self._push(sid)

        self.assertEqual(errors, [f"Proxy (NPM): {_MASKED}", f"DNS (AdGuard): {_MASKED}"])
        self._assert_masked(errors)

    def test_a_push_that_withholds_masks_the_reason(self) -> None:
        """A disabled service is suspended on its proxy, not published."""
        sid = self._seed(enabled=0)
        self.fakes[2] = _Fake(hosts={77: HOST}, raise_on={"toggle_host"})

        errors = self._push(sid)

        self.assertEqual(errors, [f"Proxy (NPM): {_MASKED}"])
        self._assert_masked(errors)

    def test_a_dry_run_masks_the_reason(self) -> None:
        sid = self._seed()
        self.fakes[2] = _Fake(hosts={77: HOST}, listing=RuntimeError(_BOOM))

        self.assertEqual(self._plan(sid), [f"Proxy (NPM): {_MASKED}"])

    def test_a_dry_run_that_withholds_masks_the_reason(self) -> None:
        """Building the client is the one call this plan makes."""
        sid = self._seed(enabled=0)

        with patch.object(sync_api, "create_provider", side_effect=RuntimeError(_BOOM)):
            self.assertEqual(self._plan(sid), [f"Proxy (NPM): {_MASKED}"])


if __name__ == "__main__":
    unittest.main()
