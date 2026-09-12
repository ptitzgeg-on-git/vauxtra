"""One rule about names, written in two languages, held to one table.

`app/validators.py` decides what the API accepts; `frontend/src/lib/hostname.ts` decides
what the panel lets an operator send. There has to be a copy in the panel -- it cannot ask
the server on every keystroke -- and two copies of one rule is exactly the arrangement
where one of them quietly stops agreeing.

So neither file owns the rule: `frontend/src/lib/hostname.cases.json` does. This file runs
the table through Python, `frontend/src/lib/hostname.test.ts` runs the same table through
TypeScript, and each is in a different CI job, so a change on one side alone turns the
other red.

The `fqdn` block is the rule neither field can hold: each half may sit inside its own
253-character limit while the name they make sits outside it. Both halves of every pair
there are valid on their own, so nothing but that rule can refuse them.

Two things the table cannot check are checked here instead: that both files list the same
codes in the same order, and that every code has a sentence in all eight locale files --
because a code with no key reaches the operator as `expose.validation.domain.charset`.

The one place the two sides are allowed to differ is which code a malformed address earns:
Python asks `ipaddress.ip_address`, the panel has no such thing and reads the shape. They
never differ on the answer that matters, valid or not, and `domain_verdict_only` holds
those values with the verdict alone asserted.
"""

import json
import re
import unittest
from pathlib import Path

from app.validators import (
    DOMAIN_PROBLEMS,
    DOMAIN_REASONS,
    FQDN_PROBLEMS,
    FQDN_REASONS,
    SUBDOMAIN_PROBLEMS,
    SUBDOMAIN_REASONS,
    domain_problem,
    fqdn_problem,
    is_valid_domain,
    is_valid_fqdn,
    is_valid_subdomain,
    subdomain_problem,
)

_ROOT = Path(__file__).resolve().parent.parent
_MIRROR = _ROOT / "frontend" / "src" / "lib" / "hostname.ts"
_CASES = _ROOT / "frontend" / "src" / "lib" / "hostname.cases.json"
_LOCALES = _ROOT / "frontend" / "src" / "locales"

_TABLE = json.loads(_CASES.read_text(encoding="utf-8"))


def _mirror_list(name: str) -> tuple[str, ...]:
    """A `..._PROBLEMS` array as the TypeScript file declares it."""
    source = _MIRROR.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.S)
    if not block:
        raise AssertionError(f"{name} not found in {_MIRROR.name}")
    return tuple(re.findall(r"'([a-z_]+)'", block.group(1)))


class SubdomainRuleTests(unittest.TestCase):
    """Every case of the shared table, through the Python side."""

    def test_each_case_breaks_the_rule_it_says_it_breaks(self):
        for case in _TABLE["subdomain"]:
            with self.subTest(value=case["value"], why=case["why"]):
                self.assertEqual(
                    subdomain_problem(case["value"], allow_wildcard=case["allow_wildcard"]),
                    case["problem"],
                )

    def test_the_boolean_says_the_same_thing_as_the_code(self):
        """`is_valid_subdomain` is `subdomain_problem(...) is None`, and must stay that."""
        for case in _TABLE["subdomain"]:
            with self.subTest(value=case["value"]):
                self.assertEqual(
                    is_valid_subdomain(case["value"], allow_wildcard=case["allow_wildcard"]),
                    case["problem"] is None,
                )

    def test_the_table_exercises_every_code(self):
        """A table that never reaches `hyphen_edge` would let `hyphen_edge` rot unnoticed."""
        reached = {case["problem"] for case in _TABLE["subdomain"]} - {None}
        self.assertEqual(reached, set(SUBDOMAIN_PROBLEMS))

    def test_it_accepts_something(self):
        """The negative control: a table of only refusals would pass a function that refuses all."""
        accepted = [c for c in _TABLE["subdomain"] if c["problem"] is None]
        self.assertGreaterEqual(len(accepted), 5)


class DomainRuleTests(unittest.TestCase):
    def test_each_case_breaks_the_rule_it_says_it_breaks(self):
        for case in _TABLE["domain"]:
            with self.subTest(value=case["value"], why=case["why"]):
                self.assertEqual(
                    domain_problem(case["value"], require_dot=case["require_dot"]),
                    case["problem"],
                )

    def test_the_boolean_says_the_same_thing_as_the_code(self):
        for case in _TABLE["domain"]:
            with self.subTest(value=case["value"]):
                self.assertEqual(
                    is_valid_domain(case["value"], require_dot=case["require_dot"]),
                    case["problem"] is None,
                )

    def test_the_verdict_only_cases_are_refused_here_too(self):
        """The codes may differ across the two sides; valid-or-not may not."""
        for case in _TABLE["domain_verdict_only"]:
            with self.subTest(value=case["value"], why=case["why"]):
                self.assertEqual(
                    is_valid_domain(case["value"], require_dot=case["require_dot"]),
                    case["valid"],
                )

    def test_the_table_exercises_every_code(self):
        reached = {case["problem"] for case in _TABLE["domain"]} - {None}
        self.assertEqual(reached, set(DOMAIN_PROBLEMS))

    def test_it_accepts_something(self):
        accepted = [c for c in _TABLE["domain"] if c["problem"] is None]
        self.assertGreaterEqual(len(accepted), 5)


