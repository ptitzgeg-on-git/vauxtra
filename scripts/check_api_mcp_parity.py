"""Every API route must be reachable through the MCP bridge, and documented.

The gate used to compare paths alone, and a path is not a route. `PUT /api/templates/{tid}`
counted as covered because `get_template` and `delete_template` mention the same path: the
bridge could create a template and delete one, but not edit one, and this script reported
full parity while that was true. It now compares (method, path), which is what a route is.

It also checks the bridge's README against the tools that exist. That list had drifted to
31 of 84 tools and omitted two whole modules -- an agent reading it would conclude the
bridge could not manage templates, settings, webhooks or API keys at all. A tool nobody
knows about is as unreachable as one that was never written, so the drift fails the build.
"""
import ast
import re
import sys
from pathlib import Path

API_PATTERN = re.compile(r'@router\.(get|post|put|delete|patch)\("(/api[^"\\)]*)"')
MCP_PATTERN = re.compile(r'client\.(get|post|put|delete|patch)\(\s*f?"(/[^"\\)]*)"')

# Routes the bridge deliberately does not expose, each with the reason it is not a gap.
ALLOWED_API_ONLY = {
    # A continuous SSE stream has no place in a request/response tool; `stream_logs_snapshot`
    # reads a bounded slice of it with its own client.
    ("GET", "/api/logs/stream"),
    # Raw per-provider record editing. A service is the bridge's unit of work: it pushes a
    # service and the provider rows follow, so reaching underneath is a way to create drift.
    ("GET", "/api/providers/{}/dns-records"),
    ("POST", "/api/providers/{}/dns-records"),
    ("DELETE", "/api/providers/{}/dns-records/{}"),
    ("GET", "/api/providers/{}/proxy-hosts"),
    ("POST", "/api/providers/{}/proxy-hosts"),
    ("DELETE", "/api/providers/{}/proxy-hosts/{}"),
}


def normalize(path: str) -> str:
    p = path.strip()
    if not p.startswith("/api"):
        p = "/api" + p
    return re.sub(r"\{[^}]+\}", "{}", p)


def _collect(files, pattern) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for file_path in files:
        text = file_path.read_text(encoding="utf-8")
        for method, path in pattern.findall(text):
            routes.add((method.upper(), normalize(path)))
    return routes


def collect_api_routes(repo_root: Path) -> set[tuple[str, str]]:
    return _collect((repo_root / "app" / "api").glob("*.py"), API_PATTERN)


def collect_mcp_routes(repo_root: Path) -> set[tuple[str, str]]:
    return _collect((repo_root / "vauxtra_mcp" / "tools").glob("*.py"), MCP_PATTERN)


def collect_mcp_tools(repo_root: Path) -> set[str]:
    """Every function decorated with `@mcp.tool()`, read from the AST rather than grepped."""
    tools: set[str] = set()
    for file_path in (repo_root / "vauxtra_mcp" / "tools").glob("*.py"):
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
                for d in node.decorator_list
            ):
                tools.add(node.name)
    return tools


def collect_documented_tools(repo_root: Path) -> set[str]:
    """Tool names in the first column of the README's "Available tools" tables.

    Only that column, so the prose is free to mention `read`, `write` or `VAUXTRA_URL`
    without either passing for a tool or being reported as one that does not exist.
    """
    text = (repo_root / "vauxtra_mcp" / "README.md").read_text(encoding="utf-8")
    start = text.find("## Available tools")
    if start == -1:
        return set()
    end = text.find("\n## ", start + 1)
    section = text[start:end if end != -1 else len(text)]

    documented: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        documented.update(re.findall(r"`([a-z_][a-z0-9_]*)`", first_cell))
    return documented


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    api_routes = collect_api_routes(repo_root)
    mcp_routes = collect_mcp_routes(repo_root)

    api_only = sorted(api_routes - mcp_routes)
    mcp_only = sorted(mcp_routes - api_routes)
    covered = len(api_routes & mcp_routes)

    print(f"API_COUNT {len(api_routes)}")
    print(f"MCP_COUNT {len(mcp_routes)}")
    print(f"COVERED_COUNT {covered}")
    print(f"API_ONLY_COUNT {len(api_only)}")
    print(f"MCP_ONLY_COUNT {len(mcp_only)}")

    unexpected_api_only = [r for r in api_only if r not in ALLOWED_API_ONLY]
    if unexpected_api_only:
        print("Unexpected API-only routes:")
        for method, path in unexpected_api_only:
            print(f"{method} {path}")
        return 1

    stale = sorted(ALLOWED_API_ONLY - api_routes)
    if stale:
        # An allowlist entry for a route that no longer exists is an exemption nobody is
        # watching -- and the next route to land on that path inherits it.
        print("Allowlisted routes that no longer exist (remove them):")
        for method, path in stale:
            print(f"{method} {path}")
        return 1

    if mcp_only:
        print("MCP-only routes (non blocking):")
        for method, path in mcp_only:
            print(f"{method} {path}")

    tools = collect_mcp_tools(repo_root)
    documented = collect_documented_tools(repo_root)
    print(f"TOOL_COUNT {len(tools)}")
    print(f"DOCUMENTED_COUNT {len(documented)}")

    undocumented = sorted(tools - documented)
    if undocumented:
        print("Tools missing from vauxtra_mcp/README.md:")
        for name in undocumented:
            print(name)
        return 1

    phantom = sorted(documented - tools)
    if phantom:
        # A README that promises a tool is worse than one that omits it: the omission is
        # discovered by reading the code, the promise by calling something that is not there.
        print("Tools documented in vauxtra_mcp/README.md that do not exist:")
        for name in phantom:
            print(name)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
