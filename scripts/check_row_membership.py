"""A database row answers `in` about its values, and every caller meant its columns.

`"expose_mode" in svc` reads like a question about the schema and is not one. A
`sqlite3.Row` is a sequence, so `in` walks the values of the row. No service column ever
holds the string "expose_mode", so the six sites in `app/api/sync.py` that asked were
deterministically False, every one of them fell through to its default, and nothing in the
product ever contradicted them.

That is what makes this shape worth a gate rather than a fix. It does not misjudge one
case: it answers "no" to all of them, silently, for ever. A whole mode of the product --
services exposed through a Cloudflare tunnel -- was therefore never examined. The drift
check compared such a service against no provider at all and answered "in sync" about a
route that had never been published; the dry-run beside it said there was nothing to do;
the withdrawal aimed at the wrong hostname; and `public_target_mode = "auto"` reached the
resolver as "manual" on every service in the database.

The rule is decidable by reading. Inside `app/`, a string literal is never tested with
`in` against a value this file can see is a database row. Ask `_has_column(row, "name")`
from `app/api/sync.py`, or `row.keys()`, either of which answers on the column names.

A row is recognised three ways, all local to the function being read:

  * a name assigned from a cursor -- anything whose expression ends in `.fetchone()`,
    `.fetchall()` or `.execute(...)`;
  * a `for` target iterating over one of those;
  * a parameter whose name is in ROW_PARAMETERS below, the spellings this repository uses
    for a row it was handed rather than one it read.

Simple aliasing (`svc = service_row`) is followed. Anything else is left alone on purpose:
a gate that cried about every dict would be a gate nobody reads, and a dict already
answers on its keys.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Parameters this repository passes a row into. A name added here is a name whose `in`
#: test will be judged; the list is short because the spellings really are that few.
ROW_PARAMETERS = frozenset({"svc", "row", "service_row", "provider_row", "service", "rec"})

#: The calls that hand back a row, or rows.
CURSOR_CALLS = frozenset({"fetchone", "fetchall", "fetchmany", "execute"})


def _ends_in_cursor_call(node: ast.AST) -> bool:
    """Whether this expression is the result of reading a cursor."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in CURSOR_CALLS
    )


def _row_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every local name this function holds a database row under.

    Read in source order so an alias sees what was bound above it. Nested functions are
    walked with the enclosing one, which is deliberate: a closure that reads `svc` from the
    scope around it is asking the same wrong question at the same cost.
    """
    names = {
        arg.arg
        for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
        if arg.arg in ROW_PARAMETERS
    }

    def _bind(target: ast.AST, source: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        if _ends_in_cursor_call(source) or (
            isinstance(source, ast.Name) and source.id in names
        ):
            names.add(target.id)
        elif target.id in names:
            names.discard(target.id)  # rebound to something else; stop judging it

    # Sorted rather than walked in tree order: `ast.walk` is breadth-first, and an alias
    # has to see what was bound above it, not beside it.
    ordered = sorted(
        ast.walk(function),
        key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)),
    )
    for node in ordered:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _bind(target, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            _bind(node.target, node.value)
        elif (
            isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
            and _ends_in_cursor_call(node.iter)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _string_in_row(test: ast.Compare, rows: set[str]) -> str | None:
    """The spelling of a string-literal `in` test against a row, or None.

    `not in` counts too: it is the same question with the answer inverted, and inverting a
    constantly wrong answer does not make it right.
    """
    if len(test.ops) != 1 or not isinstance(test.ops[0], (ast.In, ast.NotIn)):
        return None
    left = test.left
    if not (isinstance(left, ast.Constant) and isinstance(left.value, str)):
        return None
    right = test.comparators[0]
    if not (isinstance(right, ast.Name) and right.id in rows):
        return None
    word = "not in" if isinstance(test.ops[0], ast.NotIn) else "in"
    return f'"{left.value}" {word} {right.id}'


def check(root: Path) -> tuple[list[str], int]:
    """Every string-literal membership test asked of a database row."""
    problems: list[str] = []
    inspected = 0
    for path in sorted((root / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as unreadable:
            raise ValueError(f"{path.name} could not be parsed: {unreadable}") from unreadable
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rows = _row_names(node)
            if not rows:
                continue
            inspected += 1
            for test in ast.walk(node):
                if not isinstance(test, ast.Compare):
                    continue
                spelling = _string_in_row(test, rows)
                if spelling is not None:
                    problems.append(f"  {relative}:{test.lineno}  {spelling}")
    return problems, inspected


def main() -> int:
    try:
        problems, inspected = check(REPO_ROOT)
    except ValueError as unreadable:
        print(f"Row membership check failed: {unreadable}.")
        return 1

    if problems:
        print("Row membership check failed: a database row was asked about its values.")
        print("")
        for line in problems:
            print(line)
        print("")
        print(
            "A sqlite3.Row is a sequence, so `in` searches the values of the row and not\n"
            "its column names. The test above is False for every row that will ever reach\n"
            "it, so whatever it guards is dead and nothing will ever say so. Ask\n"
            "_has_column(row, \"name\") from app/api/sync.py, or `name in row.keys()`."
        )
        return 1

    print(
        f"Row membership check passed ({inspected} functions holding a row, "
        "none asks one about its values)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
