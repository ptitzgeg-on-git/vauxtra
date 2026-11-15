"""A provider credential carried in a custom header does not follow a redirect to another host."""

import unittest

import requests

from app.providers.base import TimeoutSession


def _redirect(old_url: str, new_url: str) -> requests.PreparedRequest:
    session = TimeoutSession()
    original = requests.Request(
        "GET", old_url, headers={"X-API-Key": "k", "X-FTL-SID": "s", "X-FTL-CSRF": "c"}
    ).prepare()
    response = requests.Response()
    response.request = original
    following = original.copy()
    following.prepare_url(new_url, None)
    session.rebuild_auth(following, response)
    return following


class CredentialHeadersOnRedirectTests(unittest.TestCase):
    def test_they_are_dropped_when_the_host_changes(self):
        following = _redirect("http://pdns.lan:8081/api/v1/servers", "http://collector.example/x")
        for name in ("X-API-Key", "X-FTL-SID", "X-FTL-CSRF"):
            self.assertNotIn(name, following.headers)

    def test_they_are_kept_on_the_same_host(self):
        following = _redirect("http://pdns.lan:8081/api/v1/servers", "http://pdns.lan:8081/api/v1/servers/")
        self.assertEqual(following.headers.get("X-API-Key"), "k")

    def test_they_are_kept_on_an_upgrade_to_https(self):
        """`requests` treats http:80 -> https:443 on one host as the same site."""
        following = _redirect("http://pihole.lan/api/auth", "https://pihole.lan/api/auth")
        self.assertEqual(following.headers.get("X-FTL-SID"), "s")


if __name__ == "__main__":
    unittest.main()
