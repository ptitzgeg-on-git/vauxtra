from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_TRACKED_FILES = {
    "CLAUDE.md",
    "SESSION_SUMMARY.md",
    "QA_REPORT.md",
    "DOCUMENTATION_INDEX.md",
    "TESTING_GUIDE.md",
    "docs/UX_BUG_TRACKER.md",
}

FORBIDDEN_TRACKED_PREFIXES: tuple[str, ...] = ()

PUBLIC_DOC_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
)

FORBIDDEN_REFERENCE_TOKENS = (
    "UX_BUG_TRACKER",
    "TESTING_GUIDE",
    "CLAUDE.md",
    "SESSION_SUMMARY",
    "QA_REPORT",
    "DOCUMENTATION_INDEX",
)

FORBIDDEN_PROCESS_ATTRIBUTION_TOKENS = (
    "generated with",
    "written by assistant",
    "authored by bot",
    "prompt:",
)

REQUIRED_TRACKED_FILES = (
    ".github/CODEOWNERS",
    "SECURITY.md",
    "LICENSE",
)

# `uses: owner/repo[/sub]@ref`. A local `./.github/workflows/x.yml` has no `@` and is not
# matched at all.
_USES = re.compile(r"^\s*-?\s*uses:\s*([A-Za-z0-9_.-]+/[^@\s]+)@(\S+)")
_SHA = re.compile(r"^[0-9a-f]{40}$")

# Where hand-written source lives. An ignore rule has no business reaching in here.
SOURCE_ROOTS = ("app", "frontend/src", "vauxtra_mcp", "tests", "scripts")

# Extensions that mean "somebody wrote this", as opposed to a build or cache artifact
# (`.pyc`, `.tsbuildinfo`) that is ignored inside these roots on purpose.
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".json")


def _git_ls_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _find_bad_tracked_files(files: list[str]) -> list[str]:
    bad: list[str] = []
    for path in files:
        if path in FORBIDDEN_TRACKED_FILES:
            bad.append(path)
            continue
        if path.startswith(FORBIDDEN_TRACKED_PREFIXES):
            bad.append(path)
    return sorted(set(bad))


def _find_bad_public_references() -> list[str]:
    bad_refs: list[str] = []
    for file_path in PUBLIC_DOC_FILES:
        p = Path(file_path)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for token in FORBIDDEN_REFERENCE_TOKENS:
            if token in text:
                bad_refs.append(f"{file_path}: contains '{token}'")
        lower_text = text.lower()
        for token in FORBIDDEN_PROCESS_ATTRIBUTION_TOKENS:
            if token in lower_text:
                bad_refs.append(f"{file_path}: contains '{token}'")
    return sorted(set(bad_refs))


def _find_missing_required_files(files: list[str]) -> list[str]:
    return sorted(f for f in REQUIRED_TRACKED_FILES if f not in files)


def _find_unpinned_actions() -> list[str]:
    """Third-party actions referenced by a tag instead of a commit SHA.

    A Git tag is movable by whoever owns the repository behind it, and that is not a
    theoretical worry: in March 2025 every tag of `tj-actions/changed-files` was repointed
    at a commit that dumped runner secrets into the build log. The workflow that publishes
    Vauxtra holds `packages: write` and the `id-token: write` used moments later by
    `cosign sign`, so an equivalent incident there does not just leak -- it produces a
    signed image with a valid provenance attestation. Pinning is the whole defence, and a
    pin nothing enforces comes undone the first time someone adds a step.
    """
    bad: list[str] = []
    for path in sorted(Path(".github/workflows").glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _USES.match(line)
            if match and not _SHA.match(match.group(2)):
                bad.append(f"{path.as_posix()}:{number}: {match.group(1)}@{match.group(2)}")
    return bad


def _find_ignored_source_files() -> list[str]:
    """Source files that exist on disk but that .gitignore hides from `git add`.

    Not hypothetical: the rule was `data/`, unanchored, written for the SQLite directory
    at the root -- so it also matched `frontend/src/components/features/settings/data/`,
    and `git add -A` skipped four components without printing anything. The tree built
    locally, where the files are on disk; only CI, which checks out what git actually
    holds, said `Cannot find module './data/SyncSection'`. A silent omission is the
    failure mode worth a test -- a loud one gets fixed by whoever hits it.
    """
    result = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", *SOURCE_ROOTS],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().endswith(SOURCE_SUFFIXES)
    )


def main() -> int:
    tracked = _git_ls_files()
    bad_tracked = _find_bad_tracked_files(tracked)
    bad_refs = _find_bad_public_references()
    missing_required = _find_missing_required_files(tracked)
    unpinned = _find_unpinned_actions()
    ignored_source = _find_ignored_source_files()

    if not (bad_tracked or bad_refs or missing_required or unpinned or ignored_source):
        print("Repo hygiene check passed")
        return 0

    print("Repo hygiene check failed")
    if bad_tracked:
        print("\nForbidden tracked files:")
        for path in bad_tracked:
            print(f" - {path}")

    if bad_refs:
        print("\nForbidden public references:")
        for ref in bad_refs:
            print(f" - {ref}")

    if missing_required:
        print("\nMissing required tracked files:")
        for path in missing_required:
            print(f" - {path}")

    if unpinned:
        print("\nActions referenced by a movable tag instead of a commit SHA:")
        for ref in unpinned:
            print(f" - {ref}")
        print("\nUse `uses: owner/repo@<40-char sha> # vX.Y.Z`.")

    if ignored_source:
        print("\nSource files on disk that .gitignore hides from git:")
        for path in ignored_source:
            print(f" - {path}")
        print("\nAnchor the rule to the repository root (`/data/`, not `data/`), or")
        print("negate it for this path. Until then these files build locally and are")
        print("missing from every clone.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
