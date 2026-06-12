"""Tests for security utilities and request cache."""

import unittest
from app.cache import RequestCache
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
    def test_strong_password(self):
        is_valid, msg = validate_password_strength("SecurePass123!")
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    def test_weak_length(self):
        is_valid, msg = validate_password_strength("Short1!")
        self.assertFalse(is_valid)
        self.assertIn("at least", msg)

    def test_missing_uppercase(self):
        is_valid, msg = validate_password_strength("password123!")
        self.assertFalse(is_valid)
        self.assertIn("uppercase", msg)

    def test_missing_digit(self):
        is_valid, msg = validate_password_strength("StrongPassword!")
        self.assertFalse(is_valid)
        self.assertIn("digit", msg)


class TestRequestCache(unittest.TestCase):
    def setUp(self):
        self.cache = RequestCache()

    def test_cache_hit(self):
        self.cache.set("key1", "value1")
        self.assertEqual(self.cache.get("key1"), "value1")

    def test_cache_miss(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_ttl_expiration(self):
        self.cache.set("key1", "value1", ttl=0.01)
        import time
        time.sleep(0.1)
        self.assertIsNone(self.cache.get("key1"))

    def test_get_or_compute(self):
        result = self.cache.get_or_compute("key1", lambda: "computed")
        self.assertEqual(result, "computed")
        result = self.cache.get_or_compute("key1", lambda: "recomputed")
        self.assertEqual(result, "computed")

    def test_clear(self):
        self.cache.set("key1", "val1")
        self.cache.set("key2", "val2")
        self.cache.clear()
        self.assertIsNone(self.cache.get("key1"))
        self.assertIsNone(self.cache.get("key2"))

    def test_stats(self):
        self.cache.set("key1", "val1")
        self.cache.set("key2", "val2")
        stats = self.cache.stats()
        self.assertEqual(stats["size"], 2)
        self.assertIn("key1", stats["keys"])


if __name__ == "__main__":
    unittest.main()
