"""Run `npm audit` so that a registry that will not answer is not read as a verdict.

`npm audit --audit-level=high` exits 1 for two unrelated reasons: the registry reported a
high-severity advisory, or the registry did not report anything at all. CI could only see the
exit code, so a 400 from `registry.npmjs.org` -- which happens, and happened here -- turned a
green branch red with a failure whose message named no package, and the only repair was to
re-run the job until the registry felt better.

The two now get told apart by the shape of the answer rather than by the exit code. A real
report carries `metadata.vulnerabilities`; this script counts the severities itself and
decides from those counts. Anything without that object is the registry declining to speak:
that is retried, and if it never speaks, the job still fails -- loudly, and saying so. An
audit that could not reach the registry has proved nothing, and a security gate that passes
on silence is worse than one that flakes.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# npm's own ladder, least to most severe. `--audit-level=high` means "high and critical".
SEVERITIES = ["info", "low", "moderate", "high", "critical"]

CLEAN = "clean"
VULNERABLE = "vulnerable"
UNREACHABLE = "unreachable"


def counts_at_or_above(vulnerabilities: dict, level: str) -> dict[str, int]:
    """The severities that matter at `level`, in npm's order, dropping the empty ones."""
    floor = SEVERITIES.index(level)
    return {
        name: int(vulnerabilities.get(name, 0) or 0)
        for name in SEVERITIES[floor:]
        if int(vulnerabilities.get(name, 0) or 0) > 0
    }


def classify(stdout: str, level: str) -> tuple[str, str]:
    """Read one `npm audit --json` run: a verdict, or a registry that did not give one.

    Returns the outcome and a line to print. The exit code is deliberately not an input --
    it is the thing that conflates these two cases in the first place.
    """
    try:
        report = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        head = " ".join(stdout.split())[:200] if stdout else "(no output)"
        return UNREACHABLE, f"npm audit returned something that is not a report: {head}"

    if not isinstance(report, dict):
        return UNREACHABLE, "npm audit returned a report that is not an object."

    vulnerabilities = report.get("metadata", {}).get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        error = report.get("error") or {}
        summary = error.get("summary") or error.get("code") or "no metadata in the report"
        return UNREACHABLE, f"the registry did not return an audit: {summary}"

    found = counts_at_or_above(vulnerabilities, level)
    if not found:
        total = int(vulnerabilities.get("total", 0) or 0)
        below = f"{total} advisory/advisories below {level}" if total else "no advisories"
        return CLEAN, f"No {level}-or-above vulnerability ({below})."

    listed = ", ".join(f"{count} {name}" for name, count in found.items())
    return VULNERABLE, f"{listed}. Run `npm audit` in frontend/ for the packages."


def run_once(level: str) -> tuple[str, str]:
    # Resolved rather than spelled, because on Windows `npm` is `npm.cmd` and a bare "npm"
    # only starts through a shell -- which this has no other reason to want.
    npm = shutil.which("npm")
    if npm is None:
        return UNREACHABLE, "npm is not on PATH."

    try:
        completed = subprocess.run(
            [npm, "audit", f"--audit-level={level}", "--json"],
            cwd=FRONTEND,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return UNREACHABLE, f"npm could not be run: {exc}"
    return classify(completed.stdout, level)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-level", default="high", choices=SEVERITIES)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--delay", type=float, default=10.0, help="seconds before a retry")
    args = parser.parse_args()

    message = "npm audit was never run."
    for attempt in range(1, args.attempts + 1):
        outcome, message = run_once(args.audit_level)

        if outcome == CLEAN:
            print(f"npm audit: {message}")
            return 0

        if outcome == VULNERABLE:
            print(f"npm audit found vulnerabilities at or above {args.audit_level}:")
            print(f"  {message}")
            return 1

        print(f"npm audit attempt {attempt}/{args.attempts}: {message}")
        if attempt < args.attempts:
            time.sleep(args.delay)

    print()
    print(f"npm audit never reached the registry in {args.attempts} attempts.")
    print(f"Last answer: {message}")
    print()
    print("This is not a vulnerability finding -- nothing was audited. The job fails")
    print("because an audit that did not happen proves nothing about the dependencies.")
    print("Re-run it once registry.npmjs.org is answering again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
