"""A start with the wrong SECRET_KEY must not wrap the provider passwords a second time.

`_migrate_encrypt_passwords` runs at every start. It took any value it could not decrypt for
plaintext, so a single boot with the wrong key encrypted every password again, and putting
the right key back left them unreadable.
"""

import sqlite3
import unittest

from cryptography.fernet import Fernet

from app import config, models


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE providers (id INTEGER PRIMARY KEY, password TEXT)")
    return conn


class PasswordMigrationWrongKey(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = config.fernet
        self.addCleanup(setattr, config, "fernet", self._orig)

    def test_a_token_from_another_key_is_left_alone(self):
        right, wrong = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
        token = right.encrypt(b"hunter2").decode()
        conn = _conn()
        conn.execute("INSERT INTO providers (id, password) VALUES (1, ?)", (token,))

        config.fernet = wrong
        models._migrate_encrypt_passwords(conn)

        stored = conn.execute("SELECT password FROM providers").fetchone()["password"]
        self.assertEqual(stored, token)
        self.assertEqual(right.decrypt(stored.encode()), b"hunter2")

    def test_plaintext_is_still_encrypted(self):
        conn = _conn()
        conn.execute("INSERT INTO providers (id, password) VALUES (1, 'hunter2')")

        models._migrate_encrypt_passwords(conn)

        stored = conn.execute("SELECT password FROM providers").fetchone()["password"]
        self.assertTrue(config.is_encrypted(stored))
        self.assertEqual(config.decrypt_secret(stored), "hunter2")


if __name__ == "__main__":
    unittest.main()
