"""Unit tests for TraefikProvider — read-only operations, all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock

import requests

from app.providers.traefik import TraefikProvider


def _response(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = json_data if json_data is not None else []
    r.raise_for_status = MagicMock(
        side_effect=None if status_code < 400 else requests.HTTPError
    )
    return r


SAMPLE_ROUTERS = [
    {
        "name": "myapp-router",
        "rule": "Host(`app.example.com`)",
        "service": "myapp-service",
        "status": "enabled",
        "middlewares": ["auth@file"],
        "tls": {"certResolver": "letsencrypt"},
    },
    {
        "name": "api-router",
        "rule": "Host(`api.example.com`) && PathPrefix(`/v1`)",
        "service": "api-service",
        "status": "enabled",
        "middlewares": [],
    },
    {
        "name": "disabled-router",
        "rule": "Host(`old.example.com`)",
        "service": "old-service",
        "status": "disabled",
    },
]

SAMPLE_SERVICES = [
    {
        "name": "myapp-service",
        "loadBalancer": {"servers": [{"url": "http://192.168.1.10:3000"}]},
    },
    {
        "name": "api-service",
        "loadBalancer": {"servers": [{"url": "https://192.168.1.20:8443"}]},
    },
]

SAMPLE_MIDDLEWARES = [
    {
        "name": "auth@file",
        "type": "forwardAuth",
        "forwardAuth": {"address": "http://auth:4181"},
    }
]


class TestTraefikConnection(unittest.TestCase):

    def setUp(self):
        self.tr = TraefikProvider("http://traefik:8080", "", "")

    def test_connection_ok(self):
        self.tr.session.get = MagicMock(return_value=_response(200, {"routers": 3}))
        self.assertTrue(self.tr.test_connection())

    def test_connection_fail_401(self):
        self.tr.session.get = MagicMock(return_value=_response(401))
        self.assertFalse(self.tr.test_connection())

    def test_connection_network_error(self):
        self.tr.session.get = MagicMock(side_effect=requests.RequestException("refused"))
        self.assertFalse(self.tr.test_connection())

    def test_basic_auth_set_when_credentials_provided(self):
        tr = TraefikProvider("http://traefik:8080", "user", "pass")
        self.assertEqual(tr.session.auth, ("user", "pass"))

    def test_no_auth_when_credentials_empty(self):
        tr = TraefikProvider("http://traefik:8080", "", "")
        self.assertIsNone(tr.session.auth)


class TestTraefikListHosts(unittest.TestCase):

    def setUp(self):
        self.tr = TraefikProvider("http://traefik:8080", "", "")

    def _mock_responses(self, routers=None, services=None, middlewares=None):
        """Set up mock responses for routers, services, and middlewares endpoints."""
        def mock_get(url, **kwargs):
            if "routers" in url:
                return _response(200, routers or SAMPLE_ROUTERS)
            if "services" in url:
                return _response(200, services or SAMPLE_SERVICES)
            if "middlewares" in url:
                return _response(200, middlewares or SAMPLE_MIDDLEWARES)
            return _response(200, [])
        self.tr.session.get = MagicMock(side_effect=mock_get)

    def test_list_hosts_returns_enabled_routers(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        # Only 2 of the 3 sample routers are enabled
        self.assertEqual(len(hosts), 2)

    def test_list_hosts_extracts_domains(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        domains = {h["domains"][0] for h in hosts}
        self.assertIn("app.example.com", domains)
        self.assertIn("api.example.com", domains)

    def test_list_hosts_resolves_backend_target(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        app_host = next(h for h in hosts if "app.example.com" in h["domains"])
        self.assertEqual(app_host["host"], "192.168.1.10")
        self.assertEqual(app_host["port"], 3000)
        self.assertEqual(app_host["scheme"], "http")

    def test_list_hosts_parses_https_backend(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        api_host = next(h for h in hosts if "api.example.com" in h["domains"])
        self.assertEqual(api_host["scheme"], "https")
        self.assertEqual(api_host["port"], 8443)

    def test_list_hosts_includes_tls_resolver(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        app_host = next(h for h in hosts if "app.example.com" in h["domains"])
        self.assertTrue(app_host["ssl"])
        self.assertEqual(app_host["tls_resolver"], "letsencrypt")

    def test_list_hosts_skips_disabled_routers(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        all_domains = [d for h in hosts for d in h["domains"]]
        self.assertNotIn("old.example.com", all_domains)

    def test_list_hosts_skips_routers_without_host_rule(self):
        routers_no_host = [
            {"name": "path-only", "rule": "PathPrefix(`/api`)", "service": "svc", "status": "enabled"}
        ]
        self._mock_responses(routers=routers_no_host)
        self.assertEqual(self.tr.list_hosts(), [])

    def test_list_hosts_returns_empty_on_network_error(self):
        self.tr.session.get = MagicMock(side_effect=requests.RequestException("err"))
        self.assertEqual(self.tr.list_hosts(), [])

    def test_list_hosts_includes_middlewares(self):
        self._mock_responses()
        hosts = self.tr.list_hosts()
        app_host = next(h for h in hosts if "app.example.com" in h["domains"])
        self.assertIn("auth@file", app_host["middlewares"])


class TestTraefikReadOnly(unittest.TestCase):

    def setUp(self):
        self.tr = TraefikProvider("http://traefik:8080", "", "")

    def test_create_host_returns_none(self):
        """Traefik is read-only — create always returns None."""
        result = self.tr.create_host("app.example.com", "10.0.0.1", 80)
        self.assertIsNone(result)

    def test_delete_host_returns_false(self):
        """Traefik is read-only — delete always returns False."""
        result = self.tr.delete_host("some-router")
        self.assertFalse(result)

    def test_toggle_host_returns_false(self):
        """Traefik has no toggle support — falls through to base default."""
        self.assertFalse(self.tr.toggle_host("some-router", True))
        self.assertFalse(self.tr.toggle_host("some-router", False))

    def test_get_certificates_returns_empty(self):
        """Traefik manages ACME certs internally, no API access."""
        self.assertEqual(self.tr.get_certificates(), [])

    def test_find_best_certificate_returns_none(self):
        self.assertIsNone(self.tr.find_best_certificate("example.com"))


if __name__ == "__main__":
    unittest.main()
