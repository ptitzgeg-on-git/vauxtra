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

# A public repository prints example addresses. It does not print addresses off somebody's
# actual network, and the difference is invisible in review: a host address from a real LAN
# reads exactly like a placeholder unless you happen to know that LAN. Eight locale files
# and one test fixture shipped that way, and those addresses stayed in the published history
# and in a release tag until the history was rewritten.
#
# Only an allowlist catches this. The repository declares which prefixes it is allowed to
# print -- loopback, the documentation subnet, the lab's own compose networks -- and any
# other RFC 1918 or link-local literal has to be justified here before it can be committed.
ALLOWED_ADDRESS_PREFIXES = (
    "0.0.0.0",          # bind-any
    "127.",             # loopback
    "169.254.169.254",  # cloud instance metadata endpoint
    "192.168.1.",       # the placeholder subnet used across docs, locales and screenshots
    "10.0.0.",          # the lab compose network (lab/providers/docker-compose.yml)
    "172.16.",          # docker-assigned bridge networks
    "172.17.",
    "172.18.",
    "172.19.",
)

_PRIVATE_V4 = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3})\b"
)

# Everything a human writes. Lockfiles and vendored data are not ours to police.
TEXT_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".json",
    ".md", ".yml", ".yaml", ".toml", ".txt", ".sh", ".html", ".example",
)

SKIP_ADDRESS_SCAN = ("frontend/package-lock.json",)


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


def _find_private_addresses(files: list[str]) -> list[str]:
    """Tracked files printing a private address the repository has not declared."""
    hits: list[str] = []
    for rel in files:
        if rel in SKIP_ADDRESS_SCAN or not rel.endswith(TEXT_SUFFIXES):
            continue
        path = Path(rel)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for addr in _PRIVATE_V4.findall(line):
                if not addr.startswith(ALLOWED_ADDRESS_PREFIXES):
                    hits.append(f"{rel}:{lineno}: {addr}")
    return hits


def main() -> int:
    tracked = _git_ls_files()
    bad_tracked = _find_bad_tracked_files(tracked)
    bad_refs = _find_bad_public_references()
    missing_required = _find_missing_required_files(tracked)
    unpinned = _find_unpinned_actions()
    ignored_source = _find_ignored_source_files()
    private_addresses = _find_private_addresses(tracked)

    if not (
        bad_tracked or bad_refs or missing_required or unpinned or ignored_source or private_addresses
    ):
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

    if private_addresses:
        print("\nPrivate addresses that are not declared example addresses:")
        for hit in private_addresses:
            print(f" - {hit}")
        print("\nUse the 192.168.1.x placeholder subnet, or add the prefix to")
        print("ALLOWED_ADDRESS_PREFIXES if it really belongs to this repository.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
