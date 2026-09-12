"""The translation files ship as-is; nothing else checks them.

`t()` falls back to printing the key when it cannot find it, so a key the UI asks for and
no locale defines reaches the operator as `settings.api_keys.create_failed` in a toast.
And five French strings were shipped with a plain `?` where the accent belonged --
"?chec de v?rification" -- which no compiler or linter has any reason to notice.

These are static checks over `frontend/src/locales/`: they need no browser and no Node.
`frontend/scripts/check-locale-parity.mjs` covers the same files from the Node side and CI
runs it, but only it can ask `Intl.PluralRules` which forms a language actually has. So the
plural rule lives there and only there, and this file stays on what needs no CLDR.
"""

import json
import re
import unicodedata
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOCALES = _ROOT / "frontend" / "src" / "locales"
_REFERENCE = "en"

# `t('some.key')` with a literal key. A computed key cannot be checked from here.
_T_CALL = re.compile(r"""\bt\(\s*['"]([a-zA-Z0-9_.]+)['"]""")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

# A counted sentence is written once per plural category the language has, so `foo` lives in
# the files as `foo_one` and `foo_other` and never as `foo`. These are the six CLDR category
# names; which of them a given language declares is `check-locale-parity.mjs`'s business.
_PLURAL_SUFFIXES = ("_zero", "_one", "_two", "_few", "_many", "_other")


def _logical(key: str) -> str:
    """The name the UI asks for: `certificates.meta_one` and `_other` are both that sentence."""
    for suffix in _PLURAL_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _load(lang: str) -> dict:
    return json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _languages() -> list[str]:
    return sorted(p.stem for p in _LOCALES.glob("*.json"))


class LocaleFilesTests(unittest.TestCase):
    def test_they_all_carry_the_same_sentences(self):
        """Compared by logical key: Japanese has one form where French has two, by design."""
        reference = {_logical(k) for k in _load(_REFERENCE)}
        for lang in _languages():
            with self.subTest(lang=lang):
                keys = {_logical(k) for k in _load(lang)}
                self.assertEqual(sorted(reference - keys), [], f"{lang}: missing")
                self.assertEqual(sorted(keys - reference), [], f"{lang}: unknown")

    def test_every_counted_sentence_has_an_other_form(self):
        """`other` is the one category every language declares, so it is the safe fallback.

        `t()` answers with `_other` when the exact category is missing, when the caller
        passed no count, and when it passed a string. A file without it would put a raw key
        on the screen in those three cases.
        """
        counted = {
            _logical(k) for k in _load(_REFERENCE) if k.endswith(_PLURAL_SUFFIXES)
        }
        self.assertGreater(len(counted), 20, "the suffix scan found almost nothing")
        for lang in _languages():
            keys = set(_load(lang))
            for base in sorted(counted):
                with self.subTest(lang=lang, key=base):
                    self.assertIn(f"{base}_other", keys)

    def test_no_bare_key_shadows_a_counted_one(self):
        """`t(base)` and `t(base, {count})` reading different strings is a trap, not a feature."""
        for lang in _languages():
            keys = set(_load(lang))
            for key in sorted(keys):
                if key.endswith(_PLURAL_SUFFIXES):
                    with self.subTest(lang=lang, key=key):
                        self.assertNotIn(_logical(key), keys)

    def test_no_value_is_empty(self):
        for lang in _languages():
            for key, value in _load(lang).items():
                with self.subTest(lang=lang, key=key):
                    self.assertTrue(str(value).strip(), f"{lang}: {key} is blank")

    def test_the_placeholders_survive_translation(self):
        """A dropped `{name}` turns a message into one that names nothing."""
        reference = _load(_REFERENCE)
        for lang in _languages():
            for key, value in _load(lang).items():
                with self.subTest(lang=lang, key=key):
                    self.assertEqual(
                        set(_PLACEHOLDER.findall(reference.get(key, ""))),
                        set(_PLACEHOLDER.findall(value)),
                        f"{lang}: {key}",
                    )

    def test_no_accent_was_replaced_by_a_question_mark(self):
        """`?chec de v?rification`: a `?` glued to a letter is a lost accent, not a question."""
        for lang in _languages():
            for key, value in _load(lang).items():
                with self.subTest(lang=lang, key=key):
                    self.assertIsNone(
                        re.search(r"\?[A-Za-z]", value), f"{lang}: {key} = {value!r}"
                    )


