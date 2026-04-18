"""Unit tests for TechnitiumProvider — all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock, patch, call

from app.providers.technitium import TechnitiumProvider


def _response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    return r


def _ok() -> MagicMock:
    return _response(200, {"status": "ok"})


class TestTechnitiumLogin(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")

    def test_login_success(self) -> None:
        self.provider.session.post = MagicMock(return_value=_response(200, {
            "status": "ok",
            "response": {"token": "tok123"},
        }))
        self.assertTrue(self.provider._login())
        self.assertEqual(self.provider._token, "tok123")

    def test_login_failure_wrong_status(self) -> None:
        self.provider.session.post = MagicMock(return_value=_response(401, {"status": "error"}))
        self.assertFalse(self.provider._login())
        self.assertIsNone(self.provider._token)

    def test_login_failure_request_exception(self) -> None:
        import requests
        self.provider.session.post = MagicMock(side_effect=requests.RequestException("timeout"))
        self.assertFalse(self.provider._login())

    def test_ensure_token_reuses_valid_token(self) -> None:
        self.provider._token = "existing"
        self.provider.session.get = MagicMock(return_value=_response(200, {"status": "ok"}))
        self.assertTrue(self.provider._ensure_token())
        self.assertEqual(self.provider._token, "existing")

    def test_ensure_token_refreshes_expired_token(self) -> None:
        self.provider._token = "expired"
        # Session check fails, then login succeeds
        self.provider.session.get = MagicMock(return_value=_response(401, {"status": "error"}))
        self.provider.session.post = MagicMock(return_value=_response(200, {
            "status": "ok",
            "response": {"token": "fresh"},
        }))
        self.assertTrue(self.provider._ensure_token())
        self.assertEqual(self.provider._token, "fresh")


class TestTechnitiumConnection(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"

    def test_test_connection_ok(self) -> None:
        self.provider.session.get = MagicMock(return_value=_response(200, {"status": "ok"}))
        self.assertTrue(self.provider.test_connection())

    def test_test_connection_fail(self) -> None:
        self.provider.session.get = MagicMock(return_value=_response(401, {"status": "error"}))
        self.provider.session.post = MagicMock(return_value=_response(401, {"status": "error"}))
        self.assertFalse(self.provider.test_connection())


class TestTechnitiumZones(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"

    def _mock_zones(self, zones: list[str]) -> None:
        self.provider.session.get = MagicMock(return_value=_response(200, {
            "status": "ok",
            "response": {"zones": [{"name": z, "disabled": False} for z in zones]},
        }))

    def test_list_zones(self) -> None:
        self._mock_zones(["home.local", "example.com"])
        result = self.provider._list_zones()
        self.assertEqual(result, ["home.local", "example.com"])

    def test_list_zones_excludes_disabled(self) -> None:
        self.provider.session.get = MagicMock(return_value=_response(200, {
            "status": "ok",
            "response": {"zones": [
                {"name": "home.local", "disabled": False},
                {"name": "disabled.local", "disabled": True},
            ]},
        }))
        result = self.provider._list_zones()
        self.assertEqual(result, ["home.local"])

    def test_find_zone_exact_match(self) -> None:
        self._mock_zones(["home.local", "example.com"])
        self.assertEqual(self.provider._find_zone("home.local"), "home.local")

    def test_find_zone_subdomain(self) -> None:
        self._mock_zones(["home.local", "example.com"])
        self.assertEqual(self.provider._find_zone("myapp.home.local"), "home.local")

    def test_find_zone_longest_match(self) -> None:
        self._mock_zones(["local", "home.local"])
        self.assertEqual(self.provider._find_zone("myapp.home.local"), "home.local")

    def test_find_zone_no_match(self) -> None:
        self._mock_zones(["example.com"])
        self.assertIsNone(self.provider._find_zone("myapp.home.local"))

    def test_zone_fallback(self) -> None:
        self.assertEqual(self.provider._zone_fallback("myapp.home.local"), "home.local")
        self.assertEqual(self.provider._zone_fallback("single"), "single")


class TestTechnitiumListRewrites(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"
        # Patch _ensure_token so tests focus on the actual list logic
        self.provider._ensure_token = MagicMock(return_value=True)

    def test_list_rewrites_returns_a_records(self) -> None:
        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            if "zones/records/get" in url:
                return _response(200, {"status": "ok", "response": {"records": [
                    {"name": "myapp.home.local", "type": "A", "isDisabled": False,
                     "rData": {"ipAddress": "192.168.1.10"}},
                    {"name": "other.home.local", "type": "CNAME", "isDisabled": False,
                     "rData": {"cname": "something"}},
                ]}})
            return _response(200, {})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        result = self.provider.list_rewrites()
        self.assertEqual(result, [{"domain": "myapp.home.local", "answer": "192.168.1.10"}])

    def test_list_rewrites_skips_disabled(self) -> None:
        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            return _response(200, {"status": "ok", "response": {"records": [
                {"name": "disabled.home.local", "type": "A", "isDisabled": True,
                 "rData": {"ipAddress": "192.168.1.99"}},
            ]}})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        self.assertEqual(self.provider.list_rewrites(), [])

    def test_list_rewrites_returns_empty_on_auth_fail(self) -> None:
        self.provider._ensure_token = MagicMock(return_value=False)
        self.assertEqual(self.provider.list_rewrites(), [])


class TestTechnitiumAddRewrite(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"
        self.provider._ensure_token = MagicMock(return_value=True)

    def test_add_rewrite_success(self) -> None:
        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            if "zones/records/add" in url:
                return _response(200, {"status": "ok"})
            return _response(200, {})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        self.assertTrue(self.provider.add_rewrite("myapp.home.local", "192.168.1.10"))

    def test_add_rewrite_uses_zone_fallback(self) -> None:
        """When no zone matches, falls back to last two labels."""
        captured: list[dict] = []

        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": []}})
            if "zones/records/add" in url:
                captured.append(params or {})
                return _response(200, {"status": "ok"})
            return _response(200, {})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        self.assertTrue(self.provider.add_rewrite("myapp.home.local", "192.168.1.10"))
        self.assertEqual(captured[0]["zone"], "home.local")

    def test_add_rewrite_api_error(self) -> None:
        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            return _response(200, {"status": "error", "errorMessage": "Record already exists"})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        self.assertFalse(self.provider.add_rewrite("myapp.home.local", "192.168.1.10"))


class TestTechnitiumDeleteRewrite(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"
        self.provider._ensure_token = MagicMock(return_value=True)

    def test_delete_rewrite_success(self) -> None:
        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            if "zones/records/delete" in url:
                return _response(200, {"status": "ok"})
            return _response(200, {})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        self.assertTrue(self.provider.delete_rewrite("myapp.home.local", "192.168.1.10"))


class TestTechnitiumUpdateRewrite(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = TechnitiumProvider("http://dns:5380", "admin", "secret")
        self.provider._token = "tok"
        self.provider._ensure_token = MagicMock(return_value=True)

    def test_update_noop_when_unchanged(self) -> None:
        self.provider.session.get = MagicMock()
        result = self.provider.update_rewrite("a.home.local", "1.2.3.4", "a.home.local", "1.2.3.4")
        self.assertTrue(result)
        self.provider.session.get.assert_not_called()

    def test_update_adds_then_deletes(self) -> None:
        added: list[str] = []
        deleted: list[str] = []

        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            if "zones/records/add" in url:
                added.append(params["domain"])
                return _response(200, {"status": "ok"})
            if "zones/records/delete" in url:
                deleted.append(params["domain"])
                return _response(200, {"status": "ok"})
            return _response(200, {})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        result = self.provider.update_rewrite(
            "old.home.local", "1.1.1.1",
            "new.home.local", "2.2.2.2",
        )
        self.assertTrue(result)
        self.assertIn("new.home.local", added)
        self.assertIn("old.home.local", deleted)

    def test_update_returns_true_if_add_ok_delete_fails(self) -> None:
        """New record created is preserved even if old record deletion fails."""
        def mock_get(url: str, params: dict | None = None) -> MagicMock:
            if "zones/list" in url:
                return _response(200, {"status": "ok", "response": {"zones": [
                    {"name": "home.local", "disabled": False},
                ]}})
            if "zones/records/add" in url:
                return _response(200, {"status": "ok"})
            if "zones/records/delete" in url:
                return _response(200, {"status": "error"})
            return _response(200, {})

        self.provider.session.get = MagicMock(side_effect=mock_get)
        result = self.provider.update_rewrite(
            "old.home.local", "1.1.1.1",
            "new.home.local", "2.2.2.2",
        )
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
