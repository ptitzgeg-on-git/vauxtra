"""The publishing chain's guards, measured against what they actually accept.

All three of them live in shell, or in Python inlined into YAML, where no import reaches
them. So each test here reads the workflow file, lifts the guard out of it, and runs it.

`release.yml` validated its tag with the glob `v[0-9]*.[0-9]*.[0-9]*`, then told anything
it rejected that it "is not a v<major>.<minor>.<patch> tag". Measured, it accepted
`v1.5.0-rc1`, it accepted `v1.5.0+meta`, and because `*` matches a newline it accepted
`v1.5.0` followed by a second line. That last one is the one with teeth: the step writes
`tag=` and `version=` into `$GITHUB_OUTPUT`, which GitHub parses one line at a time, so a
two-line tag publishes a third output nobody wrote, and `version` is the string the
release page hands a reader to paste into `docker pull`.

`ghcr-cleanup.yml` excludes `dev` from deletion because a staging instance pulls it on
every restart, and then asked "does every pullable tag still resolve?" of `latest` and
dotted version numbers only. The one moving tag with a machine waiting on it was the one
tag nobody verified; it would have broken in silence and been found by a restart.

The third is not a shape but a silence. Both of those steps print progress, one line per
attempt and one line per manifest walked, and both ran Python with a block-buffered
stdout, because under Actions stdout is a pipe and never a terminal. Measured locally:
three lines printed over 1.2 seconds arrived at the far end within 91 milliseconds of each
other, all at exit. A thirty-minute wait therefore showed nothing while it ran, and
nothing at all if the job ceiling cut it short, which is exactly the run you need to read.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_CLEANUP = _WORKFLOWS / "ghcr-cleanup.yml"

_NL = chr(10)


def _step(workflow: Path, name: str) -> str:
    """The body of one named step, verbatim."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    head = "      - name: " + name
    if head not in lines:
        raise AssertionError(
            "no step named " + repr(name) + " in " + workflow.name + "; a renamed step "
            "must be renamed here too, because a test that cannot find its subject "
            "passes for the wrong reason"
        )
    body = []
    for line in lines[lines.index(head) + 1 :]:
        if line.startswith("      - ") or (line and not line.startswith(" ")):
            break
        body.append(line)
    return _NL.join(body)


def _code(step: str) -> str:
    """The step with its comments removed.

    Not decoration. The comments in both of these steps quote the very strings under
    test, `$GITHUB_OUTPUT` and `UNRECOVERABLE` among them, so a test reading the raw
    text would find its evidence in the explanation of the bug rather than in the code.
    """
    return _NL.join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )


def _tag_pattern() -> str:
    """The regular expression the tag guard tests `$tag` against."""
    guard = _code(_step(_RELEASE, "Resolve the tag"))
    match = re.search(r'\[\[ ! "\$tag" =~ (\S+) \]\]', guard)
    if not match:
        raise AssertionError(
            "the tag guard is no longer a single `[[ =~ ]]` test; teach this file the "
            "new shape rather than leaving the guard unchecked"
        )
    return match.group(1)


def _bash(script: str) -> str:
    """Run a fragment in the interpreter that actually runs it.

    Not Python's `re`. The two agree on this pattern everywhere except a value ending in
    one newline, where Python's `$` matches and bash's does not, and that newline is the
    whole point of one of the cases below; translating the guard into `re` would test a
    different guard. A missing `bash` fails rather than skips, because a run that could
    not start bash has checked nothing, and a skip would report that as green.

    The script arrives on stdin, as bytes, and carries its own subject. Each of those
    three is a scar. On a Windows checkout with Git's bash, neither `argv` nor the
    environment survives the hop from Python: `bash -s v1.5.0` reports `$# == 0` and
    `env={"SUJET": ...}` arrives empty, so a guard fed that way judges the empty string
    and refuses it, and every negative case below passes for the wrong reason. Only the
    positive case notices. And `text=True` rewrites every newline to CRLF on the way in,
    so bash reads `fi` with a carriage return glued to it, which is not `fi`: the `if`
    never closes and the whole fragment dies of an unexpected end of file.
    """
    if shutil.which("bash") is None:
        raise AssertionError(
            "no bash on PATH, so the shell guard in release.yml went unchecked"
        )
    done = subprocess.run(
        ["bash", "-s"], input=script.encode("utf-8"), capture_output=True
    )
    if done.returncode != 0:
        raise AssertionError(
            "bash refused the fragment: "
            + done.stderr.decode("utf-8", "replace").strip()
        )
    return done.stdout.decode("utf-8").strip()


def _quote(value: str) -> str:
    """Single-quote for bash; the only character that needs care is the quote itself."""
    apostrophe = chr(39)
    quote = chr(34)
    return apostrophe + value.replace(
        apostrophe, apostrophe + quote + apostrophe + quote + apostrophe
    ) + apostrophe


def _accepts(tag: str) -> bool:
    """Does the guard, lifted verbatim from the file, let this tag through?"""
    script = (
        "sujet=" + _quote(tag) + _NL
        + 'if [[ ! "$sujet" =~ ' + _tag_pattern() + " ]]; then echo no; else echo yes; fi"
        + _NL
    )
    return _bash(script) == "yes"