class EveryKeyTheUiAsksForExistsTests(unittest.TestCase):
    """A missing key is not an error anywhere: it is printed on screen, verbatim."""

    def _used_keys(self) -> dict[str, str]:
        used: dict[str, str] = {}
        source = _ROOT / "frontend" / "src"
        for path in sorted(source.rglob("*.ts")) + sorted(source.rglob("*.tsx")):
            text = path.read_text(encoding="utf-8")
            for match in _T_CALL.finditer(text):
                used.setdefault(match.group(1), str(path.relative_to(_ROOT)))
        return used

    def test_the_ui_never_asks_for_a_key_no_locale_defines(self):
        # A counted sentence is asked for by its base name and stored under a suffix, so
        # `certificates.meta` is defined by `certificates.meta_other` being there.
        defined = {_logical(k) for k in _load(_REFERENCE)}
        missing = {k: where for k, where in self._used_keys().items() if k not in defined}
        self.assertEqual(missing, {})

    def test_it_would_notice_a_key_that_is_defined_nowhere(self):
        """The control: the lookup above accepts a plural family, not simply anything."""
        defined = {_logical(k) for k in _load(_REFERENCE)}
        self.assertIn("certificates.meta", defined)  # stored as _one / _other
        self.assertNotIn("certificates.meta_nonexistent", defined)

    def test_the_scan_actually_found_the_calls(self):
        """A regex that matches nothing would make the test above pass for free."""
        self.assertGreater(len(self._used_keys()), 100)

