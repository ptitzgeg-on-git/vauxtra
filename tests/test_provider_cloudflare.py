"""Unit tests for CloudflareProvider — Cloudflare SDK client is fully mocked."""

import unittest
from unittest.mock import MagicMock, patch


def _make_zone(zone_id: str, name: str) -> MagicMock:
    z = MagicMock()
    z.id = zone_id
    z.name = name
    return z


def _make_record(record_id: str, name: str, content: str, rtype: str = "A", proxied: bool = False) -> MagicMock:
    r = MagicMock()
    r.id = record_id
    r.name = name
    r.content = content
    r.type = rtype
    r.proxied = proxied
    return r


class TestCloudflareProvider(unittest.TestCase):
    """Tests for CloudflareProvider with fully mocked Cloudflare SDK."""

    def _make_provider(self, zone_id: str = "", proxied: bool = False):
        """Create a CloudflareProvider with a mocked SDK client."""
        mock_cf_module = MagicMock()
        mock_client = MagicMock()
        mock_cf_module.Cloudflare.return_value = mock_client

        with patch.dict("sys.modules", {"cloudflare": mock_cf_module}):
            # Force the module to believe CF is installed
            import app.providers.cloudflare as cf_module
            orig_has_cf = cf_module._HAS_CF
            orig_cf = cf_module._cf
            cf_module._HAS_CF = True
            cf_module._cf = mock_cf_module

            from app.providers.cloudflare import CloudflareProvider
            provider = CloudflareProvider(
                "https://api.cloudflare.com/client/v4",
                zone_id,
                "test_token",
                {"proxied": proxied},
            )
            provider._client = mock_client

            # Restore module state
            cf_module._HAS_CF = orig_has_cf
            cf_module._cf = orig_cf

        return provider, mock_client

    def test_record_type_ipv4(self):
        p, _ = self._make_provider()
        self.assertEqual(p._record_type("192.168.1.1"), "A")

    def test_record_type_ipv6(self):
        p, _ = self._make_provider()
        self.assertEqual(p._record_type("2001:db8::1"), "AAAA")

    def test_record_type_hostname(self):
        p, _ = self._make_provider()
        self.assertEqual(p._record_type("cdn.example.com"), "CNAME")

    def test_is_ip_true_for_ipv4(self):
        p, _ = self._make_provider()
        self.assertTrue(p._is_ip("10.0.0.1"))

    def test_is_ip_true_for_ipv6(self):
        p, _ = self._make_provider()
        self.assertTrue(p._is_ip("::1"))

    def test_is_ip_false_for_hostname(self):
        p, _ = self._make_provider()
        self.assertFalse(p._is_ip("example.com"))

    def test_find_zone_uses_configured_id(self):
        p, client = self._make_provider(zone_id="zone123")
        result = p._find_zone("app.example.com")
        self.assertEqual(result, "zone123")
        # Should NOT call the API when zone_id is pre-configured
        client.zones.list.assert_not_called()

    def test_find_zone_auto_detects_zone(self):
        p, client = self._make_provider()
        client.zones.list.return_value = [_make_zone("zone456", "example.com")]
        result = p._find_zone("app.example.com")
        self.assertEqual(result, "zone456")

    def test_find_zone_returns_none_when_not_found(self):
        p, client = self._make_provider()
        client.zones.list.return_value = []
        result = p._find_zone("unknown.example.com")
        self.assertIsNone(result)

    def test_test_connection_success(self):
        p, client = self._make_provider()
        client.zones.list.return_value = [_make_zone("z1", "example.com")]
        self.assertTrue(p.test_connection())

    def test_test_connection_failure(self):
        p, client = self._make_provider()
        client.zones.list.side_effect = Exception("Unauthorized")
        self.assertFalse(p.test_connection())

    def test_list_rewrites_with_configured_zone(self):
        p, client = self._make_provider(zone_id="zone123")
        client.dns.records.list.side_effect = lambda zone_id, type: {
            "A": [_make_record("r1", "app.example.com", "1.2.3.4", "A")],
            "AAAA": [],
            "CNAME": [],
        }.get(type, [])
        result = p.list_rewrites()
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["domain"], "app.example.com")
        self.assertEqual(result[0]["answer"], "1.2.3.4")

    def test_add_rewrite_creates_new_record(self):
        p, client = self._make_provider(zone_id="zone123")
        # No existing records
        client.dns.records.list.return_value = []
        client.dns.records.create.return_value = _make_record("new1", "app.example.com", "1.2.3.4")
        result = p.add_rewrite("app.example.com", "1.2.3.4")
        self.assertTrue(result)
        client.dns.records.create.assert_called_once()

    def test_add_rewrite_idempotent_when_exists(self):
        p, client = self._make_provider(zone_id="zone123")
        client.dns.records.list.return_value = [
            _make_record("r1", "app.example.com", "1.2.3.4")
        ]
        result = p.add_rewrite("app.example.com", "1.2.3.4")
        self.assertTrue(result)
        # No create call when record already exists with same content
        client.dns.records.create.assert_not_called()

    def test_add_rewrite_updates_when_content_differs(self):
        p, client = self._make_provider(zone_id="zone123")
        client.dns.records.list.return_value = [
            _make_record("r1", "app.example.com", "1.1.1.1")  # Different IP
        ]
        result = p.add_rewrite("app.example.com", "2.2.2.2")
        self.assertTrue(result)
        client.dns.records.update.assert_called_once()

    def test_add_rewrite_returns_false_when_zone_not_found(self):
        p, client = self._make_provider()  # No configured zone
        client.zones.list.return_value = []  # Auto-detect finds nothing
        result = p.add_rewrite("app.unknown.invalid", "1.2.3.4")
        self.assertFalse(result)

    def test_add_rewrite_returns_false_on_api_error(self):
        p, client = self._make_provider(zone_id="zone123")
        client.dns.records.list.return_value = []
        client.dns.records.create.side_effect = Exception("API error")
        result = p.add_rewrite("app.example.com", "1.2.3.4")
        self.assertFalse(result)

    def test_delete_rewrite_success(self):
        p, client = self._make_provider(zone_id="zone123")
        client.dns.records.list.return_value = [
            _make_record("r1", "app.example.com", "1.2.3.4")
        ]
        client.dns.records.delete.return_value = MagicMock()
        result = p.delete_rewrite("app.example.com", "1.2.3.4")
        self.assertTrue(result)
        client.dns.records.delete.assert_called_once_with(
            dns_record_id="r1", zone_id="zone123"
        )

    def test_delete_rewrite_returns_false_when_not_found(self):
        p, client = self._make_provider(zone_id="zone123")
        client.dns.records.list.return_value = []  # Record not found
        result = p.delete_rewrite("app.example.com", "1.2.3.4")
        self.assertFalse(result)

    def test_delete_rewrite_returns_false_when_zone_not_found(self):
        p, client = self._make_provider()
        client.zones.list.return_value = []
        result = p.delete_rewrite("app.unknown.invalid", "1.2.3.4")
        self.assertFalse(result)

    def test_cname_not_proxied(self):
        """CNAME records must never be proxied (Cloudflare error 1014 risk)."""
        p, client = self._make_provider(zone_id="zone123", proxied=True)
        client.dns.records.list.return_value = []
        p.add_rewrite("app.example.com", "cdn.otherdomain.com")
        call_kwargs = client.dns.records.create.call_args[1]
        self.assertFalse(call_kwargs.get("proxied"), "CNAME must not be proxied")


if __name__ == "__main__":
    unittest.main()
