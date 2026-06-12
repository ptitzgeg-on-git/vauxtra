"""Unit tests for AdGuardProvider — all HTTP calls are mocked."""

import unittest
from unittest.mock import MagicMock

import requests

from app.providers.adguard import AdGuardProvider


def _response(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.raise_for_status = MagicMock(
        side_effect=None if status_code < 400 else requests.HTTPError
    )
    return r


class TestAdGuardConnection(unittest.TestCase):

    def setUp(self):
        self.ag = AdGuardProvider("http://adguard:3000", "admin", "secret")

    def test_connection_success(self):
        self.ag.session.get = MagicMock(return_value=_response(200))
        self.assertTrue(self.ag.test_connection())

    def test_connection_failure_401(self):
        self.ag.session.get = MagicMock(return_value=_response(401))
        self.assertFalse(self.ag.test_connection())

    def test_connection_network_error(self):
        self.ag.session.get = MagicMock(side_effect=requests.RequestException("timeout"))
        self.assertFalse(self.ag.test_connection())


class TestAdGuardListRewrites(unittest.TestCase):

    def setUp(self):
        self.ag = AdGuardProvider("http://adguard:3000", "admin", "secret")

    def test_list_rewrites_returns_normalised_list(self):
        self.ag.session.get = MagicMock(return_value=_response(200, [
            {"domain": "app.home.local", "answer": "192.168.1.10"},
            {"domain": "db.home.local", "answer": "192.168.1.20"},
        ]))
        result = self.ag.list_rewrites()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["domain"], "app.home.local")
        self.assertEqual(result[0]["answer"], "192.168.1.10")

    def test_list_rewrites_returns_empty_on_error(self):
        self.ag.session.get = MagicMock(side_effect=requests.RequestException("err"))
        self.assertEqual(self.ag.list_rewrites(), [])

    def test_list_rewrites_returns_empty_on_401(self):
        r = _response(401)
        r.raise_for_status = MagicMock(side_effect=requests.HTTPError)
        self.ag.session.get = MagicMock(return_value=r)
        self.assertEqual(self.ag.list_rewrites(), [])


class TestAdGuardAddRewrite(unittest.TestCase):

    def setUp(self):
        self.ag = AdGuardProvider("http://adguard:3000", "admin", "secret")

    def _mock_list(self, existing):
        """Make list_rewrites return a fixed list."""
        self.ag.session.get = MagicMock(return_value=_response(200, existing))

    def test_add_rewrite_new_entry(self):
        # No existing entries → POST should be called
        self._mock_list([])
        self.ag.session.post = MagicMock(return_value=_response(200))
        self.assertTrue(self.ag.add_rewrite("new.home.local", "10.0.0.5"))
        self.ag.session.post.assert_called_once()

    def test_add_rewrite_idempotent_when_exists(self):
        # Exact same domain+answer already present → no POST needed
        self._mock_list([{"domain": "app.home.local", "answer": "10.0.0.1"}])
        self.ag.session.post = MagicMock()
        result = self.ag.add_rewrite("app.home.local", "10.0.0.1")
        self.assertTrue(result)
        self.ag.session.post.assert_not_called()

    def test_add_rewrite_posts_when_same_domain_different_ip(self):
        # Same domain, different IP → update should create a new entry
        self._mock_list([{"domain": "app.home.local", "answer": "10.0.0.1"}])
        self.ag.session.post = MagicMock(return_value=_response(200))
        result = self.ag.add_rewrite("app.home.local", "10.0.0.99")
        self.assertTrue(result)
        self.ag.session.post.assert_called_once()

    def test_add_rewrite_failure_on_network_error(self):
        self._mock_list([])
        self.ag.session.post = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.ag.add_rewrite("new.home.local", "10.0.0.5"))


class TestAdGuardDeleteRewrite(unittest.TestCase):

    def setUp(self):
        self.ag = AdGuardProvider("http://adguard:3000", "admin", "secret")

    def test_delete_rewrite_success(self):
        self.ag.session.post = MagicMock(return_value=_response(200))
        self.assertTrue(self.ag.delete_rewrite("app.home.local", "10.0.0.1"))
        call_args = self.ag.session.post.call_args
        self.assertIn("delete", call_args[0][0])
        self.assertEqual(call_args[1]["json"]["domain"], "app.home.local")

    def test_delete_rewrite_fail_on_error(self):
        self.ag.session.post = MagicMock(return_value=_response(500))
        self.assertFalse(self.ag.delete_rewrite("app.home.local", "10.0.0.1"))

    def test_delete_rewrite_network_error(self):
        self.ag.session.post = MagicMock(side_effect=requests.RequestException("err"))
        self.assertFalse(self.ag.delete_rewrite("app.home.local", "10.0.0.1"))


class TestAdGuardUpdateRewrite(unittest.TestCase):

    def setUp(self):
        self.ag = AdGuardProvider("http://adguard:3000", "admin", "secret")

    def test_update_noop_when_unchanged(self):
        # Nothing should be called when old == new
        self.ag.session.get = MagicMock()
        self.ag.session.post = MagicMock()
        result = self.ag.update_rewrite("app.home.local", "10.0.0.1", "app.home.local", "10.0.0.1")
        self.assertTrue(result)
        self.ag.session.post.assert_not_called()

    def test_update_creates_new_then_deletes_old(self):
        add_calls = []
        delete_calls = []

        def mock_get(url, **kwargs):
            return _response(200, [])  # No existing entries

        def mock_post(url, json=None, **kwargs):
            if "add" in url:
                add_calls.append(json)
            elif "delete" in url:
                delete_calls.append(json)
            return _response(200)

        self.ag.session.get = MagicMock(side_effect=mock_get)
        self.ag.session.post = MagicMock(side_effect=mock_post)

        result = self.ag.update_rewrite(
            "old.home.local", "10.0.0.1",
            "new.home.local", "10.0.0.99",
        )
        self.assertTrue(result)
        self.assertTrue(any(c.get("domain") == "new.home.local" for c in add_calls))
        self.assertTrue(any(c.get("domain") == "old.home.local" for c in delete_calls))

    def test_update_returns_true_if_add_ok_delete_fails(self):
        """New record preserved even if old record delete fails."""
        self.ag.session.get = MagicMock(return_value=_response(200, []))

        call_count = [0]

        def mock_post(url, json=None, **kwargs):
            call_count[0] += 1
            if "add" in url:
                return _response(200)
            return _response(500)  # delete fails

        self.ag.session.post = MagicMock(side_effect=mock_post)
        result = self.ag.update_rewrite(
            "old.home.local", "10.0.0.1",
            "new.home.local", "10.0.0.99",
        )
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
