"""A service target is a host name or an address, and nothing that can carry extra text."""

import unittest

from pydantic import ValidationError

from app.api.services import ServiceIn
from app.validators import is_valid_hostname


class AScopedIpv6AddressIsRefusedTests(unittest.TestCase):
    def test_a_scope_id_carrying_nginx_syntax_is_refused(self):
        self.assertFalse(is_valid_hostname('fe80::1%x";\n  return 200 pwned;#'))

    def test_any_scope_id_is_refused(self):
        self.assertFalse(is_valid_hostname("fe80::1%eth0"))

    def test_plain_addresses_and_names_still_pass(self):
        for value in ("192.168.1.10", "fe80::1", "2001:db8::5", "nextcloud", "app.example.com"):
            with self.subTest(value=value):
                self.assertTrue(is_valid_hostname(value))

    def test_the_service_model_refuses_it(self):
        with self.assertRaises(ValidationError):
            ServiceIn(
                subdomain="app", domain="example.com",
                target_ip="fe80::1%x", target_port=80,
            )


if __name__ == "__main__":
    unittest.main()
