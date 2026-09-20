"""The bridge's version has to be Vauxtra's, and it has to be the one it actually reports.

`FastMCP(...)` takes a `version` keyword. Left out, it answers the MCP handshake with the
version of the *library*, so `serverInfo` read `{"name": "Vauxtra", "version": "3.2.4"}` —
a release of Vauxtra that has never existed, moving with every fastmcp bump instead of
with this repository.

Two things can go wrong from here, and neither one shows up in an ordinary run:

  * The release number is bumped in `frontend/package.json` and the bridge is forgotten.
    That has already happened to that file on its own: `v1.0.2` was tagged while it still
    said `1.0.1`, and nothing anywhere said a word.
  * The `version=` argument is dropped or renamed. The bridge still starts, still lists
    every tool, and goes quietly back to announcing fastmcp's version.

So one test reads the declaration and the other reads what the built instance carries.

And the release number is written in four files. The two tests above read two of them;
the class below reads the other two. It was added while shipping 1.5.1, after the bump
had to be applied by hand to the npm lock file and the changelog, neither of which
anything was watching: the gate above stayed green with a lock file still saying
1.5.0, which is the state `npm ci` refuses, and with a changelog whose newest section
named a release that was no longer the one being built.
"""

import json
import pathlib
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _release_version() -> str:
    """Where this repository has kept its release number since v1.0.0."""
    manifest = json.loads((_REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
    return manifest["version"]


class VersionDeclarationsTests(unittest.TestCase):
    def test_the_bridge_declares_the_release_version(self) -> None:
        from vauxtra_mcp import __version__

        self.assertEqual(
            __version__,
            _release_version(),
            "vauxtra_mcp/__init__.py and frontend/package.json disagree about which "
            "release this is. They move together when the version is bumped.",
        )

    def test_the_bridge_reports_that_version_in_the_handshake(self) -> None:
        from vauxtra_mcp import __version__
        from vauxtra_mcp.app import mcp

        self.assertEqual(
            mcp.version,
            __version__,
            "The FastMCP instance is not carrying the declared version, so the handshake "
            "answers with fastmcp's instead. See the `version=` argument in "
            "vauxtra_mcp/app.py.",
        )


def _changelog_lines() -> list[str]:
    return (_REPO / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()


def _newest_released_section() -> str:
    """The version named by the topmost heading that is not `[Unreleased]`.

    Read by scanning for `## [` rather than by regular expression, so that a heading in a
    shape this function does not understand raises instead of quietly matching nothing.
    """
    for line in _changelog_lines():
        if not line.startswith("## ["):
            continue
        name = line[line.index("[") + 1 : line.index("]")]
        if name != "Unreleased":
            return name
    raise AssertionError(
        "CHANGELOG.md has no released section at all. This function reads the topmost "
        "`## [x.y.z]` heading, so a change to that shape has to be taught here rather "
        "than left to make the tests below pass on an empty answer"
    )


class ReleaseNumberIsWrittenInFourPlacesTests(unittest.TestCase):
    """The two declarations the gate above does not read.

    `frontend/package.json` is where this repository keeps the number, and the bridge
    follows it. Two more files carry it and neither was watched.

    The npm lock file repeats it twice, at the root and under `packages[""]`. `npm ci`
    refuses to install when the lock and the manifest disagree, so forgetting it does not
    produce a wrong build, it produces a red CI job with a message about being out of sync,
    which is a long way from the file that actually needs one character changed.

    The changelog is the other. A release whose newest section names the previous version
    publishes a page of notes for a build nobody made; that already happened here, and
    `release(version)` is the commit that repaired it. Both are one-character omissions
    that nothing else in the suite can see.
    """

    def test_the_npm_lock_declares_the_same_release(self) -> None:
        """Both entries at once, in one assertion, because a subtest reports twice.

        Written with `subTest` first, and pytest then summarised the same test as both
        `PASSED` and `SUBFAILED` in the same run. The exit code was still 1, so nothing
        was actually let through, but a line saying a version gate passed while the
        version is wrong is the kind of green this file exists to refuse.

        Both entries are read by key, and that is not a style preference. Two packages
        in this very lock file carry the two version numbers this release moves
        between: `css.escape` is at 1.5.1 and `lz-string` at 1.5.0. Anything that
        looked for the text `"version": "1.5.1"` would find theirs, and a
        search-and-replace that bumped the file would corrupt them.
        """
        lock = json.loads(
            (_REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8")
        )
        expected = _release_version()
        self.assertEqual(
            (expected, expected),
            (lock["version"], lock["packages"][""]["version"]),
            "frontend/package-lock.json disagrees with frontend/package.json. The pair "
            "above is the root entry then the `packages[\"\"]` entry; both carry the "
            "release number and both move with the manifest. `npm ci` refuses to "
            "install until they agree, so this falls in CI as a message about being "
            "out of sync rather than as a pointer at the file to edit.",
        )

    def test_the_changelog_names_this_release_at_the_top(self) -> None:
        self.assertEqual(
            _release_version(),
            _newest_released_section(),
            "The newest released section of CHANGELOG.md is not this release. Either the "
            "version was bumped without writing the notes, which publishes a release page "
            "for a build nobody made, or the notes were written under the wrong number.",
        )

    def test_that_release_has_a_comparison_link(self) -> None:
        """A section whose link is missing renders as literal brackets on the page.

        The link block at the bottom of the file is what turns `## [1.5.1]` into something
        a reader can click to see the diff. It is a separate edit from the section itself,
        made at the same moment and forgotten on its own.
        """
        head = "[" + _release_version() + "]: "
        self.assertTrue(
            any(line.startswith(head) for line in _changelog_lines()),
            "CHANGELOG.md has a section for "
            + _release_version()
            + " but no `"
            + head.strip()
            + "` line in the comparison links at the bottom, so the heading renders as "
            "plain brackets instead of a link to the diff.",
        )


if __name__ == "__main__":
    unittest.main()
