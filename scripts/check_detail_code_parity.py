"""A diagnostic sentence is written twice: once in Python, once in every locale file.

The API answers a validation check with the English sentence in `detail` and the short code
it was written from in `detail_code`, and the panel looks up
`providers.diag.detail.<detail_code>` so the operator reads it in their own language. Drift
and preflight do the same through `detail_key`. When the lookup misses, the frontend prints
the English sentence the API sent -- `checkDetailText()` compares the result against the key
it asked for and falls back -- so a code nobody translated does not break the page. It just
makes one line English, in the middle of a panel that is otherwise French, German or
Japanese, and nothing anywhere says so.

That is how `zone_match` and `zone_missing` sat untranslated: deSEC and PowerDNS emit them
from their "Domain match" / "Zone match" check, no locale file has ever had the keys, and
the graceful fallback is what kept it quiet. The two ends do not reference each other -- one
is a string literal in a provider, the other a line of JSON -- so nothing was wrong enough
to notice.

This gate reads the codes out of the Python and asks en.json about each one. It is
deliberately one-directional:

  * A code with no key fails. That is the defect, and it is decidable: the code was found,
    the key either exists or does not.
  * A key with no code does NOT fail, and is not reported. Extraction cannot be complete --
    a code can be built from a variable, returned in a tuple, or assembled at runtime -- so
    an unclaimed key is evidence about this script, not about the repository. Three of them
    (`zones_error`, `dns_target_required`, `dns_target_detection_failed`) are emitted by code
    shapes an earlier draft of this file could not see, and reporting them would have been
    the check accusing itself. Dead locale keys are a real thing to want, but they need a
    tool that can prove a key is unreachable, and this is not that tool.

Missing an emitter therefore costs coverage, never a false failure. Only `detail_code` and
`detail_key` are read: `type`, `action` and `name` are also sent to the UI, but those words
are used for unrelated things all over the backend -- pihole's HTTP calls pass
`action="add"` -- and a check that reads them would fail on strings that were never meant to
be translated.

`detail_key` is accepted under either of its two prefixes rather than being matched to the
module that emitted it. Preflight writes `expose.preflight.detail.<key>` and drift writes
`services.drift.detail.<key>`, and telling them apart means mapping file paths to prefixes,
which would turn a moved function into a red build. Asking only that some locale key claims
the code is weaker and cannot be wrong.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BACKEND = ROOT / "app"
EN_JSON = ROOT / "frontend" / "src" / "locales" / "en.json"

# Which locale keys a code of each kind is looked up under. `detail_key` has two homes; see
# the module docstring for why both are accepted for either.
PREFIXES = {
    "detail_code": ("providers.diag.detail.",),
    "detail_key": ("expose.preflight.detail.", "services.drift.detail."),
}

PLURAL_SUFFIX = re.compile(r"_(zero|one|two|few|many|other)$")

# What a code looks like. Every one of them is a snake_case identifier, and the sentences
# they travel beside are prose: capitals, spaces, punctuation, interpolated hostnames. The
# distinction matters because some codes are returned in a tuple next to their sentence, and
# reading the wrong half of one would fail the build over a key nobody should have written.
CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def literals(node: ast.AST, const: dict[str, set[str]]) -> set[str]:
    """Every string the expression can evaluate to, where that is statically knowable.

    `const` holds the single-assignment string variables of the enclosing function, which is
    how `zones_code = "zones_error"` reaches the `_add()` call three lines below it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if CODE.match(node.value) else set()
    if isinstance(node, ast.Name):
        return set(const.get(node.id, ()))
    if isinstance(node, ast.IfExp):
        return literals(node.body, const) | literals(node.orelse, const)
    if isinstance(node, ast.BoolOp):
        return set().union(*(literals(v, const) for v in node.values))
    return set()


