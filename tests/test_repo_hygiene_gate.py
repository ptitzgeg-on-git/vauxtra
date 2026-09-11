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


if __name__ == "__main__":
    unittest.main()
