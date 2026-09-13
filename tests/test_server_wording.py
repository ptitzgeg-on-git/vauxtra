"""The sentences the server writes itself, and the crutch they used to carry.

Fourteen of them read `f"{n} service(s)"`. That is not a plural: it is a note to a reader who
is supposed to pick a form themselves, and what the operator actually saw in the activity log
was "1 service(s)". The panel has had a plural engine since the locale files learned to ask
`Intl.PluralRules`; these strings never go through it, being written on the server and being
English, so they get `app.text` instead.

The last test here is the guard. Nothing refused the crutch before, on either side of the
wire, which is how fourteen of them accumulated.
"""

import ast
import re
import unittest
from pathlib import Path

from app.text import plural, verb

_ROOT = Path(__file__).resolve().parent.parent
_APP = _ROOT / "app"

# An English plural ending glued to the end of a word: "service(s)", "box(es)", "entr(ies)".
# Only these three, because this guard reads server strings and those are English. Anything
# wider also names `REFERENCES services(id)` and `INTO domains (name)`, SQL being written in
# the same string literals as prose.
_CRUTCH = re.compile(r"\w\((?:s|es|ies)\)")

# And only in a sentence that counts something: a literal digit, or a value interpolated into
# it. Without that, `http(s)` and any other protocol notation are indistinguishable.
_COUNTS = re.compile(r"\d|\{")

# `http(s)` counts nothing even when a number is elsewhere in the same sentence.
_ALLOWED = re.compile(r"https?\(s\)")


class PluralTests(unittest.TestCase):
    def test_one_takes_the_singular(self):
        self.assertEqual(plural(1, "service"), "1 service")

    def test_zero_takes_the_plural_because_english_does(self):
        # The eight locale files cannot assume this: French and Portuguese put zero in the
        # singular. These sentences are English only, which is the whole reason they may.
        self.assertEqual(plural(0, "service"), "0 services")

    def test_more_than_one_takes_the_plural(self):
        self.assertEqual(plural(4, "zone"), "4 zones")

    def test_a_noun_that_does_not_take_a_plain_s_can_say_so(self):
        self.assertEqual(plural(2, "entry", "entries"), "2 entries")
        self.assertEqual(plural(1, "entry", "entries"), "1 entry")

    def test_the_verb_agrees_with_the_same_count(self):
        self.assertEqual(verb(1, "uses", "use"), "uses")
        self.assertEqual(verb(0, "uses", "use"), "use")
        self.assertEqual(verb(3, "uses", "use"), "use")


class DependentsSentenceTests(unittest.TestCase):
    """The 409 body of deleting a provider services still point at.

    It is the sentence this whole change exists for: an operator meets it at the moment they
    are about to remove a target, and it used to read "1 service(s) still use" with three
    more disagreements in the paragraph under it.
    """

    def _describe(self, dependents, templates=(), webhooks=()):
        from app.api.providers import _describe_provider_removal

        # `templates` and `webhooks` default here and nowhere else. The production helper
        # takes both positionally on purpose: a caller that forgets one is how the template
        # half of this sentence went unwritten for as long as it did. Most cases below are
        # about the service half, and an empty list is what they mean.
        return _describe_provider_removal(
            "cloudflare-home", dependents, list(templates), list(webhooks)
        )

    def test_a_single_dependent_reads_as_one(self):
        text = self._describe([{"id": 1, "fqdn": "a.example.com", "still_published": False}])
        self.assertIn('1 service still uses "cloudflare-home".', text)
        self.assertIn("1 of them has no other target", text)
        self.assertIn("it keeps the public hostname", text)
        self.assertIn("stops being published", text)

    def test_several_dependents_read_as_many(self):
        text = self._describe([
            {"id": 1, "fqdn": "a.example.com", "still_published": False},
            {"id": 2, "fqdn": "b.example.com", "still_published": False},
        ])
        self.assertIn('2 services still use "cloudflare-home".', text)
        self.assertIn("2 of them have no other target", text)
        self.assertIn("they keep the public hostname", text)

    def test_a_service_kept_by_another_target_agrees_too(self):
        text = self._describe([{"id": 1, "fqdn": "a.example.com", "still_published": True}])
        self.assertIn("1 goes on being published by its other targets.", text)

    def test_two_of_them_agree_too(self):
        text = self._describe([
            {"id": 1, "fqdn": "a.example.com", "still_published": True},
            {"id": 2, "fqdn": "b.example.com", "still_published": True},
        ])
        self.assertIn("2 go on being published by their other targets.", text)

    def test_a_single_template_agrees_with_itself(self):
        # Its own sentence, and its own three agreements: the noun, the verb that follows it,
        # and the pronoun at the end. Nothing above it is reused, because nothing above it is
        # true of a template.
        text = self._describe([], [{"id": 1, "name": "standard", "roles": ["proxy"]}])
        self.assertIn('1 service template names "cloudflare-home"', text)
        self.assertIn("loses that choice when it goes", text)
        self.assertIn("the next service built from it simply starts with no provider", text)

    def test_several_templates_agree_too(self):
        text = self._describe([], [
            {"id": 1, "name": "standard", "roles": ["proxy"]},
            {"id": 2, "name": "tunnelled", "roles": ["tunnel"]},
        ])
        self.assertIn('2 service templates name "cloudflare-home"', text)
        self.assertIn("lose that choice when it goes", text)
        self.assertIn("the next service built from them simply starts with no provider", text)

    def test_a_single_webhook_agrees_with_itself(self):
        # The third kind of dependent and the third paragraph. A webhook is not published
        # and is not blanked either, so the one thing it needs said is the one thing the
        # other two never had to: it stays switched on.
        text = self._describe([], [], [{"id": 1, "name": "on-call", "enabled": True}])
        self.assertIn('1 notification webhook is scoped to "cloudflare-home"', text)
        self.assertIn("loses that target when it goes", text)
        self.assertIn("1 is still switched on and goes on looking armed", text)

    def test_several_webhooks_agree_too(self):
        text = self._describe([], [], [
            {"id": 1, "name": "on-call", "enabled": True},
            {"id": 2, "name": "pager", "enabled": True},
        ])
        self.assertIn('2 notification webhooks are scoped to "cloudflare-home"', text)
        self.assertIn("lose that target when it goes", text)
        self.assertIn("2 are still switched on and go on looking armed", text)

    def test_a_webhook_already_switched_off_is_not_called_armed(self):
        # The sentence is about a rule that looks alive and is not. One that was already
        # off looks exactly as dead as it is, and saying otherwise would be the invention
        # this whole paragraph exists to avoid.
        text = self._describe([], [], [{"id": 1, "name": "on-call", "enabled": False}])
        self.assertIn('1 notification webhook is scoped to "cloudflare-home"', text)
        self.assertNotIn("switched on", text)

    def test_a_webhook_alone_is_never_offered_a_withdrawal(self):
        # Same reason as a template: nothing was ever published from it.
        text = self._describe([], [], [{"id": 1, "name": "on-call", "enabled": True}])
        self.assertNotIn("withdraw=true", text)
        self.assertIn("?force=true to delete it anyway", text)

    def test_a_template_alone_is_never_offered_a_withdrawal(self):
        # `?withdraw=true` asks the provider to take its records down. A template put no
        # record anywhere, so offering it is offering to undo something that never happened.
        text = self._describe([], [{"id": 1, "name": "standard", "roles": ["dns"]}])
        self.assertNotIn("withdraw=true", text)
        self.assertIn("?force=true to delete it anyway", text)


