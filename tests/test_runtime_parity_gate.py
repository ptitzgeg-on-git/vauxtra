"""A gate nobody has ever seen fail is a claim. This is the proof that this one refuses.

The drift it exists for is not hypothetical: commit 834dfc3 bumped the Dockerfile to
`node:26-slim` and `python:3.14-slim` and changed nothing else, and for a day every job
in this repository installed Python 3.13 and Node 22 while the image it signed ran 3.14.
The last test here rebuilds exactly that shape and checks the gate calls every part of
it, and the one before it runs the gate against this repository, so `python -m pytest
tests/` catches the next such bump even if the CI step were removed.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_runtime_parity.py"
_spec = importlib.util.spec_from_file_location("check_runtime_parity", _GATE_PATH)
parity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity)

REPO_ROOT = Path(__file__).resolve().parent.parent

SHIPPED_NODE = "26"
SHIPPED_PYTHON = "3.14"

DOCKERFILE = """# Build arguments for multi-architecture (implicit with buildx)
FROM node:{node}-slim AS frontend-builder
WORKDIR /build
RUN npm ci

FROM python:{python}-slim
WORKDIR /app
"""

# The comment line is not decoration. `security.yml` justifies scanning the built image
# by naming the userland it scans, and that sentence went stale with the pins.
WORKFLOW = """name: Tests

# `python:{comment_python}-slim` is a Debian userland, so the image is scanned too.
jobs:
  backend:
    name: Backend (Python)
    steps:
      - uses: actions/setup-python@v7
        with:
          python-version: "{python}"
      - uses: actions/setup-node@v7
        with:
          node-version: "{node}"
"""
WORKFLOW_PYTHON_LINE = 10
WORKFLOW_COMMENT_LINE = 3

README = (
    "## Docker build\n\n"
    "The Dockerfile uses a multi-stage build: Node {node} for the frontend, "
    "Python {python}-slim for the final image.\n"
)

DEPENDABOT = (
    "  # runs on. The Dockerfile pins `node:{node}-slim` and `python:{python}-slim`, "
    "and a base image\n"
)


def _tree(
    directory: str,
    *,
    dockerfile_node: str = SHIPPED_NODE,
    dockerfile_python: str = SHIPPED_PYTHON,
    workflow_node: str = SHIPPED_NODE,
    workflow_python: str = SHIPPED_PYTHON,
    workflow_comment_python: str = SHIPPED_PYTHON,
    readme_node: str = SHIPPED_NODE,
    readme_python: str = SHIPPED_PYTHON,
    dependabot_node: str = SHIPPED_NODE,
    dependabot_python: str = SHIPPED_PYTHON,
) -> Path:
    """A throwaway repository whose every declaration is set independently.

    Each test moves exactly one of them away from the Dockerfile, which is the only way
    to tell the gate's separate readings apart.
    """
    root = Path(directory)
    (root / "Dockerfile").write_text(
        DOCKERFILE.format(node=dockerfile_node, python=dockerfile_python), encoding="utf-8"
    )
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "tests.yml").write_text(
        WORKFLOW.format(
            node=workflow_node,
            python=workflow_python,
            comment_python=workflow_comment_python,
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        README.format(node=readme_node, python=readme_python), encoding="utf-8"
    )
    (root / ".github" / "dependabot.yml").write_text(
        DEPENDABOT.format(node=dependabot_node, python=dependabot_python), encoding="utf-8"
    )
    return root


class RuntimeParityGateTests(unittest.TestCase):
    def test_a_tree_that_agrees_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(parity.check(_tree(directory)), [])

    def test_a_workflow_on_the_old_python_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, workflow_python="3.13"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("installs Python 3.13", problems[0])
        self.assertIn("the image ships Python 3.14", problems[0])
        self.assertIn(f".github/workflows/tests.yml:{WORKFLOW_PYTHON_LINE}", problems[0])

    def test_a_workflow_on_the_old_node_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, workflow_node="22"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("installs Node 22", problems[0])

    def test_a_workflow_comment_naming_the_old_image_is_caught(self) -> None:
        """The one the first version of this gate missed.

        In `security.yml` the tag had wrapped across two comment lines mid-token, so
        neither a line-anchored read of the pins nor a grep for `python:3.13-slim` found
        it. The gate reads the comments now, and the sentence has to be written on one
        line to survive it, which is the point.
        """
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, workflow_comment_python="3.13"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("says Python 3.13", problems[0])
        self.assertIn(f".github/workflows/tests.yml:{WORKFLOW_COMMENT_LINE}", problems[0])

    def test_a_readme_naming_the_old_images_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, readme_node="22", readme_python="3.13"))
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all(p.startswith("README.md:") for p in problems), problems)
        self.assertIn("says Node 22", problems[0])
        self.assertIn("says Python 3.13", problems[1])

    def test_a_dependabot_comment_naming_the_old_images_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(
                _tree(directory, dependabot_node="22", dependabot_python="3.13")
            )
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(
            all(p.startswith(".github/dependabot.yml:") for p in problems), problems
        )

    def test_a_version_that_is_an_expression_is_reported_rather_than_passed(self) -> None:
        """`python-version: ${{ matrix.python }}` resolves somewhere this gate cannot read.

        Passing quietly on one would be the same silence the gate exists to end, so it
        says it cannot rule instead of reporting green.
        """
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, workflow_python="${{ matrix.python }}"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("is an expression", problems[0])
        self.assertIn("cannot be checked here", problems[0])

    def test_a_dockerfile_with_no_python_base_refuses_to_guess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(directory)
            (root / "Dockerfile").write_text("FROM node:26-slim\n", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                parity.check(root)
        self.assertIn("0 python base images", str(caught.exception))

    def test_this_repository_agrees_with_its_own_dockerfile(self) -> None:
        """The live check, so the suite alone is enough to catch the next bump."""
        self.assertEqual(parity.check(REPO_ROOT), [])

    def test_the_drift_that_834dfc3_left_behind_would_have_been_caught(self) -> None:
        """The historical shape: the Dockerfile moved, and nothing else did."""
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(
                _tree(
                    directory,
                    workflow_python="3.13",
                    workflow_node="22",
                    workflow_comment_python="3.13",
                    readme_python="3.13",
                    readme_node="22",
                    dependabot_python="3.13",
                    dependabot_node="22",
                )
            )
        self.assertEqual(len(problems), 7, problems)
        self.assertEqual(
            sum(1 for p in problems if p.startswith(".github/workflows/")), 3, problems
        )
        self.assertEqual(sum(1 for p in problems if p.startswith("README.md:")), 2, problems)
        self.assertEqual(
            sum(1 for p in problems if p.startswith(".github/dependabot.yml:")), 2, problems
        )


if __name__ == "__main__":
    unittest.main()
