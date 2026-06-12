"""Unit tests for PiholeProvider — v5 and v6, all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock, patch

import requests

from app.providers.pihole import PiholeProvider


def _response(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.content = b"data" if json_data is not None else b""
    r.json.return_value = json_data if json_data is not None else {}
    r.raise_for_status = MagicMock(
        side_effect=None if status_code < 400 else requests.HTTPError
    )
    return r


class TestPiholeVersionDetection(unittest.TestCase):

    def setUp(self):
        self.p = PiholeProvider("http://pihole", "", "apikey123")

    def test_detect_v6_on_200(self):
        self.p.session.get = MagicMock(return_value=_response(200))
        self.assertEqual(self.p._detect_version(), 6)

    def test_detect_v6_on_401(self):
        self.p.session.get = MagicMock(return_value=_response(401))
        self.assertEqual(self.p._detect_version(), 6)

    def test_detect_v5_on_network_error(self):
        self.p.session.get = MagicMock(side_effect=requests.RequestException("conn refused"))
        self.assertEqual(self.p._detect_version(), 5)

    def test_detect_v5_on_other_status(self):
        # Any non-200/401 falls back to v5
        self.p.session.get = MagicMock(return_value=_response(500))
        self.assertEqual(self.p._detect_version(), 5)


class TestPiholeV6Auth(unittest.TestCase):

    def setUp(self):
        self.p = PiholeProvider("http://pihole", "", "mysecret")

    def test_login_v6_success(self):
        self.p.session.post = MagicMock(return_value=_response(200, {
            "session": {"sid": "sess123", "csrf": "csrf456"},
        }))
        self.assertTrue(self.p._login_v6())
        self.assertEqual(self.p._v6_sid, "sess123")
        self.assertEqual(self.p._v6_csrf, "csrf456")
        self.assertEqual(self.p.session.headers["X-FTL-SID"], "sess123")
        self.assertEqual(self.p.session.headers["X-FTL-CSRF"], "csrf456")

    def test_login_v6_failure_no_sid(self):
        self.p.session.post = MagicMock(return_value=_response(200, {"session": {}}))
        self.assertFalse(self.p._login_v6())
        self.assertFalse(bool(self.p._v6_sid))

    def test_login_v6_failure_bad_status(self):
        self.p.session.post = MagicMock(return_value=_response(401, {}))
        self.assertFalse(self.p._login_v6())

    def test_login_v6_network_error(self):
        self.p.session.post = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.p._login_v6())

    def test_logout_v6_clears_state(self):
        self.p._v6_sid = "sess123"
        self.p._v6_csrf = "csrf456"
        self.p.session.headers["X-FTL-SID"] = "sess123"
        self.p.session.headers["X-FTL-CSRF"] = "csrf456"
        self.p.session.delete = MagicMock(return_value=_response(200))
        self.p._logout_v6()
        self.assertIsNone(self.p._v6_sid)
        self.assertIsNone(self.p._v6_csrf)
        self.assertNotIn("X-FTL-SID", self.p.session.headers)
        self.assertNotIn("X-FTL-CSRF", self.p.session.headers)


class TestPiholeV5Operations(unittest.TestCase):

    def setUp(self):
        self.p = PiholeProvider("http://pihole", "", "apikey123")
        self.p._version = 5  # Force v5 path

    def _ensure_auth_noop(self):
        # v5 _ensure_auth just returns True
        return True

    def test_test_connection_v5_ok(self):
        self.p.session.get = MagicMock(return_value=_response(200, {"data": []}))
        self.assertTrue(self.p.test_connection())

    def test_test_connection_v5_fail(self):
        self.p.session.get = MagicMock(return_value=_response(401))
        self.assertFalse(self.p.test_connection())

    def test_list_rewrites_v5_success(self):
        self.p.session.get = MagicMock(return_value=_response(200, {
            "data": [
                ["app.home.local", "192.168.1.10"],
                ["db.home.local", "192.168.1.20"],
            ]
        }))
        result = self.p.list_rewrites()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["domain"], "app.home.local")
        self.assertEqual(result[0]["answer"], "192.168.1.10")

    def test_list_rewrites_v5_empty(self):
        self.p.session.get = MagicMock(return_value=_response(200, {"data": []}))
        self.assertEqual(self.p.list_rewrites(), [])

    def test_add_rewrite_v5_success(self):
        self.p.session.get = MagicMock(return_value=_response(200, {"success": True}))
        self.assertTrue(self.p.add_rewrite("new.home.local", "10.0.0.5"))

    def test_add_rewrite_v5_failure(self):
        self.p.session.get = MagicMock(return_value=_response(200, {"success": False}))
        self.assertFalse(self.p.add_rewrite("new.home.local", "10.0.0.5"))

    def test_delete_rewrite_v5_success(self):
        self.p.session.get = MagicMock(return_value=_response(200, {"success": True}))
        self.assertTrue(self.p.delete_rewrite("app.home.local", "10.0.0.1"))

    def test_delete_rewrite_v5_failure(self):
        self.p.session.get = MagicMock(return_value=_response(200, {"success": False}))
        self.assertFalse(self.p.delete_rewrite("app.home.local", "10.0.0.1"))


class TestPiholeV6Operations(unittest.TestCase):

    def setUp(self):
        self.p = PiholeProvider("http://pihole", "", "mysecret")
        self.p._version = 6
        self.p._v6_sid = "sess"
        self.p._v6_csrf = "csrf"
        self.p.session.headers["X-FTL-SID"] = "sess"
        self.p.session.headers["X-FTL-CSRF"] = "csrf"
        # Stub _ensure_auth to just return True (session already "valid")
        self.p._ensure_auth = MagicMock(return_value=True)

    def test_list_rewrites_v6_success(self):
        self.p.session.get = MagicMock(return_value=_response(200, {
            "config": {"dns": {"hosts": ["192.168.1.10 app.home.local", "192.168.1.20 db.home.local"]}}
        }))
        result = self.p.list_rewrites()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["domain"], "app.home.local")
        self.assertEqual(result[0]["answer"], "192.168.1.10")

    def test_list_rewrites_v6_empty(self):
        self.p.session.get = MagicMock(return_value=_response(200, {
            "config": {"dns": {"hosts": []}}
        }))
        self.assertEqual(self.p.list_rewrites(), [])

    def test_add_rewrite_v6_success(self):
        self.p.session.put = MagicMock(return_value=_response(201))
        self.assertTrue(self.p.add_rewrite("new.home.local", "10.0.0.5"))

    def test_add_rewrite_v6_failure(self):
        self.p.session.put = MagicMock(return_value=_response(400))
        self.assertFalse(self.p.add_rewrite("new.home.local", "10.0.0.5"))

    def test_delete_rewrite_v6_success(self):
        self.p.session.delete = MagicMock(return_value=_response(204))
        self.assertTrue(self.p.delete_rewrite("app.home.local", "10.0.0.1"))

    def test_delete_rewrite_v6_failure(self):
        self.p.session.delete = MagicMock(return_value=_response(404))
        self.assertFalse(self.p.delete_rewrite("app.home.local", "10.0.0.1"))

    def test_delete_rewrite_v6_encodes_url(self):
        """PUT/DELETE URL must URL-encode the 'ip domain' entry."""
        self.p.session.delete = MagicMock(return_value=_response(204))
        self.p.delete_rewrite("app.home.local", "10.0.0.1")
        call_url = self.p.session.delete.call_args[0][0]
        self.assertIn("10.0.0.1", call_url)
        self.assertIn("app.home.local", call_url)
        # Spaces must be encoded (%20 or +)
        self.assertNotIn(" ", call_url)


class TestPiholeUpdateRewrite(unittest.TestCase):

    def setUp(self):
        self.p = PiholeProvider("http://pihole", "", "apikey123")
        self.p._version = 5
        self.p._ensure_auth = MagicMock(return_value=True)

    def test_update_noop_when_unchanged(self):
        self.p.session.get = MagicMock()
        result = self.p.update_rewrite("a.local", "1.1.1.1", "a.local", "1.1.1.1")
        self.assertTrue(result)
        self.p.session.get.assert_not_called()

    def test_update_adds_then_deletes(self):
        add_called = []
        delete_called = []

        def mock_get(url, params=None, **kwargs):
            action = (params or {}).get("action", "")
            if action == "add":
                add_called.append(params)
                return _response(200, {"success": True})
            if action == "delete":
                delete_called.append(params)
                return _response(200, {"success": True})
            return _response(200, {"data": []})

        self.p.session.get = MagicMock(side_effect=mock_get)
        result = self.p.update_rewrite("old.local", "1.1.1.1", "new.local", "2.2.2.2")
        self.assertTrue(result)
        self.assertTrue(add_called)
        self.assertTrue(delete_called)


if __name__ == "__main__":
    unittest.main()
