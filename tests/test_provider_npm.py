"""Unit tests for NPMProvider — all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock

import requests

from app.providers.npm import NPMProvider


def _response(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    r.raise_for_status = MagicMock(side_effect=None if status_code < 400 else requests.HTTPError)
    return r


class TestNPMAuth(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")

    def test_login_success(self):
        self.npm.session.post = MagicMock(return_value=_response(200, {"token": "tok123"}))
        self.assertTrue(self.npm._login())
        self.assertEqual(self.npm._token, "tok123")
        self.assertIn("Bearer tok123", self.npm.session.headers["Authorization"])

    def test_login_failure_bad_credentials(self):
        self.npm.session.post = MagicMock(return_value=_response(401, {}))
        self.assertFalse(self.npm._login())
        self.assertIsNone(self.npm._token)

    def test_login_failure_network_error(self):
        self.npm.session.post = MagicMock(side_effect=requests.RequestException("timeout"))
        self.assertFalse(self.npm._login())

    def test_ensure_auth_reuses_valid_token(self):
        self.npm._token = "existing"
        self.npm.session.get = MagicMock(return_value=_response(200, []))
        self.assertTrue(self.npm._ensure_auth())
        self.assertEqual(self.npm._token, "existing")

    def test_ensure_auth_refreshes_expired_token(self):
        self.npm._token = "expired"
        # First call (token check) fails; then re-login succeeds
        self.npm.session.get = MagicMock(return_value=_response(401))
        self.npm.session.post = MagicMock(return_value=_response(200, {"token": "fresh"}))
        self.assertTrue(self.npm._ensure_auth())
        self.assertEqual(self.npm._token, "fresh")

    def test_test_connection_ok(self):
        self.npm._token = "tok"
        self.npm.session.get = MagicMock(return_value=_response(200, []))
        self.assertTrue(self.npm.test_connection())

    def test_test_connection_fail(self):
        self.npm._token = None
        self.npm.session.get = MagicMock(return_value=_response(401))
        self.npm.session.post = MagicMock(return_value=_response(401))
        self.assertFalse(self.npm.test_connection())


class TestNPMListHosts(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm._ensure_auth = MagicMock(return_value=True)

    def test_list_hosts_returns_normalised_entries(self):
        raw = [
            {
                "id": 1,
                "domain_names": ["app.example.com"],
                "forward_scheme": "http",
                "forward_host": "192.168.1.10",
                "forward_port": 3000,
                "ssl_forced": False,
                "allow_websocket_upgrade": True,
                "certificate_id": 2,
                "enabled": True,
            }
        ]
        self.npm.session.get = MagicMock(return_value=_response(200, raw))
        hosts = self.npm.list_hosts()
        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0]["id"], 1)
        self.assertEqual(hosts[0]["domains"], ["app.example.com"])
        self.assertEqual(hosts[0]["host"], "192.168.1.10")
        self.assertEqual(hosts[0]["port"], 3000)
        self.assertTrue(hosts[0]["websocket"])
        self.assertTrue(hosts[0]["enabled"])

    def test_list_hosts_returns_empty_on_network_error(self):
        self.npm.session.get = MagicMock(side_effect=requests.RequestException("err"))
        self.assertEqual(self.npm.list_hosts(), [])

    def test_list_hosts_returns_empty_on_auth_fail(self):
        self.npm._ensure_auth = MagicMock(return_value=False)
        self.assertEqual(self.npm.list_hosts(), [])


class TestNPMCreateHost(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm._ensure_auth = MagicMock(return_value=True)

    def test_create_host_success(self):
        self.npm.session.post = MagicMock(
            return_value=_response(201, {"id": 42, "domain_names": ["app.example.com"]})
        )
        result = self.npm.create_host("app.example.com", "10.0.0.1", 8080)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 42)
        self.assertEqual(result["domain"], "app.example.com")

    def test_create_host_with_cert(self):
        captured = {}

        def mock_post(url, json=None, **kwargs):
            captured.update(json or {})
            return _response(201, {"id": 5, "domain_names": ["tls.example.com"]})

        self.npm.session.post = MagicMock(side_effect=mock_post)
        self.npm.create_host("tls.example.com", "10.0.0.2", 443, "https", False, cert_id=3)
        self.assertEqual(captured.get("certificate_id"), 3)
        self.assertTrue(captured.get("ssl_forced"))

    def test_create_host_network_error(self):
        self.npm.session.post = MagicMock(side_effect=requests.RequestException("timeout"))
        self.assertIsNone(self.npm.create_host("app.example.com", "10.0.0.1", 80))

    def test_create_host_auth_fail(self):
        self.npm._ensure_auth = MagicMock(return_value=False)
        self.assertIsNone(self.npm.create_host("app.example.com", "10.0.0.1", 80))


class TestNPMDeleteHost(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm._ensure_auth = MagicMock(return_value=True)

    def test_delete_host_success_204(self):
        self.npm.session.delete = MagicMock(return_value=_response(204))
        self.assertTrue(self.npm.delete_host(1))

    def test_delete_host_success_200(self):
        self.npm.session.delete = MagicMock(return_value=_response(200, text="true"))
        self.assertTrue(self.npm.delete_host(1))

    def test_delete_host_text_true(self):
        r = _response(200)
        r.text = "true"
        self.npm.session.delete = MagicMock(return_value=r)
        self.assertTrue(self.npm.delete_host(1))

    def test_delete_host_network_error(self):
        self.npm.session.delete = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.npm.delete_host(1))

    def test_delete_host_auth_fail(self):
        self.npm._ensure_auth = MagicMock(return_value=False)
        self.assertFalse(self.npm.delete_host(1))


class TestNPMUpdateHost(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm._ensure_auth = MagicMock(return_value=True)

    def test_update_host_success(self):
        self.npm.session.put = MagicMock(return_value=_response(200, {"id": 1}))
        self.assertTrue(self.npm.update_host(1, "app.example.com", "10.0.0.5", 3000))

    def test_update_host_fail(self):
        self.npm.session.put = MagicMock(return_value=_response(404))
        self.assertFalse(self.npm.update_host(1, "app.example.com", "10.0.0.5", 3000))

    def test_update_host_network_error(self):
        self.npm.session.put = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.npm.update_host(1, "app.example.com", "10.0.0.5", 3000))


class TestNPMToggleHost(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm._ensure_auth = MagicMock(return_value=True)

    def test_enable_host_success(self):
        self.npm.session.post = MagicMock(return_value=_response(200))
        self.assertTrue(self.npm.toggle_host(5, True))
        call_url = self.npm.session.post.call_args[0][0]
        self.assertIn("/enable", call_url)

    def test_disable_host_success(self):
        self.npm.session.post = MagicMock(return_value=_response(200))
        self.assertTrue(self.npm.toggle_host(5, False))
        call_url = self.npm.session.post.call_args[0][0]
        self.assertIn("/disable", call_url)

    def test_toggle_returns_false_on_404(self):
        self.npm.session.post = MagicMock(return_value=_response(404))
        self.assertFalse(self.npm.toggle_host(5, True))

    def test_toggle_returns_false_on_auth_fail(self):
        self.npm._ensure_auth = MagicMock(return_value=False)
        self.assertFalse(self.npm.toggle_host(5, True))

    def test_toggle_returns_false_on_network_error(self):
        self.npm.session.post = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.npm.toggle_host(5, True))


class TestNPMCertificates(unittest.TestCase):

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "secret")
        self.npm._token = "tok"
        self.npm._ensure_auth = MagicMock(return_value=True)
        self.npm.session.get = MagicMock(return_value=_response(200, [
            {
                "id": 1,
                "nice_name": "*.example.com",
                "domain_names": ["*.example.com"],
                "expires_on": "2027-01-01T00:00:00.000Z",
            },
            {
                "id": 2,
                "nice_name": "sub.example.com",
                "domain_names": ["sub.example.com"],
                "expires_on": "2027-06-01T00:00:00.000Z",
            },
            {
                "id": 3,
                "nice_name": "other.net",
                "domain_names": ["other.net"],
                "expires_on": "",
            },
        ]))

    def test_get_certificates_returns_list(self):
        certs = self.npm.get_certificates()
        self.assertEqual(len(certs), 3)
        self.assertEqual(certs[0]["id"], 1)
        self.assertEqual(certs[0]["nice_name"], "*.example.com")

    def test_find_best_certificate_wildcard_exact(self):
        # *.example.com should match example.com domain
        cert_id = self.npm.find_best_certificate("example.com")
        self.assertEqual(cert_id, 1)

    def test_find_best_certificate_subdomain_match(self):
        # sub.example.com matches example.com suffix
        cert_id = self.npm.find_best_certificate("example.com")
        # Wildcard (*) is preferred, should return wildcard cert
        self.assertEqual(cert_id, 1)

    def test_find_best_certificate_returns_fallback(self):
        # No cert matching "unknown.org" — should return fallback (any cert)
        cert_id = self.npm.find_best_certificate("unknown.org")
        self.assertIsNotNone(cert_id)  # fallback returned, not None

    def test_find_best_certificate_returns_none_when_no_certs(self):
        self.npm.session.get = MagicMock(return_value=_response(200, []))
        cert_id = self.npm.find_best_certificate("example.com")
        self.assertIsNone(cert_id)

    def test_get_certificates_handles_npm_v3_wrapper(self):
        # NPM v3 wraps results in {"data": [...]}
        self.npm.session.get = MagicMock(return_value=_response(200, {
            "data": [{"id": 10, "nice_name": "wrapped.com", "domain_names": ["wrapped.com"], "expires_on": ""}]
        }))
        certs = self.npm.get_certificates()
        self.assertEqual(len(certs), 1)
        self.assertEqual(certs[0]["id"], 10)


if __name__ == "__main__":
    unittest.main()
