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

    def test_nothing_configured_is_not_an_error(self):
        """The production deployment the documentation recommends lands here.

        The interface is served by this same application, so `CORS_ORIGINS` empty and no
        default is the *correct* answer: allow no cross-origin caller. This used to raise,
        and `app/main.py` logged the exception at `error` -- so every recommended install
        wrote an alarm at every boot that no operator could ever clear.
        """
        self.assertEqual(validate_cors_origins("", ""), [])
        # Whitespace is not a request either -- an operator who typed a space meant
        # "unset", and telling them otherwise helps nobody.
        self.assertEqual(validate_cors_origins("   ", ""), [])
        self.assertEqual(validate_cors_origins("", "   "), [])

    def test_blank_but_present_setting_still_raises(self):
        """A setting that names nothing is a different animal from an absent setting.

        `CORS_ORIGINS=","` means somebody asked for something and got nothing. That still
        deserves to be said out loud.
        """
        for setting in (",", "  ,  ", ",,,"):
            with self.subTest(setting=setting):
                with self.assertRaises(ValueError) as cm:
                    validate_cors_origins(setting, "")
                self.assertIn("No valid CORS origins", str(cm.exception))

    def test_malformed_origin_never_widens_to_the_default(self):
        """The fallback must not rescue a list that does not parse."""
        with self.assertRaises(ValueError):
            validate_cors_origins("https://ok.example.com,ftp://bad", "https://fallback.example.com")

    def test_trailing_slash_is_dropped_not_refused(self):
        """The address bar shows a trailing slash, so that is what gets pasted.

        It used to refuse the whole setting over it -- and one bad entry refuses them all,
        so a second, perfectly good origin went down with it. The browser never sends that
        character in `Origin`; there is nothing here to refuse.
        """
        self.assertEqual(
            validate_cors_origins("https://panel.example.com/", ""),
            ["https://panel.example.com"],
        )
        self.assertEqual(
            validate_cors_origins("https://a.example.com/,https://b.example.com", ""),
            ["https://a.example.com", "https://b.example.com"],
        )

    def test_a_real_path_is_still_refused(self):
        """Forgiving the slash is not forgiving a URL that is not an origin."""
        for setting in (
            "https://example.com/app",
            "https://example.com/?x=1",
            "https://example.com/#fragment",
        ):
            with self.subTest(setting=setting):
                with self.assertRaises(ValueError):
                    validate_cors_origins(setting, "")

    def test_a_default_port_is_dropped(self):
        """`Origin` reads `https://host`. It never reads `https://host:443`.

        The comparison is exact, so an operator who spelled the port out got a CORS error
        in the browser and a line in the log saying the origin had been validated.
        """
        self.assertEqual(
            validate_cors_origins("https://panel.example.com:443", ""),
            ["https://panel.example.com"],
        )
        self.assertEqual(
            validate_cors_origins("http://panel.example.com:80", ""),
            ["http://panel.example.com"],
        )
        # Any other port is part of the origin and stays.
        self.assertEqual(
            validate_cors_origins("http://panel.example.com:8888", ""),
            ["http://panel.example.com:8888"],
        )

    def test_ipv6_keeps_its_brackets(self):
        """`urlparse` hands the host back without them, and `http://::1:8888` is nothing."""
        self.assertEqual(validate_cors_origins("http://[::1]:8888", ""), ["http://[::1]:8888"])
        self.assertEqual(
            validate_cors_origins("https://[2001:db8::1]", ""),
            ["https://[2001:db8::1]"],
        )

    def test_two_spellings_of_one_origin_are_one_entry(self):
        """Normalising is what makes a duplicate possible; the count must not double."""
        self.assertEqual(
            validate_cors_origins("https://panel.example.com/,https://panel.example.com:443", ""),
            ["https://panel.example.com"],
        )


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