def string_constants(scope: ast.AST) -> dict[str, set[str]]:
    """Names bound to string literals anywhere in a scope, dropping any rebound twice.

    A name assigned two different literals is still resolvable -- both are candidate codes --
    but a name assigned anything non-literal is dropped entirely, because the literal it also
    receives somewhere is then only one of its values.
    """
    values: dict[str, set[str]] = {}
    poisoned: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            found = literals(node.value, {})
            if found:
                values.setdefault(target.id, set()).update(found)
            else:
                poisoned.add(target.id)
    return {k: v for k, v in values.items() if k not in poisoned}


def parameter_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = fn.args
    return [p.arg for p in (*args.posonlyargs, *args.args)]


def forwarded_fields(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """{field: parameter} for each tracked field this function fills straight from an argument.

    Every provider writes its checks through a local helper of the shape

        def _add(name, ok, detail, code, blocking=True, **params):
            checks.append({"name": name, ..., "detail_code": code, ...})

    so the code itself is four arguments into a call, not a value in a visible dict.
    """
    names = set(parameter_names(fn))
    out: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in PREFIXES
                    and isinstance(value, ast.Name)
                    and value.id in names
                ):
                    out[key.value] = value.id
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in PREFIXES
                    and isinstance(node.value, ast.Name)
                    and node.value.id in names
                ):
                    out[target.slice.value] = node.value.id
    return out


def codes_in(path: Path) -> dict[str, set[tuple[str, int]]]:
    """{field: {(code, line)}} for one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    const = string_constants(tree)
    found: dict[str, set[tuple[str, int]]] = {field: set() for field in PREFIXES}

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if isinstance(key, ast.Constant) and key.value in PREFIXES:
                    for code in literals(value, const):
                        found[key.value].add((code, getattr(value, "lineno", node.lineno)))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in PREFIXES
                ):
                    for code in literals(node.value, const):
                        found[target.slice.value].add((code, node.lineno))

    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        scope = {**const, **string_constants(fn)}
        # A function that says in its own docstring which field it produces, and hands it back
        # in a tuple. Every element is read rather than a position guessed, which is safe now
        # that a code has to look like one: the sentence beside it cannot match.
        doc = ast.get_docstring(fn) or ""
        for field in PREFIXES:
            if field not in doc:
                continue
            for ret in (n for n in ast.walk(fn) if isinstance(n, ast.Return)):
                if not isinstance(ret.value, ast.Tuple):
                    continue
                for element in ret.value.elts:
                    for code in literals(element, scope):
                        found[field].add((code, element.lineno))

        names = parameter_names(fn)
        for field, param in forwarded_fields(fn).items():
            if param not in names:
                continue
            index = names.index(param)
            for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
                if not (isinstance(call.func, ast.Name) and call.func.id == fn.name):
                    continue
                argument = call.args[index] if len(call.args) > index else None
                for keyword in call.keywords:
                    if keyword.arg == param:
                        argument = keyword.value
                if argument is None:
                    continue
                for code in literals(argument, scope):
                    found[field].add((code, argument.lineno))

    return found


def main() -> int:
    if not EN_JSON.exists():
        print(f"Detail code parity check failed: {EN_JSON} is missing.")
        return 1

    keys = {PLURAL_SUFFIX.sub("", k) for k in json.loads(EN_JSON.read_text(encoding="utf-8"))}

    missing: list[str] = []
    checked = 0
    for path in sorted(BACKEND.rglob("*.py")):
        for field, entries in codes_in(path).items():
            for code, line in sorted(entries):
                checked += 1
                if any(prefix + code in keys for prefix in PREFIXES[field]):
                    continue
                wanted = " or ".join(f"'{prefix}{code}'" for prefix in PREFIXES[field])
                missing.append(f"  {path.relative_to(ROOT).as_posix()}:{line} emits {field}='{code}', and en.json has no {wanted}")

    if missing:
        print("Detail code parity check failed: the frontend has nothing to say for these.\n")
        print("\n".join(sorted(set(missing))))
        print(
            "\nThe panel falls back to the English sentence the API sent, so nothing breaks:\n"
            "one line stays English while the rest of the page is translated. Add the key to\n"
            "frontend/src/locales/en.json and to the other locale files beside it."
        )
        return 1

    print(f"Detail code parity check passed ({checked} backend codes, every one translatable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