class FqdnRuleTests(unittest.TestCase):
    """The rule about the pair, which neither field validator can reach on its own."""

    def test_each_pair_breaks_the_rule_it_says_it_breaks(self):
        for case in _TABLE["fqdn"]:
            with self.subTest(why=case["why"]):
                self.assertEqual(fqdn_problem(case["subdomain"], case["domain"]), case["problem"])

    def test_both_halves_are_valid_on_their_own(self):
        """Otherwise a field rule fires first and this table measures nothing."""
        for case in _TABLE["fqdn"]:
            with self.subTest(why=case["why"]):
                self.assertIsNone(subdomain_problem(case["subdomain"], allow_wildcard=True))
                self.assertIsNone(domain_problem(case["domain"]))

    def test_the_boolean_says_the_same_thing_as_the_code(self):
        for case in _TABLE["fqdn"]:
            with self.subTest(why=case["why"]):
                self.assertEqual(
                    is_valid_fqdn(case["subdomain"], case["domain"]),
                    case["problem"] is None,
                )

    def test_the_table_exercises_every_code(self):
        reached = {case["problem"] for case in _TABLE["fqdn"]} - {None}
        self.assertEqual(reached, set(FQDN_PROBLEMS))

    def test_it_accepts_something(self):
        accepted = [c for c in _TABLE["fqdn"] if c["problem"] is None]
        self.assertGreaterEqual(len(accepted), 5)

    def test_the_limit_is_where_it_says_it_is(self):
        """253 passes and 254 does not, measured on the name, not on either half."""
        self.assertIsNone(fqdn_problem("a" * 241, "example.com"))
        self.assertEqual(fqdn_problem("a" * 242, "example.com"), "too_long")


class MirrorTests(unittest.TestCase):
    """The panel's copy declares the same codes, in the same order, as the server's."""

    def test_the_subdomain_codes_match(self):
        self.assertEqual(_mirror_list("SUBDOMAIN_PROBLEMS"), SUBDOMAIN_PROBLEMS)

    def test_the_domain_codes_match(self):
        self.assertEqual(_mirror_list("DOMAIN_PROBLEMS"), DOMAIN_PROBLEMS)

    def test_the_fqdn_codes_match(self):
        self.assertEqual(_mirror_list("FQDN_PROBLEMS"), FQDN_PROBLEMS)

    def test_it_would_notice_a_code_that_is_only_on_one_side(self):
        """The positive control: the reader above must be reading, not returning the input."""
        self.assertNotEqual(_mirror_list("SUBDOMAIN_PROBLEMS"), DOMAIN_PROBLEMS)


class ReasonTests(unittest.TestCase):
    """Every code answers in English for a client with no translations, and in eight for the panel."""

    def test_every_code_has_an_english_sentence(self):
        self.assertEqual(tuple(sorted(SUBDOMAIN_REASONS)), tuple(sorted(SUBDOMAIN_PROBLEMS)))
        self.assertEqual(tuple(sorted(DOMAIN_REASONS)), tuple(sorted(DOMAIN_PROBLEMS)))
        self.assertEqual(tuple(sorted(FQDN_REASONS)), tuple(sorted(FQDN_PROBLEMS)))

    def test_every_code_has_a_key_in_every_locale(self):
        expected = {f"expose.validation.subdomain.{code}" for code in SUBDOMAIN_PROBLEMS}
        expected |= {f"expose.validation.domain.{code}" for code in DOMAIN_PROBLEMS}
        expected |= {f"expose.validation.fqdn.{code}" for code in FQDN_PROBLEMS}
        for path in sorted(_LOCALES.glob("*.json")):
            entries = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(locale=path.stem):
                self.assertEqual(sorted(expected - set(entries)), [])

    def test_no_sentence_is_the_key_itself(self):
        """A file that answers `expose.validation.domain.url` has the key and not the sentence."""
        for path in sorted(_LOCALES.glob("*.json")):
            entries = json.loads(path.read_text(encoding="utf-8"))
            for key, value in entries.items():
                if key.startswith("expose.validation.") and "." in key[len("expose.validation."):]:
                    with self.subTest(locale=path.stem, key=key):
                        self.assertNotEqual(value.strip(), key)
                        self.assertTrue(value.strip())


if __name__ == "__main__":
    unittest.main()
