"""A gate nobody has ever seen fail is a claim. This is the proof that this one refuses.

The four defects it exists for were live when it was written, and they arrived in two
shapes the same audit could not catch at once. NPM and Traefik caught `RequestException`
inside the listing and returned `[]` from the handler. Zoraxy and Cloudflare Tunnel
swallowed nothing: their helpers answered an honest `None`, and the listing turned it into
`[]` on the next line. So the rule here is about the value that leaves the method, not
about the handler it left from -- which is the only formulation that catches both.

The last test rebuilds all four original shapes and checks the gate names every one. The
one before it runs the gate against this repository, so `python -m pytest tests/` catches
the next instance even with the CI step removed.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_listing_contract.py"
_spec = importlib.util.spec_from_file_location("check_listing_contract", _GATE_PATH)
listing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(listing)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tree(source: str) -> Path:
    """A throwaway app/providers/ holding one provider module."""
    root = Path(tempfile.mkdtemp())
    providers = root / "app" / "providers"
    providers.mkdir(parents=True)
    (providers / "fake.py").write_text(source, encoding="utf-8")
    return root


HONEST = '''
class FakeProvider:
    def list_hosts(self):
        rules = self._get("/list")
        if not isinstance(rules, list):
            raise ProviderListingRefused("the provider would not list its rules")
        return [self._normalize(rule) for rule in rules]
'''

SWALLOWED_EXCEPTION = '''
class FakeProvider:
    def list_hosts(self):
        try:
            return [self._normalize(rule) for rule in self._get("/list")]
        except requests.RequestException:
            return []
'''

LOST_THE_NONE = '''
class FakeProvider:
    def list_hosts(self):
        config = self._get_configuration()
        if config is None:
            return []
        return self._rules(config)
'''


class TheGateHoldsThisRepositoryTests(unittest.TestCase):

    def test_every_listing_in_this_repository_passes(self):
        """The gate runs here, so the suite catches the next instance on its own."""
        problems, inspected = listing.check(REPO_ROOT)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(inspected, 10)

    def test_the_gate_looks_at_every_provider_that_lists(self):
        """A gate that inspected nothing would also report no problems."""
        _, inspected = listing.check(REPO_ROOT)
        self.assertEqual(inspected, 12)


class TheGateRefusesBothShapesOfTheDefectTests(unittest.TestCase):

    def test_a_listing_that_raises_is_accepted(self):
        problems, inspected = listing.check(_tree(HONEST))
        self.assertEqual(problems, [])
        self.assertEqual(inspected, 1)

    def test_a_swallowed_exception_is_named(self):
        """NPM's and Traefik's shape: [] returned from the except handler."""
        problems, _ = listing.check(_tree(SWALLOWED_EXCEPTION))
        self.assertEqual(len(problems), 1)
        self.assertIn("list_hosts returns []", problems[0])

    def test_an_honest_none_turned_into_an_empty_list_is_named(self):
        """Zoraxy's and Cloudflare Tunnel's shape, which no except handler contains."""
        problems, _ = listing.check(_tree(LOST_THE_NONE))
        self.assertEqual(len(problems), 1)
        self.assertIn("list_hosts returns []", problems[0])

    def test_none_and_false_are_empty_answers_too(self):
        """A listing whose caller does `or []` reads None exactly as it reads []."""
        for spelling in ("None", "False"):
            with self.subTest(spelling=spelling):
                source = HONEST.replace(
                    'raise ProviderListingRefused("the provider would not list its rules")',
                    f"return {spelling}",
                )
                problems, _ = listing.check(_tree(source))
                self.assertEqual(len(problems), 1)
                self.assertIn(f"list_hosts returns {spelling}", problems[0])

    def test_a_method_that_is_not_a_listing_is_left_alone(self):
        """`_get` answering None is the honest helper the listing must not flatten."""
        source = "class FakeProvider:\n    def _get(self, path):\n        return None\n"
        problems, inspected = listing.check(_tree(source))
        self.assertEqual(problems, [])
        self.assertEqual(inspected, 0)


if __name__ == "__main__":
    unittest.main()
