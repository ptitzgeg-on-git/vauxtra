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
    "docs/ai/AI_AUTONOMY_SETUP.md",
    "docs/ai/WORKSPACE_STRUCTURE.md",
}

FORBIDDEN_TRACKED_PREFIXES = (
    "docs/ai/",
)

PUBLIC_DOC_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
)

FORBIDDEN_REFERENCE_TOKENS = (
    "docs/ai",
    "AI_AUTONOMY_SETUP",
    "WORKSPACE_STRUCTURE",
    "UX_BUG_TRACKER",
    "TESTING_GUIDE",
    "CLAUDE.md",
    "SESSION_SUMMARY",
    "QA_REPORT",
    "DOCUMENTATION_INDEX",
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
    return sorted(set(bad_refs))


def main() -> int:
    tracked = _git_ls_files()
    bad_tracked = _find_bad_tracked_files(tracked)
    bad_refs = _find_bad_public_references()

    if not bad_tracked and not bad_refs:
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
