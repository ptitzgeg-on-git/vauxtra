"""A push to NPM keeps what the operator set in NPM, the certificate first.

Two defects compounded. The routes looked the certificate up by the service's zone,
`example.com`, where the lookup expects the name the host answers on, `vault.example.com`:
a wildcard was found through a `*.{zone}` rule written for that mistake, and a certificate
issued for the host itself -- what NPM requests by default, one per host -- never was. Then
`update_host` wrote the host whole, the way `create_host` builds a new one: one domain name,
no custom location, HSTS off, and the certificate the lookup had found, or none. NPM keeps
what a PUT does not name, so every push and every edit took HTTPS away from a host with its
own certificate, and reset the aliases, custom locations and HSTS of every host.

The lookup now takes the full name at every call site, and the update names only what
Vauxtra owns.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from starlette.requests import Request

from app import models
from app.api import services as services_api
from app.api import sync as sync_api
from app.providers.npm import NPMProvider
from app.providers.zoraxy import ZoraxyProvider

HOST = "vault.example.com"

_OPERATOR_HOST = {
    "id": 7,
    "domain_names": [HOST, "vault.lan"],
    "forward_scheme": "http",
    "forward_host": "192.168.1.10",
    "forward_port": 8200,
    "certificate_id": 12,
    "ssl_forced": True,
    "http2_support": True,
    "hsts_enabled": True,
    "block_exploits": False,
    "caching_enabled": True,
    "allow_websocket_upgrade": False,
    "access_list_id": 3,
    "advanced_config": "client_max_body_size 0;",
    "locations": [{"path": "/ui", "forward_host": "192.168.1.11", "forward_port": 8201}],
    "meta": {"letsencrypt_agree": True},
    "enabled": True,
}

_CERTIFICATES = [
    {"id": 12, "nice_name": HOST, "domain_names": [HOST]},
    {"id": 20, "nice_name": "*.example.com", "domain_names": ["*.example.com"]},
    {"id": 30, "nice_name": "example.com", "domain_names": ["example.com"]},
]


def _response(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else {}
    r.raise_for_status = MagicMock()
    return r


class _NPMTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._ensure_auth = MagicMock(return_value=True)
        self.host = dict(_OPERATOR_HOST)
        self.certificates = list(_CERTIFICATES)
        self.sent: dict = {}

        def get(url, **_kwargs):
            if url.endswith("/nginx/certificates"):
                return _response(200, self.certificates)
            if url.endswith("/nginx/proxy-hosts/7"):
                return _response(200, self.host)
            return _response(404)

        def put(_url, json=None, **_kwargs):
            self.sent.update(json or {})
            return _response(200, {"id": 7})

        self.npm.session.get = MagicMock(side_effect=get)
        self.npm.session.put = MagicMock(side_effect=put)


class TheLookupTakesTheFullNameTests(_NPMTestCase):
    def test_a_certificate_issued_for_the_host_is_found(self) -> None:
        self.assertEqual(self.npm.find_best_certificate(HOST), 12)

    def test_the_host_s_own_certificate_wins_over_the_wildcard(self) -> None:
        self.certificates.reverse()
        self.assertEqual(self.npm.find_best_certificate(HOST), 12)

    def test_the_wildcard_covers_a_name_one_label_down(self) -> None:
        self.assertEqual(self.npm.find_best_certificate("grafana.example.com"), 20)

    def test_the_wildcard_does_not_cover_the_zone_itself(self) -> None:
        self.certificates = [c for c in _CERTIFICATES if c["id"] != 30]
        self.assertIsNone(self.npm.find_best_certificate("example.com"))

    def test_the_zone_is_covered_by_a_certificate_that_names_it(self) -> None:
        self.assertEqual(self.npm.find_best_certificate("example.com"), 30)

    def test_zoraxy_follows_the_same_rule(self) -> None:
        z = ZoraxyProvider.__new__(ZoraxyProvider)
        z.get_certificates = MagicMock(return_value=[
            {"id": "_.example.com", "nice_name": "_.example.com", "domains": ["*.example.com"]},
        ])
        self.assertEqual(z.find_best_certificate(HOST), "_.example.com")
        self.assertIsNone(z.find_best_certificate("example.com"))


class AnUpdateKeepsWhatTheOperatorSetTests(_NPMTestCase):
    def test_only_what_vauxtra_owns_is_sent(self) -> None:
        self.assertTrue(self.npm.update_host(7, HOST, "192.168.1.20", 8300, "https", True, 12))
        self.assertEqual(set(self.sent), {
            "domain_names", "forward_scheme", "forward_host", "forward_port",
            "allow_websocket_upgrade",
        })
        self.assertEqual(self.sent["forward_host"], "192.168.1.20")
        self.assertEqual(self.sent["forward_port"], 8300)
        self.assertEqual(self.sent["forward_scheme"], "https")
        self.assertTrue(self.sent["allow_websocket_upgrade"])

    def test_the_other_names_of_the_host_are_kept(self) -> None:
        self.npm.update_host(7, HOST, "192.168.1.10", 8200)
        self.assertEqual(self.sent["domain_names"], [HOST, "vault.lan"])

    def test_a_rename_replaces_the_service_s_name_and_keeps_the_others(self) -> None:
        self.npm.update_host(7, "safe.example.com", "192.168.1.10", 8200, cert_id=20)
        self.assertEqual(self.sent["domain_names"], ["safe.example.com", "vault.lan"])

    def test_no_certificate_found_leaves_the_host_s_certificate_alone(self) -> None:
        # The lookup can miss one: a certificate this NPM user may not list, a custom one.
        self.npm.update_host(7, HOST, "192.168.1.10", 8200, cert_id=None)
        for field in ("certificate_id", "ssl_forced", "http2_support", "hsts_enabled"):
            self.assertNotIn(field, self.sent)

    def test_a_new_certificate_is_set_and_forced(self) -> None:
        self.host["certificate_id"] = 0
        self.npm.update_host(7, HOST, "192.168.1.10", 8200, cert_id=12)
        self.assertEqual(self.sent["certificate_id"], 12)
        self.assertTrue(self.sent["ssl_forced"])

    def test_a_rename_drops_a_certificate_that_does_not_cover_the_new_name(self) -> None:
        self.npm.update_host(7, "other.example.org", "192.168.1.10", 8200, cert_id=None)
        self.assertEqual(self.sent["certificate_id"], 0)
        self.assertFalse(self.sent["ssl_forced"])

    def test_a_rename_keeps_a_certificate_it_cannot_judge(self) -> None:
        self.certificates = []
        self.npm.update_host(7, "other.example.org", "192.168.1.10", 8200, cert_id=None)
        self.assertNotIn("certificate_id", self.sent)

    def test_a_host_that_cannot_be_read_is_not_written(self) -> None:
        self.assertFalse(self.npm.update_host(8, HOST, "192.168.1.10", 8200))
        self.npm.session.put.assert_not_called()


def _request(method: str = "PUT") -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": []})


class _Proxy:
    """A proxy that records the name each certificate lookup was asked for."""

    HOST_ID_IS_HOSTNAME = False

    def __init__(self) -> None:
        self.looked_up: list[str] = []
        self.hosts: dict = {}

    def find_best_certificate(self, host):
        self.looked_up.append(host)
        return None

    def create_host(self, domain, *_rest):
        self.hosts[501] = domain
        return {"id": 501}

    def update_host(self, host_id, domain, *_rest):
        self.hosts[host_id] = domain
        return True

    def delete_host(self, host_id):
        return self.hosts.pop(host_id, None) is not None

    def toggle_host(self, host_id, enabled):
        return host_id in self.hosts

    def list_hosts(self):
        return [{"id": i, "domains": [d], "enabled": True} for i, d in self.hosts.items()]


class EveryRouteLooksUpTheFullNameTests(unittest.TestCase):
    def setUp(self) -> None:
        import app.db as _app_db

        tmp = tempfile.TemporaryDirectory()
        orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(self._restore, orig)
        models.DATA_DIR = _app_db.DATA_DIR = tmp.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(tmp.name, "full.name.test.db")
        models.init_db()

        self.proxy = _Proxy()
        for p in (
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(sync_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: self.proxy),
            patch.object(sync_api, "create_provider", lambda _row: self.proxy),
        ):
            p.start()
            self.addCleanup(p.stop)

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://p', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _restore(orig) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = orig

    @staticmethod
    def _body(**fields) -> services_api.ServiceIn:
        body = {
            "subdomain": "vault", "domain": "example.com", "target_ip": "192.168.1.10",
            "target_port": 8200, "forward_scheme": "http", "websocket": False,
            "enabled": True, "expose_mode": "proxy_dns", "proxy_provider_id": 2,
            "dns_provider_id": None, "dns_ip": "", "public_target_mode": "manual",
        }
        body.update(fields)
        return services_api.ServiceIn(**body)

    def _create(self, **fields) -> int:
        response = services_api.add_service(_request("POST"), self._body(**fields))
        return json.loads(response.body)["id"]

    def test_creating_a_service(self) -> None:
        self._create()
        self.assertEqual(self.proxy.looked_up, [HOST])

    def test_editing_a_service(self) -> None:
        sid = self._create()
        services_api.update_service(sid, _request(), self._body(subdomain="safe"))
        self.assertEqual(self.proxy.looked_up[-1], "safe.example.com")

    def test_pushing_a_service(self) -> None:
        sid = self._create()
        sync_api.push_service(sid, _request("POST"))
        self.assertEqual(self.proxy.looked_up[-1], HOST)

    def test_re_enabling_a_service_whose_host_was_removed(self) -> None:
        sid = self._create()
        conn = models.get_db()
        conn.execute("UPDATE services SET enabled=0, npm_host_id=NULL WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        services_api.bulk_action(
            services_api._BulkActionBody(ids=[sid], action="enable"), _request("POST")
        )
        self.assertEqual(self.proxy.looked_up[-1], HOST)


if __name__ == "__main__":
    unittest.main()
