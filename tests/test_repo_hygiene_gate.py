"""The contributor sidebar of a public repository was wrong, and fixing it was expensive.

Two names that had never written a line of this project appeared on the front page, put
there by `Co-authored-by:` trailers a tool had written. GitHub builds that list from commit
authors and from those trailers, so nothing short of rewriting every commit removed them --
and rewriting every commit meant force-pushing six tags, changing every published commit
identifier, and making every existing clone stale.

That is the cost of catching it late. `check_repo_hygiene.py` now catches it at the gate,
and this module is the proof that it does: a gate nobody has ever seen fail is a claim, not
a guarantee. Each test here builds a throwaway repository containing exactly one thing the
gate is supposed to refuse, and checks that it refuses it.
"""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_repo_hygiene.py"
_spec = importlib.util.spec_from_file_location("check_repo_hygiene", _GATE_PATH)
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)

OWNER_NAME = "Ptite Pomme"
OWNER_EMAIL = "61865114+ptitzgeg-on-git@users.noreply.github.com"
BOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"


def _git(repo: Path, *args: str, **env_overrides: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **env_overrides},
    )


def _commit(
    repo: Path,
    message: str,
    author_name: str = OWNER_NAME,
    author_email: str = OWNER_EMAIL,
    committer_name: str = OWNER_NAME,
    committer_email: str = OWNER_EMAIL,
) -> None:
    """One empty commit, with the author and the committer named separately.

    They are separate fields and the gate judges them against separate lists, so a test
    that cannot set one without the other could not tell the two checks apart.
    """
    _git(
        repo,
        "-c",
        f"user.name={author_name}",
        "-c",
        f"user.email={author_email}",
        "commit",
        "--allow-empty",
        "-m",
        message,
        GIT_AUTHOR_NAME=author_name,
        GIT_AUTHOR_EMAIL=author_email,
        GIT_COMMITTER_NAME=committer_name,
        GIT_COMMITTER_EMAIL=committer_email,
    )


class RepoHygieneIdentityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_cwd = Path.cwd()
        self._tmp = Path(tempfile.mkdtemp(prefix="vauxtra-gate-"))
        self.repo = self._tmp / "repo"
        self.repo.mkdir()
        _git(self.repo.parent, "init", "-b", "main", str(self.repo))

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        # Git marks objects read-only, which makes a Windows rmtree raise on a directory
        # the test no longer cares about. A leftover temp directory is not a test failure.
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run_gate(self, repo: Path | None = None) -> list[str]:
        os.chdir(repo or self.repo)
        return hygiene._find_bad_commit_identities()

    def test_the_owner_and_the_bot_pass(self) -> None:
        _commit(self.repo, "fix(cors): trois orthographes d'une origine")
        _commit(
            self.repo,
            "build(deps): bump vite\n\n"
            f"Co-authored-by: dependabot[bot] <{BOT_EMAIL}>",
            author_name="dependabot[bot]",
            author_email=BOT_EMAIL,
            committer_name="GitHub",
            committer_email="noreply@github.com",
        )
        self.assertEqual([], self._run_gate())

    def test_an_unknown_author_is_refused(self) -> None:
        _commit(
            self.repo,
            "feat: something",
            author_name="Somebody Else",
            author_email="somebody@example.com",
        )
        findings = self._run_gate()
        self.assertTrue(
            any("author Somebody Else <somebody@example.com>" in f for f in findings),
            findings,
        )

    def test_an_unknown_committer_is_refused(self) -> None:
        """The committer is the field a merge rewrites, and the one easiest to overlook."""
        _commit(
            self.repo,
            "feat: something",
            committer_name="Somebody Else",
            committer_email="somebody@example.com",
        )
        findings = self._run_gate()
        self.assertTrue(
            any("committer Somebody Else <somebody@example.com>" in f for f in findings),
            findings,
        )

    def test_the_merge_identity_may_commit_but_not_author(self) -> None:
        """`GitHub <noreply@github.com>` signs every rebase merge and authors nothing.

        It is on the committer list and off the author list on purpose: an address that can
        only ever be a committer must not be able to become a contributor.
        """
        _commit(self.repo, "ok", committer_name="GitHub", committer_email="noreply@github.com")
        self.assertEqual([], self._run_gate())

        _commit(self.repo, "not ok", author_name="GitHub", author_email="noreply@github.com")
        self.assertTrue(
            any("author GitHub <noreply@github.com>" in f for f in self._run_gate()),
        )

    def test_a_co_author_trailer_is_refused(self) -> None:
        """The exact shape that put a stranger on the front page."""
        _commit(
            self.repo,
            "fix: something\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        )
        findings = self._run_gate()
        self.assertTrue(
            any("co-author Claude <noreply@anthropic.com>" in f for f in findings),
            findings,
        )

    def test_process_attribution_in_the_message_is_refused(self) -> None:
        """Three shapes that name a process rather than a person, none of them trailers."""
        for message, token in (
            ("chore: x\n\n\U0001f916 Generated with an assistant", "generated with"),
            ("chore: y\n\nClaude-Session: https://example.invalid/s/1", "claude-session"),
            ("chore: z\n\nSee https://console.anthropic.com/", "anthropic.com"),
        ):
            with self.subTest(token=token):
                repo = self._tmp / f"repo-{token.replace('.', '-')}"
                repo.mkdir()
                _git(self._tmp, "init", "-b", "main", str(repo))
                _commit(repo, message)
                findings = self._run_gate(repo)
                self.assertTrue(
                    any(f"message contains {token!r}" in f for f in findings), findings
                )

    def test_a_shallow_clone_is_a_failure_and_not_a_skip(self) -> None:
        """A gate that cannot read the history it polices must not report green.

        CI checks out one commit by default. Under that default this gate would have
        inspected the tip and passed, saying nothing about the sixty commits behind it --
        which is how a repository ends up with a green check and a stranger on its front
        page.
        """
        _commit(self.repo, "fix: something")
        _commit(self.repo, "fix: something else")
        shallow = self._tmp / "shallow"
        subprocess.run(
            ["git", "clone", "--depth", "1", self.repo.as_uri(), str(shallow)],
            check=True,
            capture_output=True,
            text=True,
        )
        findings = self._run_gate(shallow)
        self.assertTrue(any("shallow clone" in f for f in findings), findings)
        self.assertTrue(any("fetch-depth: 0" in f for f in findings), findings)


class RepoHygieneAllowlistTests(unittest.TestCase):
    """The allowlists themselves, because a gate is only as good as what it declares."""

    def test_the_merge_identity_is_a_committer_only(self) -> None:
        self.assertIn("noreply@github.com", hygiene.ALLOWED_COMMITTER_EMAILS)
        self.assertNotIn("noreply@github.com", hygiene.ALLOWED_AUTHOR_EMAILS)

    def test_every_author_may_also_commit(self) -> None:
        for email in hygiene.ALLOWED_AUTHOR_EMAILS:
            self.assertIn(email, hygiene.ALLOWED_COMMITTER_EMAILS)

    def test_the_forbidden_tokens_are_lowercase(self) -> None:
        """The message is lowercased before the comparison; an uppercase token never matches."""
        for token in hygiene.FORBIDDEN_COMMIT_MESSAGE_TOKENS:
            self.assertEqual(token, token.lower())


class RepoHygieneIgnoredSourceTests(unittest.TestCase):
    """The rule that could not fire in the only place it runs.

    It asked git for `ls-files --others --ignored`, which lists files that are on disk and
    NOT tracked. An `actions/checkout` tree holds the tracked files and nothing else, so
    that answer was empty on every run this gate has ever had. The defect its docstring
    describes -- an unanchored `data/` rule that hid four settings components from
    `git add -A` -- was therefore invisible to the two workflows that launch it. These
    tests build the CI shape on purpose: everything committed, nothing loose on disk.
    """

    def setUp(self) -> None:
        self._previous_cwd = Path.cwd()
        self._tmp = Path(tempfile.mkdtemp(prefix="vauxtra-ignored-"))
        self.repo = self._tmp / "repo"
        self.repo.mkdir()
        _git(self.repo.parent, "init", "-b", "main", str(self.repo))

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _commit_tree(self, gitignore: str) -> None:
        """A tracked component, then the rule, in that order.

        That order is how the real one got in: the component is committed first, the rule
        arrives later for the SQLite directory at the root, and quietly covers a path
        nobody was thinking about.
        """
        component = self.repo / "frontend/src/components/features/settings/data"
        component.mkdir(parents=True)
        (component / "SyncSection.tsx").write_text("export const SyncSection = () => null")
        _git(self.repo, "add", "-A")
        _commit(self.repo, "feat(settings) : la section de synchronisation")
        (self.repo / ".gitignore").write_text(gitignore)
        _git(self.repo, "add", "-A")
        _commit(self.repo, "chore : une regle d'exclusion")

    def _run_gate(self) -> list[str]:
        os.chdir(self.repo)
        return hygiene._find_ignored_source_files()

    def test_an_unanchored_rule_over_a_tracked_file_is_refused(self) -> None:
        self._commit_tree("data/")
        self.assertEqual(
            ["frontend/src/components/features/settings/data/SyncSection.tsx"],
            self._run_gate(),
        )

    def test_the_same_rule_anchored_to_the_root_passes(self) -> None:
        """`/data/` is the fix the gate's own message asks for, so it has to be accepted."""
        self._commit_tree("/data/")
        self.assertEqual([], self._run_gate())

    def test_a_clean_checkout_is_not_an_excuse_to_answer_nothing(self) -> None:
        """The old rule's blind spot, pinned: no loose file, and the gate still speaks."""
        self._commit_tree("data/")
        os.chdir(self.repo)
        loose = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual("", loose.stdout.strip(), "the tree under test must look like CI")
        self.assertNotEqual([], hygiene._find_ignored_source_files())

class RepoHygieneUnreadableFileTests(unittest.TestCase):
    """A file the address scan could not read used to leave no trace at all.

    `_find_private_addresses` answers one question about the whole repository: is there an
    undeclared private address printed anywhere in it. It reads each tracked text file as
    UTF-8, and it used to `continue` on `UnicodeDecodeError`, which quietly narrowed that
    answer to "anywhere I happened to be able to read" while the gate went on printing
    `Repo hygiene check passed` in the same voice. A NAS address in a latin-1 note would
    have sat there indefinitely, with the gate green over it and nobody told.

    Measured on 2026-09-20: 400 tracked files reach that loop and all 400 decode, so this
    costs nothing today. That is the argument for closing it today.
    """

    def setUp(self) -> None:
        self._previous_cwd = Path.cwd()
        self._tmp = Path(tempfile.mkdtemp(prefix="vauxtra-lecture-"))
        os.chdir(self._tmp)

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_bytes(self, name: str, payload: bytes) -> str:
        (self._tmp / name).write_bytes(payload)
        return name

    # Every address below is `172.20.x`, and that is not the usual placeholder. These
    # cases need one the gate will actually flag, so the declared prefixes -- `192.168.1.`,
    # `10.0.0.`, `172.16.` through `172.19.` -- are all unusable here: a fixture built from
    # one of them would pass while proving nothing. `172.20.` is private, undeclared, and
    # belongs to nobody. The first draft of this file reached for a real machine on the
    # author's own network instead, and the gate under test is what caught it -- which is
    # why the gate now skips this file and why the last test below takes over the scan.

    def test_a_file_that_cannot_be_decoded_is_named(self) -> None:
        """The latin-1 note, with an address in it, and the byte that hides it."""
        note = self._write_bytes(
            "note.md", b"\xe9tat du banc : la machine 172.20.0.5 ne repond plus\n"
        )
        hits = hygiene._find_private_addresses([note])
        self.assertEqual(1, len(hits), "an unreadable file must produce exactly one line")
        self.assertIn("note.md", hits[0])
        self.assertIn("was not scanned", hits[0])
        self.assertIn("UnicodeDecodeError", hits[0], "say which failure, not just that one")

    def test_the_address_inside_it_is_not_what_gets_reported(self) -> None:
        """An unreadable file is named, and only named.

        The readable path below does print the address it found, and that is right: by
        then the address is sitting in a tracked file of a public repository, so a CI log
        reveals nothing a clone would not. This path is not that. The file did not decode,
        so nothing in it has actually been established, and printing what a looser decode
        seemed to find would put a guess into a public log on the strength of a guess.
        The file is the finding here; its contents are not.
        """
        note = self._write_bytes(
            "note.md", b"\xe9crit le 20 : 172.20.0.5 et 172.20.0.6\n"
        )
        self.assertNotIn("172.20.0.5", hygiene._find_private_addresses([note])[0])

    def test_a_missing_file_is_named_too(self) -> None:
        """`OSError`, the other half of the clause, and the likelier one in CI.

        A tracked path that is not on disk means the checkout and the index disagree,
        which is worth a line of its own rather than one fewer file quietly scanned.
        """
        hits = hygiene._find_private_addresses(["jamais-ecrit.md"])
        self.assertEqual(1, len(hits))
        self.assertIn("was not scanned", hits[0])

    def test_a_readable_file_still_reports_its_address_and_its_line(self) -> None:
        """The ordinary path, unchanged: the point was to add a case, not move one."""
        note = self._write_bytes("doc.md", b"titre\nla machine 172.20.0.5 repond\n")
        self.assertEqual(
            ["doc.md:2: 172.20.0.5"], hygiene._find_private_addresses([note])
        )

    def test_a_declared_prefix_still_passes(self) -> None:
        """`10.0.0.` is the lab compose network and is declared, so it is not a hit."""
        note = self._write_bytes("compose.yml", b"provider: 10.0.0.7\n")
        self.assertEqual([], hygiene._find_private_addresses([note]))

    def test_this_file_shows_only_the_fixture_subnet(self) -> None:
        """The scan the gate no longer performs on this file, performed here instead.

        `SKIP_ADDRESS_SCAN` names this file, because the cases above have to carry an
        address the gate refuses and no declared prefix can play that part. An exemption
        that replaces a check with nothing is how the real address got in here in the
        first place, so it is replaced with this: every private address written anywhere
        in this module, fixture or prose, is either the `172.20.` fixture subnet or one
        of the prefixes the gate itself declares. The allowed list is read from the gate
        rather than copied, so widening it there cannot leave this test behind.
        """
        self.assertIn(
            "tests/test_repo_hygiene_gate.py",
            hygiene.SKIP_ADDRESS_SCAN,
            "if the gate scans this file again, this test is redundant -- delete it, "
            "do not leave two half-answers standing",
        )
        permitted = ("172.20.", *hygiene.ALLOWED_ADDRESS_PREFIXES)
        source = Path(__file__).read_text(encoding="utf-8")
        strays = sorted(
            {a for a in hygiene._PRIVATE_V4.findall(source) if not a.startswith(permitted)}
        )
        self.assertEqual(
            [],
            strays,
            "an address that is neither declared nor in the fixture subnet is written "
            "in this file, and the gate is no longer looking at it",
        )


if __name__ == "__main__":
    unittest.main()
