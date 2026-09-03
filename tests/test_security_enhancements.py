"""Tests for the security utilities."""

import unittest

from app.security import sanitize_domain, validate_cors_origins, validate_password_strength


class TestCORSValidation(unittest.TestCase):
    def test_valid_cors_origins(self):
        origins_str = "http://localhost:5173,https://example.com"
        result = validate_cors_origins(origins_str, "")
        self.assertEqual(len(result), 2)
        self.assertIn("http://localhost:5173", result)
        self.assertIn("https://example.com", result)

    def test_invalid_scheme(self):
        with self.assertRaises(ValueError) as cm:
            validate_cors_origins("ftp://example.com", "")
        self.assertIn("Invalid scheme", str(cm.exception))

    def test_wildcard_rejected(self):
        with self.assertRaises(ValueError) as cm:
            validate_cors_origins("http://*.example.com", "")
        self.assertIn("Wildcard", str(cm.exception))

    def test_port_validation(self):
        origins_str = "http://localhost:8888,https://example.com:443"
        result = validate_cors_origins(origins_str, "")
        self.assertEqual(len(result), 2)

    def test_invalid_port(self):
        with self.assertRaises(ValueError) as cm:
            validate_cors_origins("http://localhost:99999", "")
        self.assertIn("port out of range", str(cm.exception).lower())

    def test_fallback_to_default(self):
        default = "http://localhost:5173,http://127.0.0.1:5173"
        result = validate_cors_origins("", default)
        self.assertEqual(len(result), 2)


class TestDomainSanitization(unittest.TestCase):
    def test_valid_domain(self):
        self.assertEqual(sanitize_domain("app.example.com"), "app.example.com")

    def test_removes_special_chars(self):
        self.assertEqual(sanitize_domain("app../../../etc"), "app.etc")

    def test_wildcard_allowed(self):
        self.assertEqual(sanitize_domain("*.example.com"), "*.example.com")

    def test_collapses_dots(self):
        self.assertEqual(sanitize_domain("app..example.com"), "app.example.com")


class TestPasswordValidation(unittest.TestCase):
    """One policy: twelve characters, no character-class rules."""

    def test_strong_password(self):
        is_valid, msg = validate_password_strength("SecurePass123!")
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    def test_weak_length(self):
        is_valid, msg = validate_password_strength("Short1!")
        self.assertFalse(is_valid)
        self.assertIn("at least", msg)

    def test_eleven_characters_is_one_too_few(self):
        """The floor is twelve, and it is the floor both endpoints apply."""
        self.assertFalse(validate_password_strength("elevenchars")[0])
        self.assertTrue(validate_password_strength("twelvechars!")[0])

    def test_a_passphrase_needs_no_symbols(self):
        """This is the point of dropping the class rules: length is what is asked for."""
        is_valid, msg = validate_password_strength("correct horse battery staple")
        self.assertTrue(is_valid, msg)

    def test_length_alone_does_not_buy_a_repeated_character(self):
        is_valid, msg = validate_password_strength("abababababababab")
        self.assertFalse(is_valid)
        self.assertIn("different characters", msg)


if __name__ == "__main__":
    unittest.main()
