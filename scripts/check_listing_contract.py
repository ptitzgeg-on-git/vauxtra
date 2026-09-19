"""A provider that could not answer must not answer "nothing".

`ProviderListingRefused` exists because an empty list and a refused listing are different
answers, and every caller acts on the difference. `app/api/sync.py` reads a listing with no
matching record as a route that has disappeared: it reports `route_missing` and offers a
Reconcile button whose only meaning is "publish it again". `_service_proxy_state` in
`app/api/services.py` reads the same emptiness as a service that was never published. Both
readings are correct when the provider really holds nothing, and both are wrong when the
provider simply refused to say.

Four providers got this wrong at once, and they were not found together, because the loss
took two different shapes:

  * NPM and Traefik caught `requests.RequestException` inside the listing and returned `[]`
    from the handler. Traefik's was the worse of the two: its own `List routers` permission
    check counts what the listing returns, inside a `try`, so a refused read came back as a
    list of length zero, the counting `except` never fired, and the panel printed
    "0 routers readable" with a tick beside it. A green check on a read that failed.

  * Zoraxy and Cloudflare Tunnel swallowed nothing at all. Their helpers were honest --
    `_get` returns None for a refused session, and `_get_configuration` carries a docstring
    explaining why it refuses to conflate "unreadable" with "empty", since a config read as
    empty and written back would delete every other route of the tunnel -- and then the
    listing turned that None into `[]` on the next line. An audit that looked inside
    `except` handlers walked straight past both.

So the rule cannot be about exception handlers. It is about the returned value, and it is
decidable by reading alone: a listing method never returns an empty collection as a
literal. A provider holding nothing already answers `[]` by building an empty list from an
empty response; a provider that could not finish raises instead. Every listing method in
app/providers/ satisfies this, so any new literal is a new instance of the same defect.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = REPO_ROOT / "app" / "providers"

# The reads whose emptiness the drift check and the service state act on. A method added
# here is a method whose "[]" some caller will read as "this record is gone".
LISTING_METHODS = (
    "list_hosts",
    "list_rewrites",
    "list_records",
    "list_zones",
    "list_tunnels",
    "list_routes",
)


def _falsy_literal(node: ast.AST) -> str | None:
    """The spelling of an empty answer written as a literal, or None."""
    if isinstance(node, ast.List) and not node.elts:
        return "[]"
    if isinstance(node, ast.Dict) and not node.keys:
        return "{}"
    if isinstance(node, ast.Constant) and node.value in (None, False):
        return repr(node.value)
    return None


def check(root: Path) -> tuple[list[str], int]:
    """Every listing method that hands back an empty answer it did not measure."""
    problems: list[str] = []
    inspected = 0
    for path in sorted((root / "app" / "providers").glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as unreadable:
            raise ValueError(f"{path.name} could not be parsed: {unreadable}") from unreadable
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in LISTING_METHODS:
                continue
            inspected += 1
            for statement in ast.walk(node):
                if not isinstance(statement, ast.Return) or statement.value is None:
                    continue
                spelling = _falsy_literal(statement.value)
                if spelling is not None:
                    problems.append(
                        f"  app/providers/{path.name}:{statement.lineno}"
                        f"  {node.name} returns {spelling}"
                    )
    return problems, inspected


def main() -> int:
    try:
        problems, inspected = check(REPO_ROOT)
    except ValueError as unreadable:
        print(f"Listing contract check failed: {unreadable}.")
        return 1

    if problems:
        print("Listing contract check failed: a listing hands back an empty answer.")
        print("")
        for line in problems:
            print(line)
        print("")
        print(
            "An empty list is what the drift check reads as a route that has disappeared,\n"
            "so it offers a Reconcile button that republishes a route which never went\n"
            "anywhere. A provider that could not finish the listing has to say so: raise\n"
            "ProviderListingRefused from app/providers/base.py instead of returning the\n"
            "part that was collected. Build the empty list from an empty response if the\n"
            "provider genuinely holds nothing."
        )
        return 1

    print(
        f"Listing contract check passed ({inspected} listing methods, "
        "none answers an empty collection it did not measure)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