class NoWordLostItsAccentsTests(unittest.TestCase):
    """A string can arrive stripped of its accents and still be perfectly valid JSON.

    That is how "Cles API" shipped one line above "Clés API", how pt read "Backup versao" and
    de "Webhook-Eintrage" -- all in `settings.backup.*`, so one edit ate the accents in four
    languages at once. The `?`-for-accent check above cannot see this one: nothing about
    `cles` is malformed, it is simply the wrong spelling of a word the same file spells
    correctly forty lines further down.

    The file is its own dictionary, so no word list has to be maintained and none can be
    forgotten: collect every form the locale writes WITH diacritics, strip them, and look for
    that bare form again in the same file. A hit means one document spells one word two ways.

    Three filters keep the rule about spelling and away from grammar, which it cannot judge:

      * a placeholder name and a URL are not prose. `{detail}`, `{version}` and
        `ssh://usuario@host` are written unaccented on purpose.
      * a diacritic on the LAST letter is a verb ending: `activé`/`active`, `pasó`/`paso`,
        `alterá`/`altera`, `là`/`la`. Both spellings are correct and only the sentence says
        which. An accent in the MIDDLE of a word has no such excuse.
      * under four letters it is a function word, which is never silently stripped alone.

    What survives is `HOMOGRAPHS`: a short list, kept one language at a time because every
    entry is a real pair a human had to look at, and no entry earns its place by being
    inconvenient. Adding to it means asserting that both spellings are correct French,
    Spanish, Portuguese or German -- not that the test is noisy. When it fires on a word
    that is not in it, the locale is wrong until somebody shows otherwise.
    """

    # Both spellings are real words. Checked one by one; the count is deliberately small.
    HOMOGRAPHS = {
        # `connexions actives` (adjective) and `services activés` (participle).
        # `Zero Trust` is Cloudflare's product name, `zéro` the number.
        # `des chiffres` (digits, in a charset rule) is not `chiffrés` (encrypted).
        "fr": {"actives", "chiffres", "zero"},
        # The Spanish interrogatives carry an accent the relative pronouns do not,
        # and `publica` (he publishes) is not `pública` (public).
        "es": {"como", "cual", "cuando", "cuanto", "donde", "quien", "publica"},
        # `pode` (he can) / `pôde` (he could), `publica` / `pública`, as in Spanish.
        "pt": {"pode", "publica"},
        # `konnte`/`könnte`, `wurden`/`würden` and `waren`/`wären` are indicative against
        # subjunctive, `lange` (long) is not `Länge` (length), and `eintragen` (to enter) is
        # not `Einträgen` (the dative plural of `Eintrag`, which this test lowercases first).
        "de": {"eintragen", "konnte", "lange", "waren", "wurden"},
        "nl": set(),
    }

    _PLACEHOLDER_NAME = re.compile(r"\{[^}]*\}")
    _URLISH = re.compile(r"\S*(?:://|\w\.\w{2,4}\b|@)\S*")
    _WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
    _MIN_LETTERS = 4

    @staticmethod
    def _bare(text: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
        )

    @classmethod
    def _prose(cls, value: str) -> str:
        return cls._URLISH.sub(" ", cls._PLACEHOLDER_NAME.sub(" ", value))

    @classmethod
    def _words(cls, value: str) -> list[str]:
        return [w.lower() for w in cls._WORD.findall(cls._prose(value))]

    @classmethod
    def _accented_vocabulary(cls, values) -> dict[str, str]:
        """bare form -> the accented spelling this file already uses, for the judgeable words."""
        vocabulary: dict[str, str] = {}
        for value in values:
            for word in cls._words(value):
                if len(word) < cls._MIN_LETTERS or cls._bare(word) == word:
                    continue
                if cls._bare(word[-1]) != word[-1]:  # verb ending, not a lost accent
                    continue
                vocabulary.setdefault(cls._bare(word), word)
        return vocabulary

    @classmethod
    def _collisions(cls, entries: dict, allowed: set) -> dict[str, str]:
        vocabulary = cls._accented_vocabulary(entries.values())
        found: dict[str, str] = {}
        for key, value in entries.items():
            for word in cls._words(value):
                if word in vocabulary and word not in allowed:
                    found[key] = f"{word!r} is spelled {vocabulary[word]!r} elsewhere"
        return found

    def test_a_word_is_never_spelled_both_with_and_without_its_accents(self):
        for lang, allowed in self.HOMOGRAPHS.items():
            with self.subTest(lang=lang):
                self.assertEqual(self._collisions(_load(lang), allowed), {})

    def test_the_dictionary_it_builds_is_not_empty(self):
        """A vocabulary of zero words would make the test above pass without looking."""
        for lang in self.HOMOGRAPHS:
            if lang == "nl":  # Dutch writes almost no diacritics; six words is all there is
                continue
            with self.subTest(lang=lang):
                self.assertGreater(len(self._accented_vocabulary(_load(lang).values())), 100)

    def test_it_catches_an_accent_that_has_been_removed(self):
        """The positive control: strip one real string and the rule must name that key."""
        entries = _load("fr")
        key = "settings.api_keys.title"
        self.assertEqual(entries[key], "Clés API")  # the string F23 shipped as "Cles API"
        damaged = dict(entries, **{key: self._bare(entries[key])})
        self.assertIn(key, self._collisions(damaged, self.HOMOGRAPHS["fr"]))

    def test_a_verb_ending_is_not_reported(self):
        """The negative control: `activé` next to `active` is grammar, and must stay silent."""
        entries = {"a": "Le service est activé.", "b": "Une connexion active."}
        self.assertEqual(self._collisions(entries, set()), {})

    def test_a_placeholder_name_is_not_read_as_a_word(self):
        """`Prochaine étape : {detail}` must not be read as a misspelling of `détail`."""
        entries = {"a": "Voir le détail complet.", "b": "Prochaine étape : {detail}"}
        self.assertEqual(self._collisions(entries, set()), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
