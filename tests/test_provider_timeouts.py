"""Every provider HTTP call must carry a timeout.

`requests` never reads `session.timeout`; providers used to set that attribute and
send requests with no read timeout at all. A provider that accepts the connection
and then stops answering would block the scheduler thread forever, silently ending
all monitoring.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.config import PROVIDER_TIMEOUT
from app.providers.adguard import AdGuardProvider
from app.providers.base import TimeoutSession
from app.providers.cloudflare_tunnel import CloudflareTunnelProvider
from app.providers.desec import DesecProvider
from app.providers.npm import NPMProvider
from app.providers.pihole import PiholeProvider
from app.providers.powerdns import PowerDNSProvider
from app.providers.technitium import TechnitiumProvider
from app.providers.traefik import TraefikProvider


class TestTimeoutSession(unittest.TestCase):

    def test_default_timeout_is_injected(self):
        s = TimeoutSession()
        with patch("requests.Session.request", return_value=MagicMock()) as m:
            s.get("http://example.invalid/x")
        self.assertEqual(m.call_args.kwargs.get("timeout"), PROVIDER_TIMEOUT)

    def test_explicit_timeout_wins(self):
        s = TimeoutSession()
        with patch("requests.Session.request", return_value=MagicMock()) as m:
            s.get("http://example.invalid/x", timeout=3)
        self.assertEqual(m.call_args.kwargs.get("timeout"), 3)

    def test_all_verbs_go_through_the_default(self):
        s = TimeoutSession()
        with patch("requests.Session.request", return_value=MagicMock()) as m:
            s.post("http://example.invalid/x", json={})
            s.put("http://example.invalid/x", json={})
            s.delete("http://example.invalid/x")
        for call in m.call_args_list:
            self.assertEqual(call.kwargs.get("timeout"), PROVIDER_TIMEOUT)


class TestProvidersUseTimeoutSession(unittest.TestCase):

    def test_every_http_provider_carries_a_timeout(self):
        providers = [
            AdGuardProvider("http://h", "u", "p"),
            PiholeProvider("http://h", "u", "p"),
            TechnitiumProvider("http://h", "u", "p"),
            PowerDNSProvider("http://h", "u", "p"),
            DesecProvider("", "", "tok"),
            TraefikProvider("http://h", "u", "p"),
            NPMProvider("http://h", "u", "p"),
            CloudflareTunnelProvider("", "acc", "tok", {"tunnel_id": "tid"}),
        ]
        for p in providers:
            with self.subTest(provider=type(p).__name__):
                self.assertIsInstance(p.session, TimeoutSession)
                self.assertEqual(p.session.timeout, PROVIDER_TIMEOUT)

    def test_a_call_without_explicit_timeout_still_gets_one(self):
        """AdGuard's `test_connection` passes no timeout — the session must add it."""
        p = AdGuardProvider("http://h", "u", "p")
        with patch("requests.Session.request", return_value=MagicMock(status_code=200)) as m:
            p.test_connection()
        self.assertEqual(m.call_args.kwargs.get("timeout"), PROVIDER_TIMEOUT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