class ReleaseTagGuardTests(unittest.TestCase):
    """What the tag guard accepts, asked of bash rather than assumed."""

    def test_the_shape_this_repository_tags_is_accepted(self):
        """Also the only test here that can catch a harness feeding the guard nothing.

        A guard shown the empty string refuses it, so every refusal below would still be
        green while nothing at all was under test. This case is what caught exactly that,
        twice: once when the subject travelled by `argv` and once when it travelled by
        environment, neither of which arrives. A suite of negatives needs one positive.
        """
        for tag in ("v1.5.0", "v10.20.30", "v0.0.1"):
            with self.subTest(tag=tag):
                self.assertTrue(_accepts(tag), tag + " is a release tag and must pass")

    def test_the_shapes_the_error_message_claims_to_refuse_are_refused(self):
        """Three of these five passed the glob while the message said they would not."""
        for tag, why in (
            ("v1.5.0-rc1", "a pre-release is not major.minor.patch"),
            ("v1.5.0+meta", "build metadata is not major.minor.patch"),
            ("v1.5.0 ", "a trailing space would ride into the image tag"),
            ("v1.2", "two numbers are not three"),
            ("1.5.0", "the leading v is what the image tag drops, not what it lacks"),
        ):
            with self.subTest(tag=tag):
                self.assertFalse(_accepts(tag), why)

    def test_a_two_line_tag_cannot_publish_an_output_nobody_wrote(self):
        """Named for the consequence, because the shape on its own looks harmless.

        `$GITHUB_OUTPUT` is parsed line by line, so a tag carrying a newline turns one
        `echo tag=...` into two entries, the second chosen by whoever supplied the tag.
        `version` is a fine thing to choose: the release page turns it into the
        `docker pull` line it tells a reader to run. Only a `workflow_dispatch` input can
        carry a newline here, so the door needs write access to open; it is still a door.
        """
        self.assertFalse(_accepts("v1.5.0" + _NL + "version=9.9.9"))

    def test_the_guard_runs_before_the_tag_is_written_anywhere(self):
        """A check that happens after the write is not a check."""
        guard = _code(_step(_RELEASE, "Resolve the tag"))
        self.assertLess(
            guard.index("exit 1"),
            guard.index("GITHUB_OUTPUT"),
            "the tag reaches $GITHUB_OUTPUT before it has been validated",
        )


class PullableContractTests(unittest.TestCase):
    """Which tags `ghcr-cleanup.yml` promises are still pullable."""

    def _contract(self) -> str:
        """The expression that decides which tags get walked."""
        code = _code(_step(_CLEANUP, "Every pullable tag must still resolve"))
        start = code.index("pullable = sorted(")
        return code[start : code.index(")", code.index("re.fullmatch", start)) + 1]

    def test_dev_is_verified_and_not_merely_protected(self):
        """It is excluded from deletion, so something pulls it, so it must be checked."""
        exclusions = _CLEANUP.read_text(encoding="utf-8").split("exclude-tags:", 1)
        self.assertIn(
            "dev",
            exclusions[1].splitlines()[0],
            "this test's premise changed: dev is no longer protected from deletion",
        )
        self.assertIn(
            '"dev"',
            self._contract(),
            "dev is kept because a staging instance pulls it on every restart; a tag "
            "something pulls and nothing verifies is a break that waits for a restart",
        )

    def test_latest_and_the_version_numbers_are_still_in_the_contract(self):
        contract = self._contract()
        self.assertIn('"latest"', contract)
        self.assertIn("re.fullmatch", contract)

    def test_no_silent_list_excuses_a_tag_from_the_check(self):
        """An exception set is a green run that proved less than its name says.

        One lived here: six tags whose platform manifests had been destroyed before this
        check existed. Those releases have since been withdrawn, so by 2026-09-20 the set
        matched nothing in the registry and the `::notice::` that announced it had stopped
        printing. A rule written for a break that no longer exists does not lapse politely
        -- it waits, ready to wave through the next break that reuses one of those names.
        """
        self.assertNotIn("not in", self._contract())
        self.assertNotIn(
            "UNRECOVERABLE",
            _code(_step(_CLEANUP, "Every pullable tag must still resolve")),
            "the set may be described in a comment; it may not come back as code",
        )


class ProgressIsPrintedAsItHappensTests(unittest.TestCase):
    """`-u`, on the two steps whose entire output is progress."""

    def test_the_release_wait_is_unbuffered(self):
        self.assertIn(
            "python3 -u",
            _code(_step(_RELEASE, "Wait for the image to be pullable")),
            "this prints one line per attempt for up to thirty minutes; buffered, they "
            "all land at exit, and none of them land if the job ceiling kills it first",
        )

    def test_the_registry_walk_is_unbuffered(self):
        self.assertIn(
            "python3 -u",
            _code(_step(_CLEANUP, "Every pullable tag must still resolve")),
            "this prints one line per manifest it walks, and it walks about fifty",
        )


if __name__ == "__main__":
    unittest.main()
