"""Every job that occupies a runner declares how long it may hold its concurrency group.

`Build & Publish` keeps `cancel-in-progress: false` deliberately -- two runs there would be
two writers on the same registry tag or the same release page -- and it is the only producer
of three of the five checks `main` and `dev` require. `Dependency audit`, `Image build & scan`
and `Vulnerability scan (Trivy + Grype)` reach a push through its gate jobs and through
nothing else. So a job that hangs there does not stall one run, it stalls the branch.

That is not hypothetical: `06a90ed` sat in `Build and push` for 48 minutes against a measured
402 seconds, with two later pushes queued behind it and neither of them scanned. No workflow
declared a ceiling, so the one in force was GitHub's default of six hours.

A job that calls a reusable workflow is exempt. `timeout-minutes` is not accepted on a `uses:`
job; the ceiling belongs to the called workflow's own jobs, and this check reads those when it
opens that file.
"""

import re
import unittest
from pathlib import Path

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# A job id sits at exactly two spaces under `jobs:`; its body is indented deeper.
_JOB_ID = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def _jobs(path: Path) -> dict[str, list[str]]:
    """The body lines of every job in one workflow, keyed by job id."""
    lines = path.read_text(encoding="utf-8").split("\n")
    if "jobs:" not in lines:
        return {}
    found: dict[str, list[str]] = {}
    current = None
    for line in lines[lines.index("jobs:") + 1 :]:
        if line and not line.startswith(" "):
            break  # a top-level key closes the jobs block
        match = _JOB_ID.match(line)
        if match:
            current = match.group(1)
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return found


def _declares(body: list[str], key: str) -> bool:
    return any(line.startswith("    " + key + ":") for line in body)


class EveryRunnerJobDeclaresACeilingTests(unittest.TestCase):
    def test_a_job_on_a_runner_says_how_long_it_may_hold_the_queue(self):
        for path in sorted(_WORKFLOWS.glob("*.yml")):
            for job, body in _jobs(path).items():
                if not _declares(body, "runs-on"):
                    continue
                with self.subTest(workflow=path.name, job=job):
                    self.assertTrue(
                        _declares(body, "timeout-minutes"),
                        f"{path.name}: job `{job}` runs on a runner and declares no "
                        f"`timeout-minutes`, so it may hold its concurrency group for "
                        f"GitHub's default of six hours",
                    )

    def test_a_job_that_only_calls_a_workflow_is_not_asked_for_one(self):
        """The key is refused on a `uses:` job; the called workflow carries the ceiling."""
        callers = [
            (path.name, job)
            for path in sorted(_WORKFLOWS.glob("*.yml"))
            for job, body in _jobs(path).items()
            if _declares(body, "uses") and not _declares(body, "runs-on")
        ]
        self.assertTrue(callers, "no reusable-workflow call left to exempt")
        for name, job in callers:
            with self.subTest(workflow=name, job=job):
                body = _jobs(_WORKFLOWS / name)[job]
                self.assertFalse(_declares(body, "timeout-minutes"))

    def test_the_scan_actually_found_the_jobs(self):
        """A scanner that returned nothing would make the check above pass for free."""
        gated = [
            job
            for path in sorted(_WORKFLOWS.glob("*.yml"))
            for job, body in _jobs(path).items()
            if _declares(body, "runs-on")
        ]
        self.assertGreaterEqual(len(gated), 6, f"only found {gated}")


class TheCeilingSitsAboveTheWaitItContainsTests(unittest.TestCase):
    """A ceiling below the wait it holds is worse than no ceiling at all.

    `release.yml` waited up to thirty minutes for the image to become pullable, inside a
    job capped at twenty. The wait could never reach its own deadline, so the two
    `::error::` lines that name the missing manifest and say what to do about it were
    unreachable: the run died on "exceeded the maximum execution time of 20 minutes" and
    explained nothing. The two numbers were written independently, in different commits,
    and nothing compared them. This does.
    """

    def test_the_release_job_may_run_longer_than_it_is_allowed_to_wait(self):
        text = (_WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        ceiling = re.search(r"^    timeout-minutes: (\d+)$", text, re.M)
        budget = re.search(r'^      PULL_WAIT_MINUTES: "(\d+)"$', text, re.M)
        self.assertIsNotNone(ceiling, "release.yml declares no job ceiling")
        self.assertIsNotNone(budget, "release.yml declares no wait budget")
        ceiling, budget = int(ceiling.group(1)), int(budget.group(1))
        self.assertGreaterEqual(
            ceiling - budget,
            10,
            f"the job may run {ceiling} min and waits up to {budget} min: the wait needs "
            f"room to reach its deadline AND print why, plus the steps around it",
        )

    def test_the_wait_reads_that_budget_instead_of_repeating_it(self):
        """A second copy of the number is a second thing to forget."""
        text = (_WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn('DEADLINE, STEP = int(os.environ["PULL_WAIT_MINUTES"]) * 60', text)


if __name__ == "__main__":
    unittest.main()
