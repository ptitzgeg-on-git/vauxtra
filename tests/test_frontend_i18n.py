"""The translation files ship as-is; nothing else checks them.

`t()` falls back to printing the key when it cannot find it, so a key the UI asks for and
no locale defines reaches the operator as `settings.api_keys.create_failed` in a toast.
And five French strings were shipped with a plain `?` where the accent belonged --
"?chec de v?rification" -- which no compiler or linter has any reason to notice.

These are static checks over `frontend/src/locales/`: they need no browser and no Node.
`frontend/scripts/check-locale-parity.mjs` covers the key parity too, but the CI
workflow only runs `i18n:quality`, never `i18n:check` -- so nothing gated it.
"""

import json
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOCALES = _ROOT / "frontend" / "src" / "locales"
_REFERENCE = "en"

# `t('some.key')` with a literal key. A computed key cannot be checked from here.
_T_CALL = re.compile(r"""\bt\(\s*['"]([a-zA-Z0-9_.]+)['"]""")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def _load(lang: str) -> dict:
    return json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _languages() -> list[str]:
    return sorted(p.stem for p in _LOCALES.glob("*.json"))


class LocaleFilesTests(unittest.TestCase):
    def test_they_all_carry_the_same_keys(self):
        reference = set(_load(_REFERENCE))
        for lang in _languages():
            with self.subTest(lang=lang):
                keys = set(_load(lang))
                self.assertEqual(sorted(reference - keys), [], f"{lang}: missing")
                self.assertEqual(sorted(keys - reference), [], f"{lang}: unknown")

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
        defined = set(_load(_REFERENCE))
        missing = {k: where for k, where in self._used_keys().items() if k not in defined}
        self.assertEqual(missing, {})

    def test_the_scan_actually_found_the_calls(self):
        """A regex that matches nothing would make the test above pass for free."""
        self.assertGreater(len(self._used_keys()), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
