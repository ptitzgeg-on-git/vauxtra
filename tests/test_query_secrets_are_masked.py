"""A credential carried in a URL's query string is masked before it is shown or written.

Technitium sends its session token as `?token=` and Pi-hole v5 its API key as `?auth=`.
`requests` quotes the URL it was calling, query string included, in the text of the
exceptions it raises, and both providers wrapped that text into their own refusal: a failed
scan wrote the credential to the journal, and a failed listing of an integration's DNS
records sent it back in the detail of its 502.
"""

import os
import tempfile
import unittest

from app import models
from app.providers.base import ProviderListingRefused
from app.security import redact_query_secrets


class RedactQuerySecretsTests(unittest.TestCase):
    def test_the_pihole_key_is_masked_and_the_other_parameters_are_kept(self) -> None:
        text = "401 Client Error: for url: http://pihole/admin/api.php?list=white&auth=K3yK3yK3y"

        self.assertEqual(
            redact_query_secrets(text),
            "401 Client Error: for url: http://pihole/admin/api.php?list=white&auth=***",
        )

    def test_the_technitium_token_is_masked_in_first_position(self) -> None:
        self.assertEqual(
            redact_query_secrets("url: /api/zones/list?token=abc123&zone=x"),
            "url: /api/zones/list?token=***&zone=x",
        )

    def test_the_name_is_matched_whatever_its_case_and_prefix(self) -> None:
        masked = redact_query_secrets("?Token=a&api_key=b&password=c&client_secret=d&sid=e")

        self.assertEqual(masked, "?Token=***&api_key=***&password=***&client_secret=***&sid=***")

    def test_a_parameter_that_carries_no_credential_is_left_alone(self) -> None:
        """The witness: a mask that ate every parameter would pass the tests above."""
        text = "http://dns/api/zones/records/get?domain=nas.vxlab.test&zone=vxlab.test"

        self.assertEqual(redact_query_secrets(text), text)

    def test_nothing_to_mask_is_nothing_changed(self) -> None:
        self.assertEqual(redact_query_secrets("Connection refused"), "Connection refused")
        self.assertEqual(redact_query_secrets(""), "")
        self.assertEqual(redact_query_secrets(None), "")


class EveryWayOutIsMaskedTests(unittest.TestCase):
    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "mask.test.db")
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def test_a_provider_refusal_never_carries_the_credential(self) -> None:
        refusal = ProviderListingRefused("Technitium: url: /api/zones/list?token=abc123secret")

        self.assertNotIn("abc123secret", str(refusal))
        self.assertIn("token=***", str(refusal))

    def test_the_journal_never_stores_it_whoever_writes_the_line(self) -> None:
        models.add_log("error", "Sync Pi-hole: for url: http://pihole/admin/api.php?auth=K3yK3yK3y")

        conn = models.get_db()
        stored = conn.execute("SELECT message FROM logs ORDER BY id DESC LIMIT 1").fetchone()["message"]
        conn.close()
        self.assertNotIn("K3yK3yK3y", stored)
        self.assertIn("auth=***", stored)


if __name__ == "__main__":
    unittest.main()
