"""Unit tests for CloudflareTunnelProvider — all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock

import requests

from app.providers.cloudflare_tunnel import CloudflareTunnelProvider


def _response(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.raise_for_status = MagicMock(
        side_effect=None if status_code < 400 else requests.HTTPError
    )
    return r


def _cf_ok(result) -> dict:
    """Build a Cloudflare-style success response payload."""
    return {"success": True, "result": result, "errors": []}


def _cf_err(msg: str = "error") -> dict:
    return {"success": False, "result": None, "errors": [{"message": msg}]}


class TestCFTunnelInit(unittest.TestCase):

    def test_api_url_defaults(self):
        p = CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tid"})
        self.assertIn("cloudflare.com", p.api_url)

    def test_api_url_appends_v4(self):
        p = CloudflareTunnelProvider("https://api.cloudflare.com", "acc123", "tok", {})
        self.assertIn("/client/v4", p.api_url)

    def test_tunnel_id_loaded_from_extra(self):
        p = CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tunnel-uuid"})
        self.assertEqual(p.tunnel_id, "tunnel-uuid")

    def test_auth_header_set(self):
        p = CloudflareTunnelProvider("", "acc123", "mytoken", {})
        self.assertIn("Bearer mytoken", p.session.headers.get("Authorization", ""))


class TestCFTunnelResolveId(unittest.TestCase):

    def setUp(self):
        self.p = CloudflareTunnelProvider("", "acc123", "tok", {})

    def _mock_request(self, result):
        """Mock the underlying _request helper."""
        self.p._request = MagicMock(return_value=result)

    def test_resolve_uses_configured_id(self):
        self.p.tunnel_id = "fixed-id"
        self.p._request = MagicMock()
        result = self.p._resolve_tunnel_id()
        self.assertEqual(result, "fixed-id")
        self.p._request.assert_not_called()

    def test_resolve_auto_detects_single_tunnel(self):
        self.p.tunnel_id = ""
        self._mock_request([{"id": "auto-detected"}])
        result = self.p._resolve_tunnel_id()
        self.assertEqual(result, "auto-detected")
        self.assertEqual(self.p.tunnel_id, "auto-detected")

    def test_resolve_returns_empty_for_multiple_tunnels(self):
        self.p.tunnel_id = ""
        self._mock_request([{"id": "t1", "name": "T1"}, {"id": "t2", "name": "T2"}])
        result = self.p._resolve_tunnel_id()
        self.assertEqual(result, "")

    def test_resolve_returns_empty_when_api_fails(self):
        self.p.tunnel_id = ""
        self._mock_request(None)
        result = self.p._resolve_tunnel_id()
        self.assertEqual(result, "")


class TestCFTunnelListHosts(unittest.TestCase):

    def setUp(self):
        self.p = CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tid"})
        self.p._get_configuration = MagicMock(return_value={
            "ingress": [
                {"hostname": "app.example.com", "service": "http://192.168.1.10:3000", "originRequest": {}},
                {"hostname": "api.example.com", "service": "https://192.168.1.20:8443", "originRequest": {}},
                # Fallback rule (no hostname)
                {"service": "http_status:404"},
            ]
        })

    def test_list_hosts_returns_named_rules(self):
        hosts = self.p.list_hosts()
        self.assertEqual(len(hosts), 2)

    def test_list_hosts_parses_http(self):
        hosts = self.p.list_hosts()
        app = next(h for h in hosts if "app.example.com" in h["domains"])
        self.assertEqual(app["scheme"], "http")
        self.assertEqual(app["port"], 3000)
        self.assertEqual(app["host"], "192.168.1.10")

    def test_list_hosts_parses_https(self):
        hosts = self.p.list_hosts()
        api = next(h for h in hosts if "api.example.com" in h["domains"])
        self.assertEqual(api["scheme"], "https")
        self.assertEqual(api["port"], 8443)
        self.assertTrue(api["ssl"])

    def test_list_hosts_skips_fallback_rules(self):
        hosts = self.p.list_hosts()
        for h in hosts:
            for d in h["domains"]:
                self.assertNotIn("http_status", d)

    def test_list_hosts_returns_empty_when_no_config(self):
        self.p._get_configuration = MagicMock(return_value={})
        self.assertEqual(self.p.list_hosts(), [])


class TestCFTunnelCreateHost(unittest.TestCase):

    def setUp(self):
        self.p = CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tid"})
        self.p._upsert_ingress_rule = MagicMock(return_value=True)
        self.p._ensure_dns_record = MagicMock(return_value=True)

    def test_create_host_success(self):
        result = self.p.create_host("app.example.com", "192.168.1.10", 3000)
        self.assertIsNotNone(result)
        self.assertEqual(result["domain"], "app.example.com")

    def test_create_host_calls_upsert_ingress(self):
        self.p.create_host("app.example.com", "192.168.1.10", 3000, "http")
        self.p._upsert_ingress_rule.assert_called_once()
        call_args = self.p._upsert_ingress_rule.call_args[0]
        self.assertEqual(call_args[0], "app.example.com")
        self.assertIn("192.168.1.10", call_args[1])
        self.assertIn("3000", call_args[1])

    def test_create_host_calls_ensure_dns(self):
        self.p.create_host("app.example.com", "192.168.1.10", 3000)
        self.p._ensure_dns_record.assert_called_once_with("app.example.com")

    def test_create_host_returns_none_when_ingress_fails(self):
        self.p._upsert_ingress_rule = MagicMock(return_value=False)
        result = self.p.create_host("app.example.com", "192.168.1.10", 3000)
        self.assertIsNone(result)


class TestCFTunnelDeleteHost(unittest.TestCase):

    def setUp(self):
        self.p = CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tid"})
        self.p._delete_ingress_rule = MagicMock(return_value=True)
        self.p._delete_dns_record = MagicMock(return_value=True)

    def test_delete_host_calls_both_cleanups(self):
        result = self.p.delete_host("app.example.com")
        self.assertTrue(result)
        self.p._delete_ingress_rule.assert_called_once_with("app.example.com")
        self.p._delete_dns_record.assert_called_once_with("app.example.com")

    def test_delete_host_returns_false_when_ingress_delete_fails(self):
        self.p._delete_ingress_rule = MagicMock(return_value=False)
        result = self.p.delete_host("app.example.com")
        self.assertFalse(result)


class TestCFTunnelIngressRules(unittest.TestCase):

    def setUp(self):
        self.p = CloudflareTunnelProvider("", "acc123", "tok", {"tunnel_id": "tid"})

    def test_upsert_replaces_existing_rule(self):
        existing_config = {
            "ingress": [
                {"hostname": "app.example.com", "service": "http://old:80", "originRequest": {}},
                {"service": "http_status:404"},
            ]
        }
        put_calls = []

        self.p._get_configuration = MagicMock(return_value=existing_config)
        self.p._put_configuration = MagicMock(side_effect=lambda c: put_calls.append(c) or True)

        self.p._upsert_ingress_rule("app.example.com", "http://new:3000")
        ingress = put_calls[0]["ingress"]
        new_rule = next((r for r in ingress if r.get("hostname") == "app.example.com"), None)
        self.assertIsNotNone(new_rule)
        self.assertEqual(new_rule["service"], "http://new:3000")

    def test_upsert_ensures_fallback_rule_at_end(self):
        self.p._get_configuration = MagicMock(return_value={"ingress": []})
        put_calls = []
        self.p._put_configuration = MagicMock(side_effect=lambda c: put_calls.append(c) or True)
        self.p._upsert_ingress_rule("app.example.com", "http://backend:80")
        ingress = put_calls[0]["ingress"]
        last = ingress[-1]
        self.assertNotIn("hostname", last)
        self.assertIn("http_status", last.get("service", ""))

    def test_delete_removes_rule(self):
        existing_config = {
            "ingress": [
                {"hostname": "app.example.com", "service": "http://backend:80", "originRequest": {}},
                {"hostname": "other.example.com", "service": "http://other:80", "originRequest": {}},
                {"service": "http_status:404"},
            ]
        }
        put_calls = []
        self.p._get_configuration = MagicMock(return_value=existing_config)
        self.p._put_configuration = MagicMock(side_effect=lambda c: put_calls.append(c) or True)
        self.p._delete_ingress_rule("app.example.com")
        ingress = put_calls[0]["ingress"]
        hostnames = [r.get("hostname") for r in ingress]
        self.assertNotIn("app.example.com", hostnames)
        self.assertIn("other.example.com", hostnames)

    def test_delete_returns_true_when_rule_not_found(self):
        self.p._get_configuration = MagicMock(return_value={"ingress": []})
        self.p._put_configuration = MagicMock(return_value=True)
        result = self.p._delete_ingress_rule("nonexistent.example.com")
        self.assertTrue(result)
        # No PUT should be issued when nothing was removed
        self.p._put_configuration.assert_not_called()


if __name__ == "__main__":
    unittest.main()
