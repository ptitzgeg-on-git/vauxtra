"""`Build & Publish` queues the runs that can write the same moving tag, and only those.

Three kinds of push reach that workflow. What each one actually writes was read from the
run logs rather than from its `tags:` block, because the block does not show it: the
`{{is_default_branch}}` guard on `latest` is true on a tag push too.

    push dev    0d896cd  ->  dev
    push main   35ab50c  ->  latest, sha-35ab50c
    push v1.5.0 c9734fb  ->  1.5.0, 1.5, 1, latest, sha-c9734fb

So `main` and a release tag both move `latest`, and that pair is what the group exists
for: measured on v1.5.0, the two runs overlapped for four minutes and it was the later
finisher that set `latest`. A `dev` build moves only `dev` and collides with nobody.

The group has been wrong in both directions, which is why this file exists.

Keyed on `github.ref`, `main` and its own tag landed in two groups and raced. Keyed on
nothing at all, every publish shared one queue, and that is worse than it looks: GitHub
cancels the previously PENDING run of a group as soon as a newer one queues, whatever
`cancel-in-progress` says. A `dev` push arriving while a release build waited therefore
killed the release build, and `release.yml` then spent its whole wait on an image nobody
was building any more.

The table above comes from runs that predate the fix limiting the `sha-` crumb to `main`,
which is why it is not the table asserted below.
"""

import itertools
import unittest
from pathlib import Path

_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "docker-publish.yml"
)
_NAME = "Build & Publish"

# Moving tags only, per ref. An immutable tag cannot be raced for, so `1.5.0` is absent:
# it is written once, by the one push that creates it. `sha-<commit>` is absent for the
# same reason once only `main` writes it, two `main` pushes being two commits.
_MOVING = {
    "refs/heads/dev": {"dev"},
    "refs/heads/main": {"latest"},
    "refs/tags/v1.5.0": {"latest", "1.5", "1"},
}


def _concurrency_block() -> list[str]:
    """The lines of the top-level `concurrency:` mapping."""
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index("concurrency:")
    body = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        if line.strip():
            body.append(line)
    return body


def _group_expression() -> str:
    for line in _concurrency_block():
        if line.strip().startswith("group:"):
            return line.split("group:", 1)[1].strip()
    raise AssertionError("the publish workflow declares no concurrency group")


def _evaluate(inner: str, ref: str) -> str:
    """One `${{ ... }}`, for one ref.

    Deliberately narrow. An expression this cannot read is an expression nobody is
    checking, so it raises rather than guessing: extend it, do not delete the test.
    """
    if inner == "github.workflow":
        return _NAME
    if inner == "github.ref":
        return ref
    if inner.startswith("github.ref == '") and " && " in inner and " || " in inner:
        wanted = inner.split("'", 2)[1]
        taken, otherwise = inner.split(" && ", 1)[1].split(" || ", 1)
        return (taken if ref == wanted else otherwise).strip().strip("'")
    raise AssertionError(
        "this checker cannot read the group expression " + repr(inner) + "; teach it "
        "that shape rather than leaving the grouping unchecked"
    )


def _group_for(ref: str) -> str:
    """The concurrency group GitHub computes for one ref."""
    out = _group_expression()
    while "${{" in out:
        start = out.index("${{")
        end = out.index("}}", start)
        out = out[:start] + _evaluate(out[start + 3 : end].strip(), ref) + out[end + 2 :]
    return out


class PublishQueueTests(unittest.TestCase):
    def test_two_refs_that_move_the_same_tag_share_a_queue(self):
        """And two that move nothing in common do not, because queuing is not free."""
        for first, second in itertools.combinations(sorted(_MOVING), 2):
            shared = _MOVING[first] & _MOVING[second]
            with self.subTest(first=first, second=second):
                if shared:
                    self.assertEqual(
                        _group_for(first),
                        _group_for(second),
                        f"{first} and {second} both move {sorted(shared)}: two runs would "
                        f"be two writers on the same tag, so they must queue together",
                    )
                else:
                    self.assertNotEqual(
                        _group_for(first),
                        _group_for(second),
                        f"{first} and {second} move no tag in common, so sharing a queue "
                        f"buys nothing and costs the wait plus a cancelled pending run",
                    )

    def test_a_dev_push_cannot_cancel_a_waiting_release(self):
        """The regression that one queue introduced, named so a failure says which one."""
        self.assertNotEqual(
            _group_for("refs/heads/dev"),
            _group_for("refs/tags/v1.5.0"),
            "GitHub cancels a group's previously pending run when a newer one queues, so "
            "a dev push in the same group as a release tag can kill the release build",
        )

    def test_no_publish_is_ever_killed_while_it_runs(self):
        """A run already pushing, signing or attesting is never the one to interrupt."""
        self.assertIn("cancel-in-progress: false", " ".join(_concurrency_block()))


if __name__ == "__main__":
    unittest.main()
