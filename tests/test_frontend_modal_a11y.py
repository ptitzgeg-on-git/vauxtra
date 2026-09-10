"""A modal that is only a div stacked over the page.

Three of them shipped that way: no `role="dialog"`, no `aria-modal`, no Escape, and Tab
walked out of the box into the form underneath. A screen reader announced nothing when they
opened and went on reading the page behind; a keyboard user could tab into that page, type
into it, and have no way back.

Nothing compiles or lints this away, so it is checked here: any file that paints a
full-screen overlay is a modal, and a modal says what it is.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "frontend" / "src"

# The signature of a modal overlay in this codebase: full-screen, in the top layer.
# Centring used to be part of the signature, back when every dialog painted its own
# centred box. It no longer is: a command palette sits at `items-start` near the top of
# the viewport, and a drawer is flush against one edge -- both are still dialogs that
# must be announced and escapable, and both were skipped while this asked for
# `items-center justify-center`.
# `z-50` is what keeps the scrims out: the mobile chrome in `Layout.tsx` is `z-30`,
# below the dialog layer, and nothing under `z-50` is a dialog.
_OVERLAY = re.compile(r'className="[^"]*\bfixed inset-0 z-50\b')


def _modal_files() -> list[Path]:
    return sorted(
        p for p in _SRC.rglob("*.tsx") if _OVERLAY.search(p.read_text(encoding="utf-8"))
    )


class EveryModalSaysItIsOneTests(unittest.TestCase):
    def test_the_scan_still_finds_the_modals(self):
        """If a refactor changes the overlay classes, the checks below stop checking."""
        self.assertGreaterEqual(len(_modal_files()), 4)

    def test_they_declare_a_dialog_role(self):
        for path in _modal_files():
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn('role="dialog"', text)
                self.assertIn('aria-modal="true"', text)

    def test_they_are_named_for_a_screen_reader(self):
        """`aria-modal` without a label announces "dialog" and nothing else."""
        for path in _modal_files():
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(
                    "aria-labelledby=" in text or "aria-label=" in text, path.name
                )

    def test_they_handle_the_keyboard(self):
        """Escape closes, Tab stays inside -- from `useModalDialog`, or by hand."""
        for path in _modal_files():
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(
                    "useModalDialog" in text or "'Escape'" in text,
                    f"{path.name}: no Escape handling and no useModalDialog",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
