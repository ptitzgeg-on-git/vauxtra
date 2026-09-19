"""A host that refused the password is not a host that could not be reached.

`_provider_diagnostics` falls back to one boolean from `test_connection` for any provider
that carries no `validate_permissions`, and labels that boolean `connection_failed`. Five
integrations -- AdGuard, Pi-hole, NPM, Zoraxy and Traefik -- had no such method, so every
failure they could have came out as a connection problem: a wrong password, a revoked
token, an API behind a middleware. The panel then told the operator to go and look at the
network, which is the one place there was nothing to find.

These tests hold the two answers apart at the provider level, where the difference is
known. Nothing here asks whether the wording is pretty; it asks that a refusal and an
unreachable host produce different `detail_code`s, because that is what sends the reader to
the right place.
"""

import unittest
from unittest.mock import MagicMock

import requests

from app.providers.adguard import AdGuardProvider
from app.providers.npm import NPMProvider
from app.providers.pihole import PiholeProvider
from app.providers.traefik import TraefikProvider
from app.providers.zoraxy import ZoraxyProvider


def _codes(validation: dict) -> list[str]:
    return [c["detail_code"] for c in validation["checks"]]


class TestRefusedIsNotUnreachable(unittest.TestCase):
    """Each provider, twice: nothing listening, then listening and refusing."""

    def _providers(self):
        return {
            "adguard": AdGuardProvider("http://adguard", "admin", "pw"),
            "pihole": PiholeProvider("http://pihole", "", "pw"),
            "npm": NPMProvider("http://npm:81", "admin@example.com", "pw"),
            "zoraxy": ZoraxyProvider("http://zoraxy:8000", "admin", "pw"),
            "traefik": TraefikProvider("http://traefik:8080", "", ""),
        }

    def test_an_unreachable_host_is_reported_as_a_connection_failure(self):
        for name, provider in self._providers().items():
            with self.subTest(provider=name):
                provider.session.get = MagicMock(side_effect=requests.RequestException("refused"))
                provider.session.post = MagicMock(side_effect=requests.RequestException("refused"))
                result = provider.validate_permissions()
                self.assertFalse(result["ok"])
                self.assertEqual(_codes(result)[0], "connection_failed")
                self.assertNotIn("login_failed", _codes(result))

    def test_a_host_that_refused_the_credentials_is_not_a_connection_failure(self):
        for name, provider in self._providers().items():
            with self.subTest(provider=name):
                refused = MagicMock()
                refused.status_code = 401
                refused.ok = False
                refused.json.return_value = {}
                refused.text = "unauthorized"
                refused.raise_for_status = MagicMock(side_effect=requests.HTTPError)
                provider.session.get = MagicMock(return_value=refused)
                provider.session.post = MagicMock(return_value=refused)
                result = provider.validate_permissions()
                self.assertFalse(result["ok"])
                codes = _codes(result)
                self.assertEqual(codes[0], "connection_ok")
                self.assertIn("login_failed", codes)
                self.assertNotIn("connection_failed", codes)


if __name__ == "__main__":
    unittest.main()
