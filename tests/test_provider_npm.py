"""Unit tests for NPMProvider — all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock

import requests

from app.providers.base import ProviderListingRefused
from app.providers.npm import NPMProvider


def _response(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    r.raise_for_status = MagicMock(side_effect=None if status_code < 400 else requests.HTTPError)
    return r


def _bad_json_response() -> MagicMock:
    """A 200 whose body is not JSON, which `requests` reports by raising from `.json()`."""
    r = _response(200)
    r.json.side_effect = ValueError("no json")
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

    def test_a_network_error_is_not_an_empty_host_list(self):
        """[] is how the drift check learns a route is gone. A failed call never said that."""
        self.npm.session.get = MagicMock(side_effect=requests.RequestException("err"))
        with self.assertRaises(ProviderListingRefused):
            self.npm.list_hosts()

    def test_a_refused_login_is_not_an_empty_host_list(self):
        """NPM declining the token says nothing about the hosts it holds."""
        self.npm._ensure_auth = MagicMock(return_value=False)
        with self.assertRaises(ProviderListingRefused):
            self.npm.list_hosts()


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
        self.npm.session.get = MagicMock(return_value=_response(404))
        self.assertFalse(self.npm.toggle_host(5, True))

    def test_a_host_already_in_the_wanted_state_is_not_a_refusal(self):
        """NPM answers 400 to an enable on a host it already serves.

        Measured against Nginx Proxy Manager: the body is
        `{"error": {"code": 400, "message": "Host is already enabled"}}`. Every push
        resumes the host it has just updated and almost every host it updates is already
        running, so reading the status code alone reported the ordinary edit of a route as
        a proxy that had refused the push, and told the operator to go and check
        credentials that were fine.
        """
        self.npm.session.post = MagicMock(
            return_value=_response(400, {"error": {"code": 400, "message": "Host is already enabled"}})
        )
        self.npm.session.get = MagicMock(return_value=_response(200, {"id": 5, "enabled": True}))
        self.assertTrue(self.npm.toggle_host(5, True))

    def test_a_host_already_suspended_is_not_a_refusal_either(self):
        self.npm.session.post = MagicMock(return_value=_response(400))
        self.npm.session.get = MagicMock(return_value=_response(200, {"id": 5, "enabled": False}))
        self.assertTrue(self.npm.toggle_host(5, False))

    def test_toggle_returns_false_on_auth_fail(self):
        self.npm._ensure_auth = MagicMock(return_value=False)
        self.assertFalse(self.npm.toggle_host(5, True))

    def test_toggle_returns_false_on_network_error(self):
        self.npm.session.post = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.npm.toggle_host(5, True))

    def test_a_refusal_that_left_the_wrong_state_is_still_a_refusal(self):
        """The read-back must not turn every refusal into a success.

        A host that is still switched off after an enable was genuinely refused, and that
        is the one case the operator does need to hear about.
        """
        self.npm.session.post = MagicMock(return_value=_response(400))
        self.npm.session.get = MagicMock(return_value=_response(200, {"id": 5, "enabled": False}))
        self.assertFalse(self.npm.toggle_host(5, True))

    def test_a_state_that_cannot_be_read_is_not_claimed_as_success(self):
        for name, mocked in (
            ("http error", MagicMock(return_value=_response(500))),
            ("network error", MagicMock(side_effect=requests.RequestException("err"))),
            ("unreadable body", MagicMock(return_value=_bad_json_response())),
        ):
            with self.subTest(read_back=name):
                self.npm.session.post = MagicMock(return_value=_response(400))
                self.npm.session.get = mocked
                self.assertFalse(self.npm.toggle_host(5, True))

    def test_a_call_that_succeeded_is_not_read_back(self):
        self.npm.session.post = MagicMock(return_value=_response(200))
        self.npm.session.get = MagicMock(return_value=_response(200, {"enabled": False}))
        self.assertTrue(self.npm.toggle_host(5, True))
        self.npm.session.get.assert_not_called()


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

    def test_find_best_certificate_returns_none_when_nothing_covers_the_host(self):
        # Historically this returned "any certificate" as a last resort. That id then
        # reached `create_host`, which sets `ssl_forced: cert_id is not None` — HTTPS
        # forced on a host the certificate does not cover, i.e. a browser error wall
        # on every visit. No certificate is the correct answer.
        self.assertIsNone(self.npm.find_best_certificate("unknown.org"))

    def test_find_best_certificate_matches_parent_wildcard(self):
        # Real-world shape: callers pass the full hostname, and `*.example.com` is the
        # certificate that covers it. The old suffix logic never matched here and fell
        # through to the arbitrary fallback.
        self.assertEqual(self.npm.find_best_certificate("vault.example.com"), 1)

    def test_find_best_certificate_prefers_exact_over_wildcard(self):
        self.assertEqual(self.npm.find_best_certificate("sub.example.com"), 2)

    def test_find_best_certificate_rejects_deeper_label_for_a_wildcard(self):
        # A TLS wildcard covers exactly one label: `*.example.com` does not cover
        # `a.b.example.com`.
        self.assertIsNone(self.npm.find_best_certificate("a.b.example.com"))

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


class TheNPMPanelProvesItCanReadBeforeItSaysReadyTests(unittest.TestCase):
    """"Login OK" was the last word NPM said, and it was not the question being asked.

    Every other integration ends its validation with the one read it actually needs --
    AdGuard lists its rewrites, Zoraxy its rules, Traefik its routers. NPM stopped at the
    token. But NPM gives a user per-object permissions, so an account whose `proxy_hosts`
    visibility is off signs in perfectly and is then refused the very list Vauxtra manages.
    The panel called that account ready, and the refusal surfaced later as a push that
    saved nothing.
    """

    def setUp(self):
        self.npm = NPMProvider("http://npm:81", "admin@example.com", "pw")
        self.npm._ensure_auth = MagicMock(return_value=True)
        self.npm.session.get = MagicMock(return_value=_response(200, []))

    def _validate(self):
        return self.npm.validate_permissions()

    def test_a_readable_npm_reports_all_three_checks(self):
        self.npm.list_hosts = MagicMock(return_value=[{"id": 1}, {"id": 2}])
        result = self._validate()
        self.assertTrue(result["ok"])
        self.assertEqual(
            [c["detail_code"] for c in result["checks"]],
            ["connection_ok", "login_ok", "proxy_read_ok"],
        )
        self.assertIn("2 hosts", result["checks"][-1]["detail"])

    def test_an_account_that_signs_in_but_cannot_list_is_not_ready(self):
        """The whole point: authenticated, reachable, and still not usable."""
        self.npm.list_hosts = MagicMock(
            side_effect=ProviderListingRefused("NPM would not list its proxy hosts: 403")
        )
        result = self._validate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"][-1]["detail_code"], "proxy_read_failed")
        self.assertTrue(result["checks"][-1]["blocking"])
        self.assertTrue(result["checks"][1]["ok"], "the login itself did succeed")

    def test_an_empty_npm_is_readable_not_broken(self):
        """Nothing published is a fine answer; it is a failed read that is not."""
        self.npm.list_hosts = MagicMock(return_value=[])
        result = self._validate()
        self.assertTrue(result["ok"])
        self.assertEqual(result["checks"][-1]["detail_code"], "proxy_read_ok")

    def test_the_read_is_not_attempted_when_the_login_failed(self):
        """A second failure about the same cause reads as two problems."""
        self.npm._ensure_auth = MagicMock(return_value=False)
        self.npm.list_hosts = MagicMock(side_effect=AssertionError("must not be called"))
        result = self._validate()
        self.assertFalse(result["ok"])
        self.assertEqual([c["detail_code"] for c in result["checks"]], ["connection_ok", "login_failed"])


if __name__ == "__main__":
    unittest.main()
