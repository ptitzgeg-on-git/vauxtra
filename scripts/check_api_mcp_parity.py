import re
import sys
from pathlib import Path

API_PATTERN = re.compile(r'@router\.(?:get|post|put|delete|patch)\("(/api[^"\\)]*)"')
MCP_PATTERN = re.compile(r'client\.(?:get|post|put|delete|patch)\(f?"(/[^"\\)]*)"')

ALLOWED_API_ONLY = {
    "/api/logs/stream",  # SSE continuous stream; covered via bounded snapshot helper
}


def normalize(path: str) -> str:
    p = path.strip()
    if not p.startswith("/api"):
        p = "/api" + p
    return re.sub(r"\{[^}]+\}", "{}", p)


def collect_api_paths(repo_root: Path) -> set[str]:
    paths: set[str] = set()
    for file_path in (repo_root / "app" / "api").glob("*.py"):
        text = file_path.read_text(encoding="utf-8")
        for match in API_PATTERN.finditer(text):
            paths.add(normalize(match.group(1)))
    return paths


def collect_mcp_paths(repo_root: Path) -> set[str]:
    paths: set[str] = set()
    for file_path in (repo_root / "vauxtra_mcp" / "tools").glob("*.py"):
        text = file_path.read_text(encoding="utf-8")
        for match in MCP_PATTERN.finditer(text):
            paths.add(normalize(match.group(1)))
    return paths


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    api_paths = collect_api_paths(repo_root)
    mcp_paths = collect_mcp_paths(repo_root)

    api_only = sorted(api_paths - mcp_paths)
    mcp_only = sorted(mcp_paths - api_paths)
    covered = len(api_paths & mcp_paths)

    print(f"API_COUNT {len(api_paths)}")
    print(f"MCP_COUNT {len(mcp_paths)}")
    print(f"COVERED_COUNT {covered}")
    print(f"API_ONLY_COUNT {len(api_only)}")
    print(f"MCP_ONLY_COUNT {len(mcp_only)}")

    unexpected_api_only = [p for p in api_only if p not in ALLOWED_API_ONLY]
    if unexpected_api_only:
        print("Unexpected API-only endpoints:")
        for path in unexpected_api_only:
            print(path)
        return 1

    if mcp_only:
        print("MCP-only endpoints (non blocking):")
        for path in mcp_only:
            print(path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
