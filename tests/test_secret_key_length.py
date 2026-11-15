"""A hand-set SECRET_KEY that is too short is named at boot, without anything derived from it."""

import unittest
from unittest.mock import patch

import app.main as app_main


class AShortSecretKeyIsNamedAtBoot(unittest.TestCase):
    def test_a_short_key_is_warned_about_without_its_length(self):
        with patch.object(app_main, "SECRET_KEY", "hunter2-hunter2"), \
             self.assertLogs("app.main", level="WARNING") as logs:
            app_main._warn_if_secret_key_is_short()
        said = "\n".join(logs.output)
        self.assertIn("SECRET_KEY is shorter than 32 characters", said)
        self.assertNotIn("15", said)
        self.assertNotIn("hunter2", said)

    def test_a_generated_key_is_not_warned_about(self):
        with patch.object(app_main, "SECRET_KEY", "a" * 64), \
             patch.object(app_main._logger, "warning") as warned:
            app_main._warn_if_secret_key_is_short()
        warned.assert_not_called()


if __name__ == "__main__":
    unittest.main()
