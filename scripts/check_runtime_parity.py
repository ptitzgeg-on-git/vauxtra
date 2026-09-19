"""The image ships one runtime. Everything that vouches for it has to run on that one.

`build(deps): bump the docker group` (834dfc3) moved the Dockerfile from `node:22-slim`
and `python:3.13-slim` to `node:26-slim` and `python:3.14-slim`, and it touched one file.
The workflows kept installing Python 3.13 and Node 22, so from that commit on every green
check on this repository attested to a runtime the published image does not have. The
image is still built, signed with cosign and given a SLSA provenance attestation on the
strength of those checks.

Two of the stale pins were worse than a missed test. `security.yml` resolves and installs
the dependency set so Syft can read real dist-info metadata -- the step that exists
because the SBOM was empty otherwise -- and it resolved that set on 3.13. Wheel
availability and environment markers are per-interpreter, so the SBOM that ships beside
the image, and the Grype scan that is the only step here allowed to break the build, can
both describe packages the image does not contain.

So the Dockerfile is the single source of truth for the runtime, and this gate holds
every other declaration of it to that. It is deliberately not a general version check:

  * `ruff.toml` keeps `target-version = "py313"` and the README keeps "Requires Python
    3.13+". Those state the *source floor* -- the code avoids syntax the previous release
    cannot parse -- which is a separate policy from what the image runs, and is allowed
    to sit below it. Neither is read here.
  * `CHANGELOG.md` still names `node:22-slim` and `python:3.13-slim`, and is right to:
    it records what the images *were* on the day each entry was written. A history
    that silently reprinted today's tags would be worth less than no history. It is
    not read here, and it is the only file naming a base image that isn't.
  * Only the versions that decide what an artifact or an attestation is made of are
    compared: the `setup-python` and `setup-node` pins in the workflows, and every
    sentence that tells a reader what the Dockerfile contains -- in the workflow
    comments, in the README, and above the `docker` entry in `dependabot.yml`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The prose that describes the Dockerfile to a human, outside the workflows. A sentence
# naming the wrong base image is a defect in its own right: both of these said 22 and 3.13
# for a day, and so did a comment inside `security.yml`.
PROSE_FILES = ("README.md", ".github/dependabot.yml")

FROM_NODE = re.compile(r"^FROM\s+node:(\d+)-slim\b", re.MULTILINE)
FROM_PYTHON = re.compile(r"^FROM\s+python:(\d+\.\d+)-slim\b", re.MULTILINE)

SETUP_PYTHON = re.compile(r"""^\s*python-version:\s*["']?([^"'\s#]+)""")
SETUP_NODE = re.compile(r"""^\s*node-version:\s*["']?([^"'\s#]+)""")

# `node:26-slim` in a backticked span, and "Node 26 for the frontend" / "Python
# 3.14-slim for the final image" in a sentence.
PROSE_NODE = re.compile(r"node:(\d+)-slim|Node (\d+) for the frontend")
PROSE_PYTHON = re.compile(r"python:(\d+\.\d+)-slim|Python (\d+\.\d+)-slim")


def _sole(pattern: re.Pattern[str], text: str, what: str) -> str:
    found = pattern.findall(text)
    if len(found) != 1:
        raise ValueError(
            f"Dockerfile declares {len(found)} {what} base images ({found or 'none'}); "
            "this gate assumes exactly one and has to be taught the new shape."
        )
    return found[0]


def base_images(root: Path) -> tuple[str, str]:
    """The node major and the python minor the image is actually built from."""
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    return _sole(FROM_NODE, dockerfile, "node"), _sole(FROM_PYTHON, dockerfile, "python")


def check(root: Path) -> list[str]:
    """Every declaration of the runtime that disagrees with the Dockerfile."""
    node, python = base_images(root)
    problems: list[str] = []

    def scan(
        path: Path,
        checks: tuple[tuple[re.Pattern[str], str, str], ...],
        anchored: bool,
    ) -> None:
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            where = f"{path.relative_to(root).as_posix()}:{number}"
            for pattern, want, name in checks:
                found = [pattern.match(line)] if anchored else list(pattern.finditer(line))
                for hit in found:
                    if hit is None:
                        continue
                    got = next((g for g in hit.groups() if g is not None), None)
                    if got is None:
                        continue
                    # A matrix or an input reference decides its value elsewhere; this
                    # gate reads literals, and says so rather than passing quietly on one.
                    if "$" in got:
                        problems.append(
                            f"{where}: {name} version is an expression ({got}); the runtime "
                            "it resolves to cannot be checked here."
                        )
                    elif got != want:
                        verb = "installs" if anchored else "says"
                        tail = (
                            f"but the image ships {name} {want}."
                            if anchored
                            else f"but the Dockerfile says {name} {want}."
                        )
                        problems.append(f"{where}: {verb} {name} {got}, {tail}")

    prose = ((PROSE_NODE, node, "Node"), (PROSE_PYTHON, python, "Python"))

    workflows = root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.yml")):
        scan(
            path,
            ((SETUP_PYTHON, python, "Python"), (SETUP_NODE, node, "Node")),
            anchored=True,
        )
        # The comments in them too. `security.yml` justifies scanning the built image by
        # naming the userland it scans, and that sentence went stale with everything else.
        # It had wrapped across two lines mid-token, so neither the first version of this
        # gate nor a grep for the tag could see it.
        scan(path, prose, anchored=False)

    for name in PROSE_FILES:
        path = root / name
        if path.exists():
            scan(path, prose, anchored=False)

    return problems


def main() -> int:
    node, python = base_images(ROOT)
    problems = check(ROOT)

    if problems:
        print("Runtime parity check failed:\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            f"\nThe Dockerfile is the source of truth: node:{node}-slim, python:{python}-slim.\n"
            "A base image bump has to move these with it, or the checks stop describing\n"
            "the image they gate."
        )
        return 1

    print(
        f"Runtime parity check passed (node:{node}-slim, python:{python}-slim "
        "in every workflow pin and both prose descriptions)"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"Runtime parity check failed: {exc}")
        sys.exit(1)
