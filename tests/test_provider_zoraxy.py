"""Unit tests for ZoraxyProvider — all HTTP calls are mocked."""

import copy
import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests
from starlette.requests import Request

from app import models, scheduler
from app.api import certificates as certificates_api
from app.api import services as services_api
from app.api import sync as sync_api
from app.config import encrypt_secret
from app.providers.factory import (
    PROVIDER_TYPES,
    certificate_provider_types,
    create_provider,
    host_id_is_hostname,
)
from app.providers.zoraxy import ZoraxyProvider

_NO_JSON = object()


def _response(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    if json_data is _NO_JSON:
        r.json.side_effect = ValueError("no JSON body")
    else:
        r.json.return_value = json_data
    r.text = text
    r.raise_for_status = MagicMock(side_effect=None if status_code < 400 else requests.HTTPError)
    return r


_LOGIN_PAGE = '<html><head><meta name="zoraxy.csrf.Token" content="tok123"></head></html>'

_RULE = {
    "ProxyType": 1,
    "RootOrMatchingDomain": "app.example.com",
    "MatchingDomainAlias": ["www.example.com"],
    "ActiveOrigins": [
        {"OriginIpOrDomain": "10.0.0.5:8443", "RequireTLS": True, "SkipCertValidations": True,
         "SkipWebSocketOriginCheck": False, "Weight": 1, "MaxConn": 0, "RespTimeout": 0},
    ],
    "InactiveOrigins": [],
    "Disabled": False,
    "BypassGlobalTLS": True,
    "DisableWebSocket": False,
    "WebsocketTimeout": 0,
    "TlsOptions": {
        "DisableSNI": False,
        "DisableLegacyCertificateMatching": False,
        "EnableAutoHTTPS": False,
        "PreferredCertificate": {"app.example.com": "_.example.com"},
    },
    "Tags": ["media"],
    "AuthenticationProvider": {"AuthMethod": 0},
    "RequireRateLimit": False,
    "RateLimit": 0,
    "RequireCaptcha": False,
    "CaptchaConfig": None,
}


def _rule(**overrides) -> dict:
    rule = copy.deepcopy(_RULE)
    rule.update(overrides)
    return rule


def _posted(provider) -> list[tuple[str, dict]]:
    """(path, form) of every POST the provider sent, in order."""
    out = []
    for call in provider.session.post.call_args_list:
        url = call.args[0] if call.args else call.kwargs["url"]
        out.append((url.replace(provider.base_url, ""), call.kwargs["data"]))
    return out


class TestZoraxyCSRF(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000/", "vauxtra", "secret")

    def test_base_url_is_stripped(self):
        self.assertEqual(self.z.base_url, "http://zoraxy:8000")

    def test_fetch_csrf_parses_meta_and_caches_token(self):
        self.z.session.get = MagicMock(return_value=_response(200, _NO_JSON, _LOGIN_PAGE))
        self.assertEqual(self.z._fetch_csrf(), "tok123")
        self.assertEqual(self.z._csrf_token, "tok123")
        call = self.z.session.get.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/login.html")
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_fetch_csrf_accepts_single_quoted_meta(self):
        page = "<meta name='zoraxy.csrf.Token' content='abc'>"
        self.z.session.get = MagicMock(return_value=_response(200, _NO_JSON, page))
        self.assertEqual(self.z._fetch_csrf(), "abc")

    def test_fetch_csrf_returns_none_without_meta(self):
        self.z.session.get = MagicMock(return_value=_response(200, _NO_JSON, "<html></html>"))
        self.assertIsNone(self.z._fetch_csrf())
        self.assertIsNone(self.z._csrf_token)

    def test_fetch_csrf_returns_none_on_network_error(self):
        self.z.session.get = MagicMock(side_effect=requests.RequestException("timeout"))
        self.assertIsNone(self.z._fetch_csrf())


class TestZoraxyLogin(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._csrf_token = "tok"

    def test_login_success_is_form_encoded_with_csrf_header(self):
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        self.assertTrue(self.z._login())
        call = self.z.session.post.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/auth/login")
        self.assertEqual(call.kwargs["data"],
                         {"username": "vauxtra", "password": "secret", "rmbme": "true"})
        self.assertEqual(call.kwargs["headers"], {"X-CSRF-Token": "tok"})
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertNotIn("json", call.kwargs)

    def test_login_bad_password_is_http_200_with_error_body(self):
        self.z.session.post = MagicMock(
            return_value=_response(200, {"error": "Invalid username or password"})
        )
        self.assertFalse(self.z._login())

    def test_login_failure_network_error(self):
        self.z.session.post = MagicMock(side_effect=requests.RequestException("timeout"))
        self.assertFalse(self.z._login())

    def test_login_fetches_token_when_missing(self):
        self.z._csrf_token = None
        self.z._fetch_csrf = MagicMock(return_value="fresh")
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        self.assertTrue(self.z._login())
        self.z._fetch_csrf.assert_called_once()
        self.assertEqual(self.z.session.post.call_args.kwargs["headers"], {"X-CSRF-Token": "fresh"})

    def test_login_fails_without_token(self):
        self.z._csrf_token = None
        self.z._fetch_csrf = MagicMock(return_value=None)
        self.z.session.post = MagicMock()
        self.assertFalse(self.z._login())
        self.z.session.post.assert_not_called()


class TestZoraxyEnsureAuth(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")

    def test_ensure_auth_true_when_session_is_valid(self):
        self.z.session.get = MagicMock(return_value=_response(200, True))
        self.z._login = MagicMock(return_value=True)
        self.assertTrue(self.z._ensure_auth())
        self.z._login.assert_not_called()
        call = self.z.session.get.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/auth/checkLogin")
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_ensure_auth_logs_in_when_session_is_invalid(self):
        self.z.session.get = MagicMock(return_value=_response(200, False))
        self.z._login = MagicMock(return_value=True)
        self.assertTrue(self.z._ensure_auth())
        self.z._login.assert_called_once()

    def test_ensure_auth_reports_failed_login(self):
        self.z.session.get = MagicMock(return_value=_response(200, False))
        self.z._login = MagicMock(return_value=False)
        self.assertFalse(self.z._ensure_auth())

    def test_ensure_auth_noauth_instance_without_credentials(self):
        z = ZoraxyProvider("http://zoraxy:8000", "", "")
        z.session.get = MagicMock(return_value=_response(200, True))
        z.session.post = MagicMock()
        self.assertTrue(z._ensure_auth())
        z.session.post.assert_not_called()

    def test_ensure_auth_fails_without_credentials_when_login_is_required(self):
        z = ZoraxyProvider("http://zoraxy:8000", "", "")
        z.session.get = MagicMock(return_value=_response(200, False))
        z.session.post = MagicMock()
        self.assertFalse(z._ensure_auth())
        z.session.post.assert_not_called()

    def test_ensure_auth_false_on_network_error(self):
        self.z.session.get = MagicMock(side_effect=requests.RequestException("down"))
        self.z.session.post = MagicMock(side_effect=requests.RequestException("down"))
        self.assertFalse(self.z._ensure_auth())


class TestZoraxyPost(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._ensure_auth = MagicMock(return_value=True)
        self.z._csrf_token = "stale"

    def test_post_retries_once_with_a_fresh_token_on_403(self):
        self.z._fetch_csrf = MagicMock(return_value="fresh")
        self.z.session.post = MagicMock(side_effect=[
            _response(403, _NO_JSON, "Forbidden - CSRF token invalid"),
            _response(200, "OK"),
        ])
        self.assertEqual(self.z._post("/api/proxy/toggle", {"ep": "a"}), (True, ""))
        self.assertEqual(self.z.session.post.call_count, 2)
        first, second = self.z.session.post.call_args_list
        self.assertEqual(first.kwargs["headers"], {"X-CSRF-Token": "stale"})
        self.assertEqual(second.kwargs["headers"], {"X-CSRF-Token": "fresh"})
        self.z._fetch_csrf.assert_called_once()

    def test_post_gives_up_after_a_second_403(self):
        self.z._fetch_csrf = MagicMock(return_value="fresh")
        self.z.session.post = MagicMock(side_effect=[
            _response(403, _NO_JSON, "Forbidden"),
            _response(403, _NO_JSON, "Forbidden"),
        ])
        ok, err = self.z._post("/api/proxy/toggle", {"ep": "a"})
        self.assertFalse(ok)
        self.assertEqual(err, "HTTP 403")
        self.assertEqual(self.z.session.post.call_count, 2)

    def test_post_reports_zoraxy_error_text(self):
        self.z.session.post = MagicMock(return_value=_response(200, {"error": "proxy rule not found"}))
        self.assertEqual(self.z._post("/api/proxy/del", {"ep": "a"}), (False, "proxy rule not found"))

    def test_post_treats_redirect_as_not_authenticated(self):
        self.z.session.post = MagicMock(return_value=_response(307, _NO_JSON))
        self.assertEqual(self.z._post("/api/proxy/del", {"ep": "a"}), (False, "not authenticated"))

    def test_post_rejects_non_json_body(self):
        self.z.session.post = MagicMock(return_value=_response(200, _NO_JSON, "<html>"))
        ok, err = self.z._post("/api/proxy/del", {"ep": "a"})
        self.assertFalse(ok)
        self.assertIn("non-JSON", err)

    def test_post_does_not_send_when_auth_fails(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        self.z.session.post = MagicMock()
        self.assertEqual(self.z._post("/api/proxy/del", {"ep": "a"}), (False, "authentication failed"))
        self.z.session.post.assert_not_called()

    def test_post_network_error_names_the_exception_when_message_is_empty(self):
        self.z.session.post = MagicMock(side_effect=requests.ReadTimeout())
        ok, err = self.z._post("/api/proxy/del", {"ep": "a"})
        self.assertFalse(ok)
        self.assertEqual(err, "ReadTimeout")


class TestZoraxyTestConnection(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")

    def test_test_connection_ok(self):
        self.z._ensure_auth = MagicMock(return_value=True)
        self.z.session.get = MagicMock(return_value=_response(200, []))
        self.assertTrue(self.z.test_connection())
        call = self.z.session.get.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/proxy/list")
        self.assertEqual(call.kwargs["params"], {"type": "host"})
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_test_connection_fails_when_list_is_a_redirect(self):
        self.z._ensure_auth = MagicMock(return_value=True)
        self.z.session.get = MagicMock(return_value=_response(307, _NO_JSON))
        self.assertFalse(self.z.test_connection())

    def test_test_connection_fails_on_auth_failure(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        self.z.session.get = MagicMock()
        self.assertFalse(self.z.test_connection())
        self.z.session.get.assert_not_called()


class TestZoraxyListHosts(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._ensure_auth = MagicMock(return_value=True)

    def _hosts(self, *rules):
        self.z.session.get = MagicMock(return_value=_response(200, list(rules)))
        return self.z.list_hosts()

    def test_list_hosts_keeps_the_stored_spelling_as_id_and_lowercases_domains(self):
        """The id names the config file on disk, so it must stay as Zoraxy spells it; the
        domains are what Vauxtra matches its own lower-case hostnames against."""
        hosts = self._hosts(_rule(RootOrMatchingDomain="App.Example.com",
                                  MatchingDomainAlias=["WWW.Example.com"]))
        self.assertEqual(hosts[0]["id"], "App.Example.com")
        self.assertEqual(hosts[0]["domains"], ["app.example.com", "www.example.com"])

    def test_list_hosts_normalises_a_rule(self):
        hosts = self._hosts(_rule())
        self.assertEqual(hosts, [{
            "id": "app.example.com",
            "domains": ["app.example.com", "www.example.com"],
            "target": "https://10.0.0.5:8443",
            "scheme": "https",
            "host": "10.0.0.5",
            "port": 8443,
            "ssl": True,
            "bypass_global_tls": True,
            "websocket": True,
            "cert_id": "_.example.com",
            "enabled": True,
            "tags": ["media"],
        }])
        call = self.z.session.get.call_args
        self.assertEqual(call.kwargs["params"], {"type": "host"})
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_list_hosts_scheme_follows_require_tls(self):
        rule = _rule(ActiveOrigins=[{"OriginIpOrDomain": "10.0.0.5:8080", "RequireTLS": False}])
        host = self._hosts(rule)[0]
        self.assertEqual(host["scheme"], "http")
        self.assertEqual(host["target"], "http://10.0.0.5:8080")

    def test_list_hosts_defaults_the_port_by_scheme(self):
        plain = _rule(ActiveOrigins=[{"OriginIpOrDomain": "backend.lan", "RequireTLS": False}])
        secure = _rule(ActiveOrigins=[{"OriginIpOrDomain": "backend.lan", "RequireTLS": True}])
        hosts = self._hosts(plain, secure)
        self.assertEqual((hosts[0]["host"], hosts[0]["port"]), ("backend.lan", 80))
        self.assertEqual((hosts[1]["host"], hosts[1]["port"]), ("backend.lan", 443))

    def test_list_hosts_parses_bracketed_ipv6_origin(self):
        rule = _rule(ActiveOrigins=[{"OriginIpOrDomain": "[fd00::5]:8080", "RequireTLS": False}])
        host = self._hosts(rule)[0]
        self.assertEqual((host["host"], host["port"]), ("fd00::5", 8080))
        self.assertEqual(host["target"], "http://[fd00::5]:8080")

    def test_list_hosts_ipv6_without_port_gets_the_default(self):
        rule = _rule(ActiveOrigins=[{"OriginIpOrDomain": "[fd00::5]", "RequireTLS": True}])
        host = self._hosts(rule)[0]
        self.assertEqual((host["host"], host["port"]), ("fd00::5", 443))

    def test_list_hosts_flags_disabled_rule_and_disabled_websocket(self):
        host = self._hosts(_rule(Disabled=True, DisableWebSocket=True))[0]
        self.assertFalse(host["enabled"])
        self.assertFalse(host["websocket"])

    def test_list_hosts_falls_back_to_inactive_origin(self):
        rule = _rule(ActiveOrigins=[],
                     InactiveOrigins=[{"OriginIpOrDomain": "10.0.0.9:3000", "RequireTLS": False}])
        host = self._hosts(rule)[0]
        self.assertEqual(host["target"], "http://10.0.0.9:3000")

    def test_list_hosts_without_any_origin_has_empty_target(self):
        host = self._hosts(_rule(ActiveOrigins=[], InactiveOrigins=None))[0]
        self.assertEqual((host["host"], host["port"], host["target"]), ("", 0, ""))

    def test_list_hosts_cert_id_none_without_preference(self):
        self.assertIsNone(self._hosts(_rule(TlsOptions=None))[0]["cert_id"])
        other = _rule(TlsOptions={"PreferredCertificate": {"other.example.com": "x"}})
        self.assertIsNone(self._hosts(other)[0]["cert_id"])

    def test_list_hosts_skips_root_and_vdir_rules(self):
        hosts = self._hosts(_rule(ProxyType=0, RootOrMatchingDomain="root"),
                            _rule(),
                            _rule(ProxyType=2, RootOrMatchingDomain="vdir"))
        self.assertEqual([h["id"] for h in hosts], ["app.example.com"])

    def test_list_hosts_returns_empty_on_auth_fail(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        self.z.session.get = MagicMock()
        self.assertEqual(self.z.list_hosts(), [])
        self.z.session.get.assert_not_called()

    def test_list_hosts_returns_empty_on_network_error(self):
        self.z.session.get = MagicMock(side_effect=requests.RequestException("down"))
        self.assertEqual(self.z.list_hosts(), [])

    def test_list_hosts_returns_empty_when_answer_is_not_a_list(self):
        self.z.session.get = MagicMock(return_value=_response(200, {"error": "nope"}))
        self.assertEqual(self.z.list_hosts(), [])


class TestZoraxyCreateHost(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._ensure_auth = MagicMock(return_value=True)
        self.z._csrf_token = "tok"
        # What /api/proxy/detail answers for a hostname Zoraxy does not know.
        self.z.session.get = MagicMock(return_value=_response(200, {"error": "proxy rule not found"}))

    def test_create_host_sends_every_form_field(self):
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        result = self.z.create_host("app.example.com", "10.0.0.5", 8080, "http", websocket=True)
        self.assertEqual(result["id"], "app.example.com")
        self.assertEqual(result["domain"], "app.example.com")
        self.assertEqual(result["target"], "http://10.0.0.5:8080")
        self.assertTrue(result["enabled"])
        call = self.z.session.post.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/proxy/add")
        self.assertEqual(call.kwargs["data"], {
            "type": "host",
            "rootname": "app.example.com",
            "ep": "10.0.0.5:8080",
            "tls": "false",
            "tlsval": "false",
            "bypassGlobalTLS": "false",
            "access": "default",
            "disableWebSocket": "false",
            "websocketTimeout": "0",
            "enableUtm": "true",
        })
        self.assertEqual(call.kwargs["headers"], {"X-CSRF-Token": "tok"})
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertNotIn("json", call.kwargs)
        self.assertEqual(self.z.session.post.call_count, 1)

    def test_create_host_https_upstream_sets_tls_flags(self):
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        self.z.create_host("app.example.com", "10.0.0.5", 8443, "https", websocket=False)
        data = self.z.session.post.call_args.kwargs["data"]
        self.assertEqual(data["tls"], "true")
        self.assertEqual(data["tlsval"], "true")
        self.assertEqual(data["disableWebSocket"], "true")

    def test_create_host_brackets_ipv6_upstream(self):
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        result = self.z.create_host("app.example.com", "fd00::5", 8080)
        self.assertEqual(self.z.session.post.call_args.kwargs["data"]["ep"], "[fd00::5]:8080")
        self.assertEqual(result["target"], "http://[fd00::5]:8080")

    def test_create_host_with_cert_sets_tls_config(self):
        self.z.session.post = MagicMock(side_effect=[_response(200, "OK"), _response(200, "OK")])
        result = self.z.create_host("app.example.com", "10.0.0.5", 8080, cert_id="_.example.com")
        self.assertEqual(result["cert_id"], "_.example.com")
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/add", "/api/proxy/setTlsConfig"])
        self.assertEqual(posts[1][1]["ep"], "app.example.com")
        self.assertEqual(json.loads(posts[1][1]["tlsConfig"]), {
            "DisableSNI": False,
            "DisableLegacyCertificateMatching": False,
            "EnableAutoHTTPS": False,
            "PreferredCertificate": {"app.example.com": "_.example.com"},
        })

    def test_create_host_without_cert_skips_tls_config(self):
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        self.z.create_host("app.example.com", "10.0.0.5", 8080, cert_id=None)
        self.assertEqual(self.z.session.post.call_count, 1)

    def test_create_host_keeps_the_host_when_cert_step_fails(self):
        self.z.session.post = MagicMock(side_effect=[
            _response(200, "OK"), _response(200, {"error": "certificate not found"}),
        ])
        result = self.z.create_host("app.example.com", "10.0.0.5", 8080, cert_id="missing")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "app.example.com")

    def test_create_host_checks_the_rule_does_not_exist_first(self):
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        self.z.create_host("app.example.com", "10.0.0.5", 8080)
        call = self.z.session.get.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/proxy/detail")
        self.assertEqual(call.kwargs["params"], {"type": "host", "epname": "app.example.com"})

    def test_create_host_duplicate_hostname_returns_none(self):
        """`/api/proxy/add` replaces an existing rule without a word; the check is ours."""
        self.z.session.get = MagicMock(return_value=_response(200, _rule()))
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        self.assertIsNone(self.z.create_host("app.example.com", "10.0.0.5", 8080))
        self.z.session.post.assert_not_called()

    def test_create_host_refuses_when_the_existence_check_does_not_answer(self):
        """Only "proxy rule not found" proves absence; anything else could hide a rule."""
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))
        for answer in (_response(307, _NO_JSON), _response(200, _NO_JSON, "<html>"),
                       _response(500, _NO_JSON, "boom")):
            self.z.session.get = MagicMock(return_value=answer)
            self.assertIsNone(self.z.create_host("app.example.com", "10.0.0.5", 8080))
        self.z.session.get = MagicMock(side_effect=requests.RequestException("down"))
        self.assertIsNone(self.z.create_host("app.example.com", "10.0.0.5", 8080))
        self.z.session.post.assert_not_called()

    def test_create_host_network_error_returns_none(self):
        self.z.session.post = MagicMock(side_effect=requests.RequestException("down"))
        self.assertIsNone(self.z.create_host("app.example.com", "10.0.0.5", 8080))

    def test_create_host_auth_fail_returns_none(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        self.z.session.post = MagicMock()
        self.assertIsNone(self.z.create_host("app.example.com", "10.0.0.5", 8080))
        self.z.session.post.assert_not_called()

    def test_create_host_empty_domain_returns_none(self):
        self.z.session.post = MagicMock()
        self.assertIsNone(self.z.create_host("  ", "10.0.0.5", 8080))
        self.z.session.post.assert_not_called()


class TestZoraxyUpdateHost(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._ensure_auth = MagicMock(return_value=True)
        self.z._csrf_token = "tok"
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))

    def _detail(self, rule):
        self.z.session.get = MagicMock(return_value=_response(200, rule))

    def test_update_host_reads_detail_by_hostname(self):
        self._detail(_rule())
        self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443, "https", True, "_.example.com")
        call = self.z.session.get.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/proxy/detail")
        self.assertEqual(call.kwargs["params"], {"type": "host", "epname": "app.example.com"})
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_update_host_is_a_noop_when_nothing_changed(self):
        self._detail(_rule())
        ok = self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443,
                                "https", True, "_.example.com")
        self.assertTrue(ok)
        self.z.session.post.assert_not_called()

    def test_update_host_missing_rule_returns_false(self):
        self._detail({"error": "proxy rule not found"})
        self.assertFalse(self.z.update_host("gone.example.com", "gone.example.com", "10.0.0.5", 80))
        self.z.session.post.assert_not_called()

    def test_update_host_rejects_non_string_id(self):
        self.z.session.get = MagicMock()
        self.assertFalse(self.z.update_host(12, "app.example.com", "10.0.0.5", 80))
        self.assertFalse(self.z.update_host("", "app.example.com", "10.0.0.5", 80))
        self.z.session.get.assert_not_called()

    def test_update_host_renames_and_keys_later_steps_on_the_new_name(self):
        self._detail(_rule())
        ok = self.z.update_host("app.example.com", "new.example.com", "10.0.0.5", 8443,
                                "https", True, "_.example.com")
        self.assertTrue(ok)
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/setHostname", "/api/proxy/setTlsConfig"])
        self.assertEqual(posts[0][1], {"oldHostname": "app.example.com", "newHostname": "new.example.com"})
        self.assertEqual(posts[1][1]["ep"], "new.example.com")
        self.assertEqual(json.loads(posts[1][1]["tlsConfig"])["PreferredCertificate"],
                         {"new.example.com": "_.example.com"})

    def test_update_host_does_not_rename_when_only_the_case_differs(self):
        """Zoraxy looks rules up lower-cased: renaming "App.Example.com" to
        "app.example.com" is refused as "already exists". The stored spelling stays the
        key of every later step, because the config file on disk carries that spelling."""
        self._detail(_rule(RootOrMatchingDomain="App.Example.com",
                           TlsOptions={**_RULE["TlsOptions"],
                                       "PreferredCertificate": {"App.Example.com": "_.example.com"}}))
        ok = self.z.update_host("App.Example.com", "app.example.com", "10.0.0.5", 8443,
                                "https", True, "new.example.com")
        self.assertTrue(ok)
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/setTlsConfig"])
        self.assertEqual(posts[0][1]["ep"], "App.Example.com")
        self.assertEqual(json.loads(posts[0][1]["tlsConfig"])["PreferredCertificate"],
                         {"App.Example.com": "new.example.com"})

    def test_update_host_is_a_noop_when_only_the_case_differs_and_nothing_else(self):
        self._detail(_rule(RootOrMatchingDomain="App.Example.com",
                           TlsOptions={**_RULE["TlsOptions"],
                                       "PreferredCertificate": {"App.Example.com": "_.example.com"}}))
        ok = self.z.update_host("App.Example.com", "app.example.com", "10.0.0.5", 8443,
                                "https", True, "_.example.com")
        self.assertTrue(ok)
        self.z.session.post.assert_not_called()

    def test_update_host_updates_the_upstream(self):
        self._detail(_rule())
        ok = self.z.update_host("app.example.com", "app.example.com", "10.0.0.6", 9000,
                                "http", True, "_.example.com")
        self.assertTrue(ok)
        posts = _posted(self.z)
        self.assertEqual(len(posts), 1)
        path, data = posts[0]
        self.assertEqual(path, "/api/proxy/upstream/update")
        self.assertEqual(data["ep"], "app.example.com")
        self.assertEqual(data["origin"], "10.0.0.5:8443")
        self.assertEqual(data["active"], "true")
        self.assertEqual(json.loads(data["payload"]), {
            "OriginIpOrDomain": "10.0.0.6:9000",
            "RequireTLS": False,
            "SkipCertValidations": False,
        })

    def test_update_host_scheme_change_alone_updates_the_upstream(self):
        self._detail(_rule())
        self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443,
                           "http", True, "_.example.com")
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/upstream/update"])
        payload = json.loads(posts[0][1]["payload"])
        self.assertEqual(payload["OriginIpOrDomain"], "10.0.0.5:8443")
        self.assertFalse(payload["RequireTLS"])

    def test_update_host_adds_an_upstream_when_the_rule_has_none(self):
        self._detail(_rule(ActiveOrigins=[], InactiveOrigins=[]))
        ok = self.z.update_host("app.example.com", "app.example.com", "10.0.0.6", 9000,
                                "https", True, "_.example.com")
        self.assertTrue(ok)
        posts = _posted(self.z)
        self.assertEqual(posts, [("/api/proxy/upstream/add", {
            "ep": "app.example.com",
            "origin": "10.0.0.6:9000",
            "tls": "true",
            "tlsval": "true",
            "bpwsorg": "false",
            "active": "true",
        })])

    def test_update_host_websocket_edit_mirrors_every_field_the_handler_reads(self):
        self._detail(_rule(
            UseStickySession=True,
            BypassGlobalTLS=True,
            EnableConnectSupport=True,
            DisableUptimeMonitor=True,
            UptimeMonitorURI="/healthz",
            DisableAutoFallback=True,
            AuthenticationProvider={"AuthMethod": 4, "BasicAuthCredentials": [{"Username": "u"}]},
            RequireRateLimit=True,
            RateLimit=200,
            RequireCaptcha=True,
            CaptchaConfig={
                "Provider": 1, "SiteKey": "site", "SecretKey": "sec", "SessionDuration": 600,
                "RecaptchaVersion": "v3", "RecaptchaScore": 0.7,
                "ProtectedPathPrefixes": ["/admin", "/api"], "ExceptionRules": [],
            },
            DisableChunkedTransferEncoding=True,
            ForceHTTP11=True,
            WebsocketTimeout=120,
            EnableTimeoutRefreshOnActivity=True,
            DisableLogging=True,
            DisableStatisticCollection=True,
            BlockCommonExploits=True,
            BlockAICrawlers=True,
            MitigationAction=2,
            Tags=["media", "lan"],
        ))
        ok = self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443,
                                "https", False, "_.example.com")
        self.assertTrue(ok)
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/edit"])
        self.assertEqual(posts[0][1], {
            "type": "host",
            "rootname": "app.example.com",
            "ss": "true",
            "bpgtls": "true",
            "enableConnectSupport": "true",
            "dutm": "true",
            "utmURI": "/healthz",
            "dAutoFallback": "true",
            "authprovider": "4",
            "rate": "true",
            "ratenum": "200",
            "captcha": "true",
            "dChunkedEnc": "true",
            "forceHTTP11": "true",
            "disableWebSocket": "true",
            "websocketTimeout": "120",
            "enableTimeoutRefreshOnActivity": "true",
            "dLogging": "true",
            "dStatisticCollection": "true",
            "blockCommonExploits": "true",
            "blockAICrawlers": "true",
            "mitigationAction": "2",
            "tags": "media,lan",
            "captchaProvider": "1",
            "captchaSiteKey": "site",
            "captchaSecretKey": "sec",
            "captchaSessionDuration": "600",
            "captchaRecaptchaVersion": "v3",
            "captchaRecaptchaScore": "0.7",
            "captchaPathPrefixes": "/admin,/api",
        })

    def test_update_host_websocket_edit_on_a_plain_rule_uses_zero_values(self):
        self._detail(_rule(BypassGlobalTLS=False, Tags=[], AuthenticationProvider=None))
        self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443,
                           "https", False, "_.example.com")
        data = _posted(self.z)[0][1]
        self.assertEqual(data["disableWebSocket"], "true")
        self.assertEqual(data["authprovider"], "0")
        self.assertEqual(data["rate"], "false")
        self.assertEqual(data["ratenum"], "0")
        self.assertEqual(data["captcha"], "false")
        self.assertEqual(data["tags"], "")
        self.assertNotIn("captchaSiteKey", data)
        self.assertNotIn("tls", data)

    def test_update_host_enables_websocket(self):
        self._detail(_rule(DisableWebSocket=True))
        self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443,
                           "https", True, "_.example.com")
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/edit"])
        self.assertEqual(posts[0][1]["disableWebSocket"], "false")

    def test_update_host_changes_the_certificate_and_keeps_tls_flags(self):
        self._detail(_rule(TlsOptions={
            "DisableSNI": True, "DisableLegacyCertificateMatching": True, "EnableAutoHTTPS": False,
            "PreferredCertificate": {"app.example.com": "_.example.com"},
        }))
        ok = self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443,
                                "https", True, "app.example.com")
        self.assertTrue(ok)
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/setTlsConfig"])
        self.assertEqual(posts[0][1]["ep"], "app.example.com")
        self.assertEqual(json.loads(posts[0][1]["tlsConfig"]), {
            "DisableSNI": True,
            "DisableLegacyCertificateMatching": True,
            "EnableAutoHTTPS": False,
            "PreferredCertificate": {"app.example.com": "app.example.com"},
        })

    def test_update_host_clears_the_certificate_when_none(self):
        self._detail(_rule())
        self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 8443, "https", True, None)
        posts = _posted(self.z)
        self.assertEqual([p for p, _ in posts], ["/api/proxy/setTlsConfig"])
        self.assertEqual(json.loads(posts[0][1]["tlsConfig"])["PreferredCertificate"], {})

    def test_update_host_runs_every_needed_step_in_order(self):
        self._detail(_rule())
        ok = self.z.update_host("app.example.com", "new.example.com", "10.0.0.6", 9000,
                                "http", False, "other")
        self.assertTrue(ok)
        self.assertEqual([p for p, _ in _posted(self.z)], [
            "/api/proxy/setHostname",
            "/api/proxy/upstream/update",
            "/api/proxy/edit",
            "/api/proxy/setTlsConfig",
        ])
        posts = _posted(self.z)
        self.assertEqual(posts[1][1]["ep"], "new.example.com")
        self.assertEqual(posts[2][1]["rootname"], "new.example.com")
        self.assertEqual(posts[3][1]["ep"], "new.example.com")

    def test_update_host_returns_false_when_a_step_fails(self):
        self._detail(_rule())
        self.z.session.post = MagicMock(return_value=_response(200, {"error": "upstream not found"}))
        ok = self.z.update_host("app.example.com", "app.example.com", "10.0.0.6", 9000,
                                "http", True, "_.example.com")
        self.assertFalse(ok)

    def test_update_host_stops_after_a_failed_rename(self):
        self._detail(_rule())
        self.z.session.post = MagicMock(
            return_value=_response(200, {"error": "Endpoint with this hostname already exists"})
        )
        ok = self.z.update_host("app.example.com", "taken.example.com", "10.0.0.6", 9000,
                                "http", False, "other")
        self.assertFalse(ok)
        self.assertEqual(self.z.session.post.call_count, 1)

    def test_update_host_returns_false_on_auth_failure(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        self.z.session.get = MagicMock()
        self.assertFalse(self.z.update_host("app.example.com", "app.example.com", "10.0.0.5", 80))
        self.z.session.get.assert_not_called()


class TestZoraxyToggleAndDelete(unittest.TestCase):

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._ensure_auth = MagicMock(return_value=True)
        self.z._csrf_token = "tok"
        self.z.session.post = MagicMock(return_value=_response(200, "OK"))

    def test_enable_host_payload(self):
        self.assertTrue(self.z.toggle_host("app.example.com", True))
        self.assertEqual(_posted(self.z), [("/api/proxy/toggle", {"ep": "app.example.com", "enable": "true"})])

    def test_disable_host_payload(self):
        self.assertTrue(self.z.toggle_host("app.example.com", False))
        self.assertEqual(_posted(self.z), [("/api/proxy/toggle", {"ep": "app.example.com", "enable": "false"})])

    def test_toggle_returns_false_on_zoraxy_error(self):
        self.z.session.post = MagicMock(return_value=_response(200, {"error": "proxy rule not found"}))
        self.assertFalse(self.z.toggle_host("gone.example.com", True))

    def test_toggle_rejects_non_string_id(self):
        self.assertFalse(self.z.toggle_host(7, True))
        self.z.session.post.assert_not_called()

    def test_delete_host_payload(self):
        self.assertTrue(self.z.delete_host("app.example.com"))
        self.assertEqual(_posted(self.z), [("/api/proxy/del", {"ep": "app.example.com"})])

    def test_delete_host_returns_false_on_zoraxy_error(self):
        self.z.session.post = MagicMock(return_value=_response(200, {"error": "proxy rule not found"}))
        self.assertFalse(self.z.delete_host("gone.example.com"))

    def test_delete_host_rejects_non_string_ids(self):
        for bad in (7, None, "", "   ", ["app.example.com"]):
            with self.subTest(host_id=bad):
                self.assertFalse(self.z.delete_host(bad))
        self.z.session.post.assert_not_called()

    def test_delete_host_network_error(self):
        self.z.session.post = MagicMock(side_effect=requests.RequestException("down"))
        self.assertFalse(self.z.delete_host("app.example.com"))

    def test_delete_host_auth_fail(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        self.assertFalse(self.z.delete_host("app.example.com"))
        self.z.session.post.assert_not_called()


class TestZoraxyCertificates(unittest.TestCase):

    _CERTS = [
        {"Domain": "*.example.com", "Filename": "_.example.com",
         "LastModifiedDate": "2026-08-01 10:00:00", "ExpireDate": "2027-01-01 12:30:00",
         "RemainingDays": 120, "UseDNS": True, "IsFallback": False},
        {"Domain": "app.example.com", "Filename": "app.example.com",
         "LastModifiedDate": "2026-08-01 10:00:00", "ExpireDate": "Unknown",
         "RemainingDays": -1, "UseDNS": False, "IsFallback": True},
    ]

    def setUp(self):
        self.z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        self.z._ensure_auth = MagicMock(return_value=True)

    def test_get_certificates_normalises_entries(self):
        self.z.session.get = MagicMock(return_value=_response(200, self._CERTS))
        certs = self.z.get_certificates()
        self.assertEqual(certs, [
            {"id": "_.example.com", "nice_name": "_.example.com", "domains": ["*.example.com"],
             "expires_on": "2027-01-01T12:30:00Z", "remaining_days": 120,
             "use_dns": True, "is_fallback": False},
            {"id": "app.example.com", "nice_name": "app.example.com", "domains": ["app.example.com"],
             "expires_on": "", "remaining_days": -1, "use_dns": False, "is_fallback": True},
        ])
        call = self.z.session.get.call_args
        self.assertEqual(call.args[0], "http://zoraxy:8000/api/cert/list")
        self.assertEqual(call.kwargs["params"], {"date": "true"})
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_get_certificates_expires_on_is_parsed_by_the_certificates_route(self):
        self.z.session.get = MagicMock(return_value=_response(200, self._CERTS))
        raw = self.z.get_certificates()[0]["expires_on"]
        self.assertEqual(certificates_api._parse_expiry(raw), datetime.datetime(2027, 1, 1, 12, 30))

    def test_get_certificates_adds_hostname_filename_as_domain(self):
        self.z.session.get = MagicMock(return_value=_response(200, [
            {"Domain": "", "Filename": "vault.example.com", "ExpireDate": "Unknown"},
            {"Domain": "*.example.com", "Filename": "wildcard", "ExpireDate": "Unknown"},
            {"Domain": "old.example.com", "Filename": "new.example.com", "ExpireDate": "Unknown"},
        ]))
        domains = [c["domains"] for c in self.z.get_certificates()]
        self.assertEqual(domains, [
            ["vault.example.com"],
            ["*.example.com"],
            ["old.example.com", "new.example.com"],
        ])

    def test_get_certificates_translates_wildcard_filename(self):
        self.z.session.get = MagicMock(return_value=_response(200, [
            {"Domain": "", "Filename": "_.example.com", "ExpireDate": "Unknown"},
        ]))
        self.assertEqual(self.z.get_certificates()[0]["domains"], ["*.example.com"])

    def test_get_certificates_handles_plain_filename_list(self):
        self.z.session.get = MagicMock(return_value=_response(200, ["a.example.com", "misc"]))
        certs = self.z.get_certificates()
        self.assertEqual([c["id"] for c in certs], ["a.example.com", "misc"])
        self.assertEqual(certs[0]["domains"], ["a.example.com"])
        self.assertEqual(certs[1]["domains"], [])
        self.assertEqual(certs[0]["expires_on"], "")

    def test_get_certificates_skips_junk_entries(self):
        self.z.session.get = MagicMock(return_value=_response(200, [None, 3, {"Domain": "x"}]))
        self.assertEqual(self.z.get_certificates(), [])

    def test_get_certificates_returns_empty_when_answer_is_not_a_list(self):
        self.z.session.get = MagicMock(return_value=_response(200, {"error": "nope"}))
        self.assertEqual(self.z.get_certificates(), [])

    def test_get_certificates_raises_on_auth_failure(self):
        self.z._ensure_auth = MagicMock(return_value=False)
        with self.assertRaises(RuntimeError):
            self.z.get_certificates()

    def test_get_certificates_raises_on_redirect(self):
        self.z.session.get = MagicMock(return_value=_response(307, _NO_JSON))
        with self.assertRaises(RuntimeError):
            self.z.get_certificates()

    def _with_certs(self, *certs):
        self.z.get_certificates = MagicMock(return_value=list(certs))

    def test_find_best_certificate_exact_match(self):
        self._with_certs(
            {"id": "_.example.com", "nice_name": "_.example.com", "domains": ["*.example.com"]},
            {"id": "app.example.com", "nice_name": "app.example.com", "domains": ["app.example.com"]},
        )
        self.assertEqual(self.z.find_best_certificate("app.example.com"), "app.example.com")

    def test_find_best_certificate_wildcard_match(self):
        self._with_certs(
            {"id": "_.example.com", "nice_name": "_.example.com", "domains": ["*.example.com"]},
        )
        self.assertEqual(self.z.find_best_certificate("vault.example.com"), "_.example.com")
        self.assertEqual(self.z.find_best_certificate("VAULT.example.com."), "_.example.com")

    def test_find_best_certificate_rejects_deeper_label_for_a_wildcard(self):
        self._with_certs(
            {"id": "_.example.com", "nice_name": "_.example.com", "domains": ["*.example.com"]},
        )
        self.assertIsNone(self.z.find_best_certificate("a.b.example.com"))

    def test_find_best_certificate_returns_none_when_nothing_covers_the_host(self):
        self._with_certs(
            {"id": "_.other.org", "nice_name": "_.other.org", "domains": ["*.other.org"]},
        )
        self.assertIsNone(self.z.find_best_certificate("app.example.com"))
        self.assertIsNone(self.z.find_best_certificate(""))

    def test_find_best_certificate_returns_none_without_certs(self):
        self._with_certs()
        self.assertIsNone(self.z.find_best_certificate("app.example.com"))


class TestZoraxyFactory(unittest.TestCase):

    def test_provider_type_is_registered_with_certificates_capability(self):
        meta = PROVIDER_TYPES["zoraxy"]
        self.assertEqual(meta["category"], "proxy")
        self.assertTrue(meta["available"])
        self.assertEqual(meta["capabilities"], {
            "proxy": True,
            "dns": False,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
            "certificates": True,
        })
        self.assertEqual(meta["placeholder_url"], "http://192.168.1.10:8000")
        self.assertEqual(meta["user_label"], "Username")
        self.assertEqual(meta["pass_label"], "Password")
        self.assertEqual(len(meta["guided_steps"]), 3)
        self.assertEqual(list(PROVIDER_TYPES).index("zoraxy"), list(PROVIDER_TYPES).index("npm") + 1)

    def test_npm_declares_certificates_too(self):
        self.assertTrue(PROVIDER_TYPES["npm"]["capabilities"]["certificates"])

    def test_certificate_provider_types_follow_the_capability(self):
        types = certificate_provider_types()
        self.assertIn("npm", types)
        self.assertIn("zoraxy", types)
        self.assertNotIn("adguard", types)
        self.assertNotIn("traefik", types)

    def test_create_provider_builds_a_zoraxy_provider_from_a_row(self):
        row = {
            "type": "zoraxy", "url": "http://zoraxy:8000/", "username": "vauxtra",
            "password": encrypt_secret("pw"), "extra": None,
        }
        provider = create_provider(row)
        self.assertIsInstance(provider, ZoraxyProvider)
        self.assertEqual(provider.base_url, "http://zoraxy:8000")
        self.assertEqual(provider.username, "vauxtra")
        self.assertEqual(provider.password, "pw")


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _FakeCertProvider:
    def __init__(self, ptype: str):
        self.ptype = ptype

    def get_certificates(self):
        return [{"id": f"{self.ptype}-cert", "nice_name": self.ptype, "domains": [],
                 "expires_on": "2027-01-01T00:00:00Z"}]


class TestCertificatesRouteCoversZoraxy(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db
        self._saved = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "zoraxy.certs.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()
        with models.get_db_ctx() as conn:
            for name, ptype in (("npm-a", "npm"), ("zoraxy-a", "zoraxy"), ("adguard-a", "adguard")):
                conn.execute(
                    "INSERT INTO providers (name, type, url, username, password) VALUES (?, ?, ?, '', '')",
                    (name, ptype, "http://x"),
                )
            conn.execute(
                "INSERT INTO providers (name, type, url, username, password, enabled) "
                "VALUES ('zoraxy-off', 'zoraxy', 'http://y', '', '', 0)"
            )
            conn.commit()

    def tearDown(self):
        import app.db as _app_db
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._saved
        self._tmpdir.cleanup()

    def test_list_certificates_includes_zoraxy_and_skips_non_certificate_providers(self):
        with patch.object(certificates_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(certificates_api, "create_provider", lambda row: _FakeCertProvider(row["type"])):
            result = certificates_api.list_certificates(_request("GET", "/api/certificates"))
        self.assertEqual(sorted(c["provider_name"] for c in result), ["npm-a", "zoraxy-a"])

    def test_certificate_expiry_includes_zoraxy(self):
        with patch.object(certificates_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(certificates_api, "create_provider", lambda row: _FakeCertProvider(row["type"])):
            result = certificates_api.certificate_expiry(_request("GET", "/api/certificates/expiry"))
        names = sorted(c["provider_name"] for c in result["certificates"])
        self.assertEqual(names, ["npm-a", "zoraxy-a"])
        self.assertTrue(all(c["days_remaining"] is not None for c in result["certificates"]))

    def test_both_routes_spell_domains_the_way_the_frontend_reads_them(self):
        """The Dashboard dereferences `domain_names`; the providers answer `domains`."""
        def _provider(_row):
            fake = _FakeCertProvider("zoraxy")
            fake.get_certificates = lambda: [{"id": "_.example.com", "nice_name": "_.example.com",
                                              "domains": ["*.example.com"],
                                              "expires_on": "2027-01-01T00:00:00Z"}]
            return fake
        with patch.object(certificates_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(certificates_api, "create_provider", _provider):
            listed = certificates_api.list_certificates(_request("GET", "/api/certificates"))
            expiry = certificates_api.certificate_expiry(_request("GET", "/api/certificates/expiry"))
        for cert in [*listed, *expiry["certificates"]]:
            self.assertEqual(cert["domain_names"], ["*.example.com"])
            self.assertEqual(cert["domains"], ["*.example.com"])


class TestSchedulerCertExpiryAlertsCoverZoraxy(unittest.TestCase):
    """The scheduler's expiry scan runs on the provider's real `expires_on`, end to end."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db
        self._saved = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "zoraxy.scheduler.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()
        scheduler._cert_alert_state.clear()
        with models.get_db_ctx() as conn:
            conn.execute(
                "INSERT INTO providers (name, type, url, username, password) "
                "VALUES ('zoraxy-a', 'zoraxy', 'http://zoraxy:8000', 'vauxtra', ?)",
                (encrypt_secret("secret"),),
            )
            conn.commit()

    def tearDown(self):
        import app.db as _app_db
        scheduler._cert_alert_state.clear()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._saved
        self._tmpdir.cleanup()

    def _zoraxy_with_cert_expiring_in(self, days: int) -> ZoraxyProvider:
        expire = datetime.datetime.utcnow() + datetime.timedelta(days=days, hours=1)
        z = ZoraxyProvider("http://zoraxy:8000", "vauxtra", "secret")
        z._ensure_auth = MagicMock(return_value=True)
        z.session.get = MagicMock(return_value=_response(200, [
            {"Domain": "*.example.com", "Filename": "_.example.com",
             "LastModifiedDate": "2026-08-01 10:00:00",
             "ExpireDate": expire.strftime("%Y-%m-%d %H:%M:%S"),
             "RemainingDays": days, "UseDNS": True, "IsFallback": False},
        ]))
        return z

    def _cert_expiry_logs(self, conn) -> list:
        return conn.execute(
            "SELECT level, message FROM logs WHERE message LIKE '[CertExpiry]%'"
        ).fetchall()

    def test_scheduler_logs_a_critical_alert_for_a_zoraxy_certificate_expiring_soon(self):
        with patch.object(scheduler, "create_provider", lambda _row: self._zoraxy_with_cert_expiring_in(5)), \
             models.get_db_ctx() as conn:
            scheduler._run_cert_expiry_alerts(conn)
            conn.commit()
            rows = self._cert_expiry_logs(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], "error")
        self.assertIn("CRITICAL: '_.example.com' (ID _.example.com) expires in 5 day(s)", rows[0]["message"])

    def test_scheduler_stays_quiet_for_a_zoraxy_certificate_with_time_left(self):
        with patch.object(scheduler, "create_provider", lambda _row: self._zoraxy_with_cert_expiring_in(120)), \
             models.get_db_ctx() as conn:
            scheduler._run_cert_expiry_alerts(conn)
            conn.commit()
            rows = self._cert_expiry_logs(conn)
        self.assertEqual(rows, [])


class _HostnameKeyedProvider:
    """A proxy whose host ids are hostnames, the way Zoraxy's and Cloudflare Tunnel's are.

    Records every call; `update_host` renames the rule and answers True, like the real
    provider does once `setHostname` succeeded.
    """

    HOST_ID_IS_HOSTNAME = True

    def __init__(self, calls: list, hosts: dict | None = None):
        self._calls = calls
        self.hosts = hosts if hosts is not None else {}

    def _record(self, name, *args):
        self._calls.append((name, *args))

    def list_hosts(self):
        return [{"id": name, "domains": [name.lower()], "target": t} for name, t in self.hosts.items()]

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._record("create_host", domain)
        self.hosts[domain] = f"{scheme}://{ip}:{port}"
        return {"id": domain, "domain": domain}

    def update_host(self, host_id, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        self._record("update_host", host_id, domain)
        if host_id not in self.hosts:
            return False
        target = self.hosts.pop(host_id)
        self.hosts[domain] = target
        return True

    def delete_host(self, host_id):
        self._record("delete_host", host_id)
        return self.hosts.pop(host_id, None) is not None

    def toggle_host(self, host_id, enabled):
        self._record("toggle_host", host_id, enabled)
        return host_id in self.hosts

    def find_best_certificate(self, _domain):
        return None


class TestServiceRenameOnHostnameKeyedProxy(unittest.TestCase):
    """Renaming a service on Zoraxy renames its rule, so the stored id must follow.

    The services route used to keep the old hostname in `npm_host_id` after a successful
    rename, and every later disable, push and delete went looking for a rule that the
    rename had just removed.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db
        self._saved = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "zoraxy.rename.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        self.calls: list = []
        self.proxy = _HostnameKeyedProvider(self.calls, {"app.example.com": "http://10.0.0.9:8080"})
        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: self.proxy),
            patch.object(sync_api, "create_provider", lambda _row: self.proxy),
        ]
        for p in self._patchers:
            p.start()

        with models.get_db_ctx() as conn:
            conn.execute(
                "INSERT INTO providers (id, name, type, url, username, password, extra, enabled) "
                "VALUES (2, 'Zoraxy', 'zoraxy', 'http://zoraxy:8000', 'vauxtra', ?, '{}', 1)",
                (encrypt_secret("secret"),),
            )
            cur = conn.execute(
                """INSERT INTO services
                     (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                      enabled, expose_mode, proxy_provider_id, npm_host_id, public_target_mode)
                   VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'proxy_dns',
                           2, 'app.example.com', 'manual')""",
            )
            self.sid = cur.lastrowid
            conn.commit()

    def tearDown(self):
        import app.db as _app_db
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._saved
        self._tmpdir.cleanup()

    @staticmethod
    def _payload(subdomain: str, *, enabled: bool = True):
        return services_api.ServiceIn(
            subdomain=subdomain, domain="example.com", target_ip="10.0.0.9", target_port=8080,
            forward_scheme="http", websocket=False, enabled=enabled, expose_mode="proxy_dns",
            proxy_provider_id=2, public_target_mode="manual",
        )

    def _stored_host_id(self):
        with models.get_db_ctx() as conn:
            return conn.execute("SELECT npm_host_id FROM services WHERE id=?", (self.sid,)).fetchone()[0]

    def _service_row(self):
        with models.get_db_ctx() as conn:
            return conn.execute("SELECT * FROM services WHERE id=?", (self.sid,)).fetchone()

    def test_rename_stores_the_new_hostname_as_host_id(self):
        services_api.update_service(self.sid, _request("PUT"), self._payload("new"))
        self.assertIn(("update_host", "app.example.com", "new.example.com"), self.calls)
        self.assertEqual(self._stored_host_id(), "new.example.com")
        self.assertEqual(list(self.proxy.hosts), ["new.example.com"])

    def test_disable_after_rename_addresses_the_renamed_rule(self):
        services_api.update_service(self.sid, _request("PUT"), self._payload("new"))
        self.calls.clear()
        services_api.update_service(self.sid, _request("PUT"), self._payload("new", enabled=False))
        self.assertIn(("toggle_host", "new.example.com", False), self.calls)
        self.assertNotIn(("toggle_host", "app.example.com", False), self.calls)

    def test_rename_and_disable_in_one_request_addresses_the_renamed_rule(self):
        services_api.update_service(self.sid, _request("PUT"), self._payload("new", enabled=False))
        self.assertIn(("update_host", "app.example.com", "new.example.com"), self.calls)
        self.assertIn(("toggle_host", "new.example.com", False), self.calls)
        self.assertEqual(self._stored_host_id(), "new.example.com")

    def test_withdraw_after_rename_deletes_the_renamed_rule(self):
        services_api.update_service(self.sid, _request("PUT"), self._payload("new"))
        self.calls.clear()
        with models.get_db_ctx() as conn:
            errors = sync_api.withdraw_service_routes(conn, self._service_row(), self.sid)
        self.assertEqual(errors, [])
        self.assertIn(("delete_host", "new.example.com"), self.calls)
        self.assertEqual(self.proxy.hosts, {})

    def test_withdraw_ignores_a_stale_host_id_left_by_an_older_rename(self):
        """A row written before the fix still says "app.example.com" after the rename."""
        self.proxy.hosts = {"new.example.com": "http://10.0.0.9:8080"}
        with models.get_db_ctx() as conn:
            conn.execute("UPDATE services SET subdomain='new' WHERE id=?", (self.sid,))
            conn.commit()
            errors = sync_api.withdraw_service_routes(conn, self._service_row(), self.sid)
        self.assertEqual(errors, [])
        self.assertIn(("delete_host", "new.example.com"), self.calls)

    def test_push_heals_a_stale_host_id_left_by_an_older_rename(self):
        self.proxy.hosts = {"new.example.com": "http://10.0.0.9:8080"}
        with models.get_db_ctx() as conn:
            conn.execute("UPDATE services SET subdomain='new' WHERE id=?", (self.sid,))
            conn.commit()
            result = sync_api._push_service_row(conn, self._service_row(), self.sid)
            conn.commit()
        self.assertTrue(result.get("ok"), result)
        self.assertIn(("update_host", "new.example.com", "new.example.com"), self.calls)
        self.assertNotIn(("update_host", "app.example.com", "new.example.com"), self.calls)
        self.assertEqual(self._stored_host_id(), "new.example.com")

    def test_numeric_ids_are_left_alone_by_the_stale_check(self):
        """NPM's ids survive a rename: the hostname check is for hostname-keyed providers only."""
        self.assertEqual(sync_api._stored_host_id(False, {"npm_host_id": 77}, "new.example.com"), 77)
        self.assertEqual(sync_api._stored_host_id(True, {"npm_host_id": "App.Example.com"},
                                                  "app.example.com"), "App.Example.com")
        self.assertIsNone(sync_api._stored_host_id(True, {"npm_host_id": "app.example.com"},
                                                   "new.example.com"))

    def test_hostname_keyed_is_read_from_the_provider_or_its_registered_class(self):
        """A test double that declares nothing is judged by the class its row would build."""
        class _Silent:
            pass
        self.assertTrue(host_id_is_hostname(self.proxy, "npm"))
        self.assertTrue(host_id_is_hostname(_Silent(), "zoraxy"))
        self.assertTrue(host_id_is_hostname(_Silent(), "cloudflare_tunnel"))
        self.assertFalse(host_id_is_hostname(_Silent(), "npm"))
        self.assertFalse(host_id_is_hostname(_Silent(), "no-such-type"))
        self.assertTrue(host_id_is_hostname(create_provider(
            {"type": "zoraxy", "url": "http://z", "username": "", "password": encrypt_secret("")}), "zoraxy"))

    def test_find_host_id_matches_hostnames_case_insensitively(self):
        self.proxy.hosts = {"App.Example.com": "http://10.0.0.9:8080"}
        self.assertEqual(sync_api._find_host_id(self.proxy, "app.example.com"), "App.Example.com")
        self.assertIsNone(sync_api._find_host_id(self.proxy, "other.example.com"))


if __name__ == "__main__":
    unittest.main()
