"""Test suite for security enhancements and optimizations."""

import unittest
from app.security import validate_cors_origins, sanitize_domain, validate_password_strength
from app.cache import RequestCache
from app.errors import ValidationError, ProviderException, ErrorCode


class TestCORSValidation(unittest.TestCase):
    """Test CORS origin validation."""
    
    def test_valid_cors_origins(self):
        """Valid CORS origins should be accepted."""
        origins_str = "http://localhost:5173,https://example.com"
        result = validate_cors_origins(origins_str, "")
        self.assertEqual(len(result), 2)
        self.assertIn("http://localhost:5173", result)
        self.assertIn("https://example.com", result)
    
    def test_invalid_scheme(self):
        """Invalid scheme should raise ValueError."""
        with self.assertRaises(ValueError) as cm:
            validate_cors_origins("ftp://example.com", "")
        self.assertIn("Invalid scheme", str(cm.exception))
    
    def test_wildcard_rejected(self):
        """Wildcard origins should be rejected."""
        with self.assertRaises(ValueError) as cm:
            validate_cors_origins("http://*.example.com", "")
        self.assertIn("Wildcard", str(cm.exception))
    
    def test_port_validation(self):
        """Valid ports should be accepted."""
        origins_str = "http://localhost:8888,https://example.com:443"
        result = validate_cors_origins(origins_str, "")
        self.assertEqual(len(result), 2)
    
    def test_invalid_port(self):
        """Invalid ports should raise ValueError."""
        with self.assertRaises(ValueError) as cm:
            validate_cors_origins("http://localhost:99999", "")
        self.assertIn("port out of range", str(cm.exception).lower())
    
    def test_fallback_to_default(self):
        """Empty origins_str should use default."""
        default = "http://localhost:5173,http://127.0.0.1:5173"
        result = validate_cors_origins("", default)
        self.assertEqual(len(result), 2)


class TestDomainSanitization(unittest.TestCase):
    """Test domain sanitization."""
    
    def test_valid_domain(self):
        """Valid domains should pass through."""
        self.assertEqual(sanitize_domain("app.example.com"), "app.example.com")
    
    def test_removes_special_chars(self):
        """Special characters should be removed (dots preserved for domains)."""
        self.assertEqual(sanitize_domain("app../../../etc"), "app.etc")  # Dots preserved
    
    def test_wildcard_allowed(self):
        """Wildcard in domain should be preserved."""
        self.assertEqual(sanitize_domain("*.example.com"), "*.example.com")
    
    def test_collapses_dots(self):
        """Multiple consecutive dots should be collapsed."""
        self.assertEqual(sanitize_domain("app..example.com"), "app.example.com")


class TestPasswordValidation(unittest.TestCase):
    """Test password strength validation."""
    
    def test_strong_password(self):
        """Strong password should pass."""
        is_valid, msg = validate_password_strength("SecurePass123!")
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")
    
    def test_weak_length(self):
        """Short password should fail."""
        is_valid, msg = validate_password_strength("Short1!")
        self.assertFalse(is_valid)
        self.assertIn("at least", msg)
    
    def test_missing_uppercase(self):
        """Missing uppercase should fail."""
        is_valid, msg = validate_password_strength("password123!")
        self.assertFalse(is_valid)
        self.assertIn("uppercase", msg)
    
    def test_missing_digit(self):
        """Missing digit should fail."""
        is_valid, msg = validate_password_strength("StrongPassword!")
        self.assertFalse(is_valid)
        self.assertIn("digit", msg)


class TestRequestCache(unittest.TestCase):
    """Test request-scoped caching."""
    
    def setUp(self):
        self.cache = RequestCache()
    
    def test_cache_hit(self):
        """Cached value should be retrieved."""
        self.cache.set("key1", "value1")
        self.assertEqual(self.cache.get("key1"), "value1")
    
    def test_cache_miss(self):
        """Missing key should return None."""
        self.assertIsNone(self.cache.get("nonexistent"))
    
    def test_ttl_expiration(self):
        """Expired cache entries should be cleared."""
        self.cache.set("key1", "value1", ttl=0.01)
        import time
        time.sleep(0.1)
        self.assertIsNone(self.cache.get("key1"))
    
    def test_get_or_compute(self):
        """Missing key should compute value."""
        result = self.cache.get_or_compute("key1", lambda: "computed")
        self.assertEqual(result, "computed")
        # Second call should hit cache
        result = self.cache.get_or_compute("key1", lambda: "recomputed")
        self.assertEqual(result, "computed")
    
    def test_clear(self):
        """Clear should remove all entries."""
        self.cache.set("key1", "val1")
        self.cache.set("key2", "val2")
        self.cache.clear()
        self.assertIsNone(self.cache.get("key1"))
        self.assertIsNone(self.cache.get("key2"))
    
    def test_stats(self):
        """Stats should reflect cache state."""
        self.cache.set("key1", "val1")
        self.cache.set("key2", "val2")
        stats = self.cache.stats()
        self.assertEqual(stats["size"], 2)
        self.assertIn("key1", stats["keys"])


class TestErrorHandling(unittest.TestCase):
    """Test unified error handling."""
    
    def test_validation_error(self):
        """ValidationError should have correct format."""
        err = ValidationError("subdomain", "must be lowercase")
        self.assertEqual(err.code, ErrorCode.INVALID_INPUT)
        self.assertIn("subdomain", err.message)
    
    def test_provider_exception(self):
        """ProviderException should capture provider info."""
        err = ProviderException("adguard", "add_rewrite", "Connection timeout")
        self.assertEqual(err.provider_type, "adguard")
        self.assertEqual(err.operation, "add_rewrite")
        self.assertIn("adguard", err.message)
    
    def test_error_to_http_exception(self):
        """VauxtraException should convert to HTTPException."""
        err = ValidationError("port", "invalid range")
        http_err = err.to_http_exception()
        self.assertEqual(http_err.status_code, 422)
        self.assertIn("code", http_err.detail)
        self.assertIn("message", http_err.detail)


if __name__ == "__main__":
    unittest.main()
