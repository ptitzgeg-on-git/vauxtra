"""The generated SECRET_KEY file is never readable by anyone but its owner, not even briefly."""

import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import app.config as config


@unittest.skipUnless(os.name == "posix", "file modes are a POSIX question")
class TheGeneratedKeyFile(unittest.TestCase):
    def test_it_is_created_with_owner_only_permissions(self):
        created_modes: list[int] = []
        real_open = os.open

        def recording_open(path, flags, *args, **kwargs):
            if str(path).endswith(".secret_key") and flags & os.O_CREAT:
                created_modes.append(args[0] if args else kwargs.get("mode"))
            return real_open(path, flags, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "DATA_DIR", directory), \
             patch.dict(os.environ, {"SECRET_KEY": ""}), \
             patch.object(config.os, "open", recording_open):
            key = config._get_or_generate_secret()
            path = os.path.join(directory, ".secret_key")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), key)

        self.assertEqual(created_modes, [0o600])

    def test_an_existing_key_is_read_back_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "DATA_DIR", directory), \
             patch.dict(os.environ, {"SECRET_KEY": ""}):
            first = config._get_or_generate_secret()
            self.assertEqual(config._get_or_generate_secret(), first)


if __name__ == "__main__":
    unittest.main()
