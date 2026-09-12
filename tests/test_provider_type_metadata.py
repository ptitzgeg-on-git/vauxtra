"""What a provider type says about itself, in the three places it gets said.

`PROVIDER_TYPES["npm"]` carried `"description": "Nginx Proxy Manager"`, which is its label
repeated. The panel never showed it, because every locale overrides the description and the
override was a real sentence; the duplicate only ever left through `GET /api/providers/types`
and through the frontend's offline fallback. So the one reader who had no translation to fall
back on -- an MCP client, a third-party script, anyone reading the API -- was told the name
twice and nothing about what the provider does.

Three checks, one per place the sentence can go wrong.
"""

import json
import os
import re
import unittest

from app.providers.factory import PROVIDER_TYPES

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCALES = os.path.join(_ROOT, "frontend", "src", "locales")
_CONSTANTS = os.path.join(
    _ROOT, "frontend", "src", "components", "features", "providers", "providerConstants.ts"
)


def _fallback_descriptions() -> dict:
    """The `descByType` map the panel uses when `/api/providers/types` is unreachable."""
    with open(_CONSTANTS, encoding="utf-8") as fh:
        src = fh.read()
    bloc = re.search(
        r"export const descByType: Record<string, string> = \{(.*?)\n\};", src, re.S
    )
    assert bloc, "descByType is no longer in providerConstants.ts in the expected shape"
    out = {}
    for line in bloc.group(1).splitlines():
        m = re.match(r"\s*(\w+):\s*(['\"])(.*)\2,\s*$", line)
        if m:
            out[m.group(1)] = m.group(3).replace("\'", "'").replace('\\"', '"')
    return out


class ProviderTypeMetadata(unittest.TestCase):
    def test_no_description_merely_repeats_the_label(self):
        for t, meta in PROVIDER_TYPES.items():
            with self.subTest(type=t):
                label = meta["label"].strip().lower()
                desc = meta["description"].strip().lower()
                self.assertNotEqual(
                    desc,
                    label,
                    f"{t}: the description repeats the label instead of saying what it does",
                )

    def test_the_frontend_fallback_says_the_same_thing_as_the_api(self):
        """The fallback exists to stand in for the API, so it has to agree with it.

        Two copies of the same sentence in two languages of source is a drift waiting to
        happen: the one that gets edited is whichever the next change happens to touch.
        """
        fallback = _fallback_descriptions()
        self.assertTrue(fallback, "no fallback descriptions were parsed")
        for t, meta in PROVIDER_TYPES.items():
            with self.subTest(type=t):
                self.assertIn(t, fallback, f"{t} has no offline fallback description")
                self.assertEqual(
                    fallback[t],
                    meta["description"],
                    f"{t}: the panel's fallback and the API disagree on the description",
                )

    def test_every_type_has_a_translated_description_in_every_language(self):
        """Locale parity catches a key missing from seven files, not from all eight."""
        for name in sorted(os.listdir(_LOCALES)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(_LOCALES, name), encoding="utf-8") as fh:
                keys = json.load(fh)
            for t in PROVIDER_TYPES:
                with self.subTest(locale=name, type=t):
                    self.assertIn(
                        f"providers.type.{t}.desc",
                        keys,
                        f"{name} has no description for the {t} provider",
                    )


if __name__ == "__main__":
    unittest.main()
