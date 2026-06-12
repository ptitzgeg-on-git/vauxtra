from __future__ import annotations

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


def main() -> int:
    tracked = _git_ls_files()
    bad_tracked = _find_bad_tracked_files(tracked)
    bad_refs = _find_bad_public_references()
    missing_required = _find_missing_required_files(tracked)

    if not bad_tracked and not bad_refs and not missing_required:
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