class NoStringWritesItsOwnPluralTests(unittest.TestCase):
    """The guard: `(s)` next to a number, in any string literal under `app/`.

    Read from the AST rather than line by line, so a sentence split across three source lines
    is judged as the one sentence it becomes -- which is exactly how two of the fourteen had
    hidden, their `day(s)` sitting on a different line from the number feeding it.
    """

    @staticmethod
    def _docstrings(tree):
        # A docstring is read by whoever maintains the file, never by an operator, and this
        # rule is about what the server writes out. Without the exception `app/text.py` fails
        # it by explaining itself: its prose quotes the crutch it was written to remove.
        holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, holders) or not node.body:
                continue
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
        return found

    def _offenders(self):
        found = []
        for path in sorted(_APP.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = self._docstrings(tree)
            for node in ast.walk(tree):
                if id(node) in docstrings:
                    continue
                if isinstance(node, ast.JoinedStr):
                    text = "".join(
                        p.value if isinstance(p, ast.Constant) and isinstance(p.value, str)
                        else "{}"
                        for p in node.values
                    )
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = node.value
                else:
                    continue
                if not _COUNTS.search(text):
                    continue
                for match in _CRUTCH.finditer(text):
                    if _ALLOWED.search(text[max(0, match.start() - 6) : match.end()]):
                        continue
                    found.append(
                        f"{path.relative_to(_ROOT)}:{node.lineno}: {match.group(0)!r} in {text!r}"
                    )
        return found

    def test_no_server_string_writes_its_plural_by_hand(self):
        self.assertEqual(self._offenders(), [])

    @staticmethod
    def _flags(sample: str) -> bool:
        if not _COUNTS.search(sample):
            return False
        for match in _CRUTCH.finditer(sample):
            if not _ALLOWED.search(sample[max(0, match.start() - 6) : match.end()]):
                return True
        return False

    def test_it_would_catch_one(self):
        """The control: a rule that matches nothing passes the test above for free."""
        for sample in (
            "{n} service(s)",
            "expires in 3 day(s)",
            "{len(x)} zone(s) accessible",
            "2 box(es) left",
        ):
            with self.subTest(sample=sample):
                self.assertTrue(self._flags(sample))

    def test_it_leaves_a_real_parenthetical_alone(self):
        for sample in (
            "Checks (24 h)",
            "[CertExpiry] WARNING: '{name}' (ID {cert_id})",
            "{n} http(s) URLs, one per line",
            "Keep logs for (days)",
            "INSERT OR IGNORE INTO domains (name) VALUES (?)",
            "FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE",
            "SELECT COUNT(*) FROM services WHERE provider_id=?",
        ):
            with self.subTest(sample=sample):
                self.assertFalse(self._flags(sample), f"false positive on {sample!r}")


if __name__ == "__main__":
    unittest.main()
