"""Two buttons in this interface empty the whole database. Only one of them said so.

`POST /api/reset` and `POST /api/restore` both delete every row of the same sixteen tables
(`_RESTORE_WIPE_TABLES` in `app/api/backup.py`, and the matching statement in
`reset_all`). The confirm dialog can make the operator type a word before the button
enables -- `requireText` -- and the reset button used it. The restore button did not: a
backup file picked by mistake in the file chooser was two clicks from an emptied instance,
and the only thing between them was a dialog that looked exactly like the eleven dialogs
that delete one named object.

Worse, `ConfirmDialog.tsx` documented `requireText` as guarding "reset, restore, deleting a
provider with services" while the code passed it for reset alone. The comment was the only
record of the intent, and it was wrong.

So the intent lives here instead, where it is executed. The rule this file holds:

  * a call site that empties the database asks the operator to type a word;
  * a call site that deletes one named object does NOT -- a word asked for everything is a
    word typed without reading, and the dialog already names the thing;
  * the set of call sites that ask is closed, so neither half can drift alone.

The setup wizard's restore is the one exception, and it is pinned rather than waived: see
`TheWizardRestoreIsTheOneExceptionTests`.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "frontend" / "src"

# The two routes that delete rows nobody selected.
_WIPING_ROUTES = ("/reset", "/restore")

# `api.post` and the route literal are regularly separated by a multi-line generic:
#     api.post<{
#       ok: boolean;
#     }>('/restore', { ... })
# so the route is found first and the call is recognised by what precedes it.
_ROUTE = re.compile("'(" + "|".join(re.escape(r) for r in _WIPING_ROUTES) + ")'")
# Two questions, two regexes. `Services.tsx` passes a computed value
# (`count > 3 ? String(count) : undefined`), so "does this dialog ask for a word at all"
# cannot be answered by looking for a quoted literal.
_ASKS_FOR_A_WORD = re.compile(r"\brequireText:")
_REQUIRE_TEXT = re.compile(r"requireText:\s*'([^']*)'")
_DANGER = re.compile(r"variant:\s*'danger'")


def _sources() -> list[Path]:
    """Every hand-written frontend source, tests excluded -- they pass `requireText` on purpose."""
    return sorted(
        p
        for p in _SRC.rglob("*.ts*")
        if p.suffix in {".ts", ".tsx"} and ".test." not in p.name
    )


def _callers_of_the_wiping_routes() -> dict[str, str]:
    """{relative path: file text} for every source that posts to /reset or /restore."""
    found = {}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for match in _ROUTE.finditer(text):
            # 200 characters is wider than the longest generic in the codebase and narrower
            # than the distance to the previous statement.
            if "api.post" in text[max(0, match.start() - 200) : match.start()]:
                found[path.relative_to(_SRC).as_posix()] = text
                break
    return found


def _confirm_option_blocks(text: str) -> list[str]:
    """The `{ ... }` handed to each `confirm(...)`, brace-matched rather than line-matched."""
    blocks = []
    for match in re.finditer(r"\bconfirm\(\s*\{", text):
        start = text.index("{", match.start())
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : i + 1])
                    break
    return blocks


# What each caller of a wiping route must do, and why.
_WIPING_CALL_SITES = {
    "components/features/settings/data/RestoreSection.tsx": "RESTORE",
    "components/features/settings/DataTab.tsx": "RESET",
}
_WIZARD_RESTORE = "components/features/setup/RestoreStep.tsx"


class EmptyingTheDatabaseAsksForAWordTests(unittest.TestCase):
    def test_the_scan_still_finds_the_calls(self):
        """A renamed route or a new HTTP helper would leave the checks below checking nothing."""
        self.assertEqual(
            set(_callers_of_the_wiping_routes()),
            set(_WIPING_CALL_SITES) | {_WIZARD_RESTORE},
            "the callers of /reset and /restore moved; decide for each new one whether it "
            "empties a database the operator can still be surprised by, then update this list",
        )

    def test_each_dashboard_call_site_asks_for_its_word(self):
        callers = _callers_of_the_wiping_routes()
        for relative, word in _WIPING_CALL_SITES.items():
            with self.subTest(file=relative):
                blocks = _confirm_option_blocks(callers[relative])
                self.assertTrue(blocks, f"{relative} empties the database behind no confirm at all")
                words = [m.group(1) for b in blocks for m in _REQUIRE_TEXT.finditer(b)]
                self.assertIn(
                    word,
                    words,
                    f"{relative} empties sixteen tables; its dialog must ask the operator to "
                    f"type {word!r} first",
                )

    def test_that_dialog_is_the_destructive_one(self):
        """The typed word belongs to the confirm that wipes, not to some other confirm in the file."""
        callers = _callers_of_the_wiping_routes()
        for relative, word in _WIPING_CALL_SITES.items():
            with self.subTest(file=relative):
                carrying = [b for b in _confirm_option_blocks(callers[relative]) if word in b]
                self.assertEqual(len(carrying), 1, f"{relative}: expected exactly one dialog to ask for {word!r}")
                self.assertRegex(
                    carrying[0],
                    _DANGER,
                    f"{relative}: the dialog that asks for {word!r} must also be painted as danger",
                )


class TheWizardRestoreIsTheOneExceptionTests(unittest.TestCase):
    """`RestoreStep.tsx` posts to `/restore` and asks for no word. That is deliberate.

    It is the first-run wizard's "I have a backup" branch, and the server only offers the
    wizard on an instance it considers unconfigured. Asking the operator to type RESTORE on
    the screen whose entire purpose is to restore would be ceremony, not a guard.

    What makes the exception safe is the precondition, so the precondition is what is tested.
    If the wizard ever becomes reachable on a configured instance, these fail and the
    decision gets made again instead of being inherited.
    """

    def test_the_wizard_restore_asks_for_no_word(self):
        text = _callers_of_the_wiping_routes()[_WIZARD_RESTORE]
        self.assertNotRegex(
            text,
            _ASKS_FOR_A_WORD,
            "the wizard restore now asks for a typed word; that is defensible, but then it "
            "belongs in _WIPING_CALL_SITES and this exception should go",
        )

    def test_the_wizard_is_only_mounted_while_setup_is_required(self):
        app = (_SRC / "App.tsx").read_text(encoding="utf-8")
        mount = re.search(r"if \(auth\.setup_required\) \{(.{0,200})", app, re.DOTALL)
        self.assertIsNotNone(mount, "App.tsx no longer gates the wizard on auth.setup_required")
        self.assertIn("<Setup", mount.group(1))
        # And nothing else renders the step.
        elsewhere = [
            p.relative_to(_SRC).as_posix()
            for p in _sources()
            if "RestoreStep" in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            sorted(elsewhere),
            ["components/features/setup/RestoreStep.tsx", "components/features/setup/index.ts", "pages/Setup.tsx"],
            "the wizard's restore step is reachable from somewhere new; it carries no typed-word guard",
        )

    def test_setup_is_only_required_on_an_instance_with_no_provider(self):
        auth = (_ROOT / "app" / "api" / "auth.py").read_text(encoding="utf-8")
        self.assertIn(
            '"setup_required": (not setup_completed) and provider_count == 0',
            auth,
            "setup_required widened; the wizard's unguarded restore may now open on a "
            "configured instance",
        )

    def test_restore_never_runs_anonymously(self):
        """Even inside the wizard, a restore needs a real admin credential."""
        backup = (_ROOT / "app" / "api" / "backup.py").read_text(encoding="utf-8")
        route = backup[backup.index('@router.post("/api/restore")') :][:1200]
        self.assertIn('require_auth(request, scope="admin")', route)
        self.assertNotIn("require_auth_or_setup", route)


class DeletingOneObjectDoesNotAskForAWordTests(unittest.TestCase):
    """The control, and half the rule.

    A typed word is worth something only while it is rare. Asked before every deletion it
    becomes a reflex, and the operator types it without reading the sentence above it --
    which is exactly the reflex the two wiping buttons need to break. So the dialogs that
    delete one named thing must stay plain, and the list of dialogs that do not must stay
    closed.
    """

    def test_the_scan_reads_the_option_and_not_merely_the_file(self):
        """Eleven danger dialogs delete one named object each; none of them asks for a word."""
        plain = 0
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(_SRC).as_posix()
            if relative in _WIPING_CALL_SITES or relative.startswith("components/ui/"):
                continue
            for block in _confirm_option_blocks(text):
                if not _DANGER.search(block):
                    continue
                if _ASKS_FOR_A_WORD.search(block):
                    # `Services.tsx` types the number of routes a bulk delete is about to
                    # remove: the dialog cannot name them, so the count is the name.
                    self.assertEqual(
                        relative,
                        "pages/Services.tsx",
                        f"{relative} asks for a typed word to delete one named object; that "
                        "trains the operator to type through the two dialogs that matter",
                    )
                    continue
                plain += 1
        self.assertGreaterEqual(
            plain,
            8,
            "the scan found almost no plain danger dialogs, so it is measuring its own regex",
        )

    def test_the_set_of_dialogs_that_ask_is_closed(self):
        asking = sorted(
            p.relative_to(_SRC).as_posix()
            for p in _sources()
            if not p.relative_to(_SRC).as_posix().startswith("components/ui/")
            and any(_ASKS_FOR_A_WORD.search(b) for b in _confirm_option_blocks(p.read_text(encoding="utf-8")))
        )
        self.assertEqual(
            asking,
            [
                "components/features/settings/DataTab.tsx",
                "components/features/settings/data/RestoreSection.tsx",
                "pages/Services.tsx",
            ],
        )


class TheTwoRoutesReallyWipeTheSameThingTests(unittest.TestCase):
    """The premise of everything above: reset and restore destroy the same rows.

    If they ever stop agreeing, one of the two dialogs is guarding the wrong amount of data.
    """

    def test_restore_wipes_the_tables_reset_wipes(self):
        backup = (_ROOT / "app" / "api" / "backup.py").read_text(encoding="utf-8")
        settings = (_ROOT / "app" / "api" / "settings.py").read_text(encoding="utf-8")

        listed = backup[backup.index("_RESTORE_WIPE_TABLES = (") :]
        listed = listed[: listed.index(")", listed.index("("))]
        wiped_by_restore = set(re.findall(r'"(\w+)"', listed))
        self.assertGreaterEqual(len(wiped_by_restore), 10, "_RESTORE_WIPE_TABLES no longer parses as a list")

        reset = settings[settings.index("def reset_all") :]
        reset = reset[: reset.index("\ndef ") if "\ndef " in reset[1:] else len(reset)]
        wiped_by_reset = set(re.findall(r"DELETE FROM (\w+)", reset))

        missing = wiped_by_restore - wiped_by_reset
        self.assertFalse(
            missing,
            f"a restore empties {sorted(missing)} and a reset does not; the two dialogs no "
            "longer guard the same amount of data",
        )


if __name__ == "__main__":
    unittest.main()
