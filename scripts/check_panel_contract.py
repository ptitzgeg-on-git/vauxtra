#!/usr/bin/env python3
"""The panel writes JSON too, and nothing compared it to the model that reads it.

`check_api_mcp_parity.py` holds the MCP bridge to the Pydantic model of the route it posts
to. The panel posts to the same routes and nothing held it to anything. Pydantic ignores a
key it does not declare, so a field the panel sends is dropped in silence and answered
`200`: the form said saved, the column never moved, and the next `GET` hands back the old
value with nothing to say why.

TypeScript does not close this. `api.post<T>(url, payload)` types the *answer*, never the
body, and the body's own type is declared in the panel -- so the panel can promise a field
the server has never heard of and `tsc` agrees, because both halves are internally
consistent and neither has read the other.

What it reads
-------------
Every `api.post(...)` / `api.put(...)` under `frontend/src` that carries a body, with the
key set resolved by binding names the way the language binds them:

  * an inline object literal, `...spread` included, resolved recursively;
  * an identifier bound by the innermost *enclosing* function's parameter list -- annotated
    directly, destructured (rest element included) out of an annotated object, or typed by
    the third generic argument of the `useMutation<Result, Error, Vars>` it belongs to;
  * an identifier bound by a `const` in a block that actually contains the call site;
  * a call, resolved through the callee's declared return type or its single `return`.

Names are looked up the way a module does: the declaration in this file, else the file this
file imports the name from, else a single unambiguous declaration in the panel. Both halves
of that matter. `buildPayload` is declared twice -- a local one in `ExposeModal.tsx` and the
exported one in `providerConstants.ts` -- and eleven type names are declared in two files
each, `Provider` among them. A flat repo-wide table would quietly union two unrelated
`Provider`s and answer with keys that exist in neither call.

Which is the whole design rule here: a resolver that guesses is worse than one that refuses.
An earlier draft took "the last `name:` above the call", read `data` off an
`onSuccess: (data: ProviderValidationResult)` twenty lines up, and reported a provider
update as sending `ok, health, validation`. A wrong key set both misses real divergence and
invents fake divergence, and the fake one teaches everybody to ignore the gate.

So every call the resolver cannot bind is UNCOMPARED, and uncompared is not clean: each one
is listed in `ALLOWED_UNCOMPARED` with its reason, and a call that stops being listed fails
the build. An allowance nobody is watching is one the next call site inherits without ever
having argued for it -- the same rule `check_api_mcp_parity.py` applies to its own
exemptions, for the same reason.

Only the direction that loses data is a failure. A key the panel declares and the model does
not is a value thrown away; a field the model declares and the panel omits is the default
doing its job, which is what a default is for.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_api_mcp_parity import ApiModel, ApiRoute, collect_api_contracts  # noqa: E402

PANEL_ROOT = Path("frontend") / "src"

# Resolution recurses through calls, aliases and parent types; eight hops is well past
# anything the panel does today (`payload()` -> `buildPayload()` -> literal is three) and
# stops a cycle the `seen` sets miss from running forever.
MAX_DEPTH = 8

CALL = re.compile(r"\bapi\.(post|put)\s*(?:<[^(]*?>)?\s*\(")
IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
CALL_HEAD = re.compile(r"^([A-Za-z_$][\w$]*)\s*\(")
KEY = re.compile(r"^\s*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_$][\w$]*))\s*(?::|$)")
MEMBER = re.compile(
    r"^\s*(?:readonly\s+)?(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_$][\w$]*))\s*\??\s*:\s*(.+)$",
    re.S,
)
TYPE_DECL = re.compile(
    r"\b(?:export\s+)?(?:(interface)\s+([A-Za-z_$][\w$]*)|(type)\s+([A-Za-z_$][\w$]*)\s*=)"
)
FN_DECL = re.compile(r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(?=\()")
CONST_FN = re.compile(
    r"\b(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?=\()"
)
IMPORT = re.compile(r"\bimport\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
UTILITY = re.compile(r"^(Partial|Required|Readonly|NonNullable|Pick|Omit)\s*<(.*)>$", re.S)


# Calls this gate does not compare, and why each one is allowed to stay that way.
#
# Keyed by the file, the verb and the URL expression exactly as written, so moving a call
# down a file does not churn the table. The URL is part of the key because one file may hold
# several calls that fail for different reasons.
ALLOWED_UNCOMPARED: dict[tuple[str, str, str], str] = {
    (
        "components/features/settings/GeneralTab.tsx",
        "POST",
        "'/settings'",
    ): "the settings form posts a `Record<string, string>` of whatever keys it rendered, and "
       "`/api/settings` reads the same free-form bag -- there is no field list on either side "
       "for this gate to compare",
    (
        "components/features/settings/data/SyncSection.tsx",
        "POST",
        "'/services/import'",
    ): "`useMutation<ImportResult, Error, unknown>` -- the body is whatever the operator's "
       "backup file held, and the route takes it as a dict for the same reason",
    (
        "components/features/settings/TaxonomyTab.tsx",
        "POST",
        "cfg.endpoint",
    ): "one form drives `/api/tags` and `/api/environments`; the route is a config field, so "
       "the body (`name`, `color`) has two models and the gate cannot tell which one is meant",
    (
        "components/features/settings/TaxonomyTab.tsx",
        "PUT",
        "`${cfg.endpoint}/${id}`",
    ): "the update half of the same two-endpoint form, unreadable for the same reason",
}


# Keys the panel sends that the model of the route does not declare, and why each is not a
# defect. Every entry here is a value the panel puts on the wire and the server drops.
ALLOWED_PANEL_KEYS: dict[tuple[str, str, str], str] = {}


# --- source views ----------------------------------------------------------


def blank(text: str, strings: bool) -> str:
    """A same-length copy with comments -- and optionally string *contents* -- blanked out.

    Offsets stay usable across both views, so a span found in one can be sliced out of the
    other. Comments go in both: a doc comment between two members of an interface is not a
    member, and a resolver that reads it as one declares the whole type unreadable. Strings
    go only in the bracket-matching view, where a `)` inside a message or a `{` inside a
    template literal would otherwise throw off the count -- key names and URLs are still
    read from a view that kept them.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if c in "\"'`":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == c:
                    break
                j += 1
            if strings:
                for k in range(i + 1, min(j, n)):
                    if out[k] != "\n":
                        out[k] = " "
            i = min(j, n) + 1
            continue
        i += 1
    return "".join(out)


def bracket_map(code: str) -> dict[int, int]:
    """Every matched bracket pair, in both directions."""
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for i, c in enumerate(code):
        if c in "([{":
            stack.append(i)
        elif c in ")]}" and stack:
            j = stack.pop()
            pairs[j] = i
            pairs[i] = j
    return pairs


def split_spans(
    code: str, a: int, b: int, seps: str, angle: bool = False
) -> list[tuple[int, int]]:
    """Spans of `code[a:b]` split on `seps` at bracket depth zero."""
    out: list[tuple[int, int]] = []
    depth = 0
    start = a
    i = a
    while i < b:
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif angle and c == "<":
            depth += 1
        elif angle and c == ">" and i and code[i - 1] != "=":
            depth -= 1
        elif c in seps and depth == 0:
            out.append((start, i))
            start = i + 1
        i += 1
    out.append((start, b))
    return [(x, y) for x, y in out if code[x:y].strip()]


def split_str(text: str, seps: str, angle: bool = False) -> list[str]:
    """`split_spans` for a string that is not part of a source file."""
    code = blank(text, strings=True)
    return [text[x:y].strip() for x, y in split_spans(code, 0, len(code), seps, angle)]


@dataclass
class TsType:
    """A declared object type: its own members, plus the types it folds in."""

    name: str
    members: dict[str, str] = field(default_factory=dict)
    parents: list[str] = field(default_factory=list)
    readable: bool = True


@dataclass
class FnDecl:
    """A declared function, and where its body is."""

    ret: str | None
    body: tuple[int, int]
    body_is_expr: bool


@dataclass
class Source:
    """One panel source file, with everything derived from it."""

    rel: str
    raw: str
    code: str
    brackets: dict[int, int] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)
    types: dict[str, TsType] = field(default_factory=dict)
    functions: dict[str, FnDecl] = field(default_factory=dict)

    @property
    def stem(self) -> str:
        return self.rel.rsplit(".", 1)[0]

    def line_of(self, pos: int) -> int:
        return self.raw.count("\n", 0, pos) + 1

    def enclosing_block(self, pos: int) -> tuple[int, int] | None:
        """The innermost `{ ... }` containing `pos`."""
        best: tuple[int, int] | None = None
        for open_at, close_at in self.brackets.items():
            if self.code[open_at] != "{" or not open_at < pos < close_at:
                continue
            if best is None or open_at > best[0]:
                best = (open_at, close_at)
        return best


def after_params(code: str, close: int, want: str) -> tuple[str | None, int] | None:
    """Past a parameter list: the return annotation, if any, and where `want` starts."""
    i = close + 1
    while i < len(code) and code[i].isspace():
        i += 1
    ret: str | None = None
    if i < len(code) and code[i] == ":":
        start = i + 1
        depth = 0
        j = start
        while j < len(code):
            c = code[j]
            if c == "=" and code.startswith("=>", j) and depth == 0:
                break
            if c in "([<":
                depth += 1
            elif c == "{":
                if depth == 0:
                    break
                depth += 1
            elif c in ")]}>":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        ret = code[start:j].strip() or None
        i = j
        while i < len(code) and code[i].isspace():
            i += 1
    if not code.startswith(want, i):
        return None
    return ret, i


def body_span(src: Source, arrow_at: int) -> tuple[int, int, bool]:
    """Where an arrow's body starts and ends, and whether it is a bare expression."""
    i = arrow_at + 2
    while i < len(src.code) and src.code[i].isspace():
        i += 1
    if i < len(src.code) and src.code[i] == "{" and i in src.brackets:
        return i, src.brackets[i] + 1, False
    depth = 0
    j = i
    while j < len(src.code):
        c = src.code[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c in ",;" and depth == 0:
            break
        j += 1
    return i, j, True


# --- the index -------------------------------------------------------------

# A type expression together with the file whose imports give its names meaning.
Typed = tuple["Source", str]


class PanelIndex:
    """Every declaration the panel makes, and the resolution built on top of them."""

    def __init__(self, repo_root: Path) -> None:
        self.sources: list[Source] = []
        self.by_stem: dict[str, Source] = {}
        self.types_by_name: dict[str, list[Source]] = {}
        self.fns_by_name: dict[str, list[Source]] = {}

        root = repo_root / PANEL_ROOT
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".ts", ".tsx"):
                continue
            if path.name.endswith((".test.ts", ".test.tsx", ".d.ts")):
                continue
            if "test" in path.relative_to(root).parts:
                continue
            raw = blank(path.read_text(encoding="utf-8"), strings=False)
            src = Source(
                rel=path.relative_to(root).as_posix(),
                raw=raw,
                code=blank(raw, strings=True),
            )
            src.brackets = bracket_map(src.code)
            self.sources.append(src)
            self.by_stem[src.stem] = src

        for src in self.sources:
            self._index_imports(src)
            self._index_types(src)
            self._index_functions(src)
            for name in src.types:
                self.types_by_name.setdefault(name, []).append(src)
            for name in src.functions:
                self.fns_by_name.setdefault(name, []).append(src)

    # -- declarations ------------------------------------------------------

    def _index_imports(self, src: Source) -> None:
        for m in IMPORT.finditer(src.raw):
            for spec in split_str(m.group(1), ","):
                spec = re.sub(r"^type\s+", "", spec.strip())
                local = spec.split(" as ")[-1].strip()
                if IDENT.match(local):
                    src.imports[local] = m.group(2)

    def _index_types(self, src: Source) -> None:
        for m in TYPE_DECL.finditer(src.code):
            name = m.group(2) or m.group(4)
            t = src.types.setdefault(name, TsType(name=name))
            if m.group(1):  # interface
                open_at = src.code.find("{", m.end())
                if open_at < 0 or open_at not in src.brackets:
                    t.readable = False
                    continue
                head = src.raw[m.end():open_at]
                if "extends" in head:
                    t.parents.extend(split_str(head.split("extends", 1)[1], ",", angle=True))
                self._fold(src.raw[open_at: src.brackets[open_at] + 1], t)
            else:  # type alias
                a, b = self._value_span(src, m.end())
                self._fold(src.raw[a:b], t)

    def _value_span(self, src: Source, start: int) -> tuple[int, int]:
        """From `start`, the expression that follows: a `{...}` block, or up to `;`."""
        i = start
        while i < len(src.code) and src.code[i].isspace():
            i += 1
        if i < len(src.code) and src.code[i] == "{" and i in src.brackets:
            return i, src.brackets[i] + 1
        depth = 0
        j = i
        while j < len(src.code):
            c = src.code[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == ";" and depth == 0:
                break
            j += 1
        return i, j

    def _fold(self, expr: str, t: TsType) -> None:
        """Fold one type expression into a declared type."""
        if len(split_str(expr, "|", angle=True)) > 1:
            t.readable = False  # a union has no single key set
            return
        for term in split_str(expr, "&", angle=True):
            if term.startswith("{"):
                members = self._members(term)
                if members is None:
                    t.readable = False
                else:
                    t.members.update(members)
            elif term:
                t.parents.append(term)

    def _members(self, block: str) -> dict[str, str] | None:
        """Members of a `{ ... }` type body, or None when one is not a plain field."""
        block = block.strip()
        if not (block.startswith("{") and block.endswith("}")):
            return None
        out: dict[str, str] = {}
        for part in split_str(block[1:-1], ",;", angle=True):
            m = MEMBER.match(part)
            if not m:
                return None
            out[m.group(1) or m.group(2) or m.group(3)] = m.group(4).strip()
        return out

    def _index_functions(self, src: Source) -> None:
        for m in FN_DECL.finditer(src.code):
            open_at = src.code.find("(", m.end())
            if open_at not in src.brackets:
                continue
            past = after_params(src.code, src.brackets[open_at], "{")
            if past is None:
                continue
            ret, brace = past
            if brace not in src.brackets:
                continue
            src.functions.setdefault(
                m.group(1),
                FnDecl(ret=ret, body=(brace, src.brackets[brace] + 1), body_is_expr=False),
            )
        for m in CONST_FN.finditer(src.code):
            open_at = src.code.find("(", m.end())
            if open_at not in src.brackets:
                continue
            past = after_params(src.code, src.brackets[open_at], "=>")
            if past is None:
                continue
            ret, arrow = past
            a, b, is_expr = body_span(src, arrow)
            src.functions.setdefault(
                m.group(1), FnDecl(ret=ret, body=(a, b), body_is_expr=is_expr)
            )

    # -- module-scoped lookup ---------------------------------------------

    def module_of(self, src: Source, spec: str) -> Source | None:
        """The panel file an import specifier points at, or None when it leaves the panel."""
        if spec.startswith("@/"):
            base = spec[2:]
        elif spec.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(src.rel), spec))
        else:
            return None  # a package, not one of ours
        return self.by_stem.get(base) or self.by_stem.get(base + "/index")

    def _lookup(self, src: Source, name: str, table: str, by_name: dict[str, list[Source]]):
        """This file's declaration, else the file it imports the name from, else a unique one."""
        own = getattr(src, table).get(name)
        if own is not None:
            return src, own
        spec = src.imports.get(name)
        if spec is not None:
            other = self.module_of(src, spec)
            if other is None:
                return None  # imported from a package: nothing of ours to read
            found = getattr(other, table).get(name)
            return (other, found) if found is not None else None
        hits = by_name.get(name) or []
        if len(hits) != 1:
            return None  # undeclared, or declared in several files with no import to pick one
        return hits[0], getattr(hits[0], table)[name]

    def lookup_type(self, src: Source, name: str) -> tuple[Source, TsType] | None:
        return self._lookup(src, name, "types", self.types_by_name)

    def lookup_fn(self, src: Source, name: str) -> tuple[Source, FnDecl] | None:
        return self._lookup(src, name, "functions", self.fns_by_name)

    # -- types -------------------------------------------------------------

    def resolve_type(self, typed: Typed | None, seen: frozenset[tuple[str, str]] = frozenset()):
        """Every key a type expression carries, or None when it cannot be read."""
        if typed is None:
            return None
        src, expr = typed
        expr = expr.strip().rstrip(";").strip()
        if not expr or (src.rel, expr) in seen or len(seen) > MAX_DEPTH:
            return None
        seen = seen | {(src.rel, expr)}
        if len(split_str(expr, "|", angle=True)) > 1:
            return None  # a union has no single key set
        terms = split_str(expr, "&", angle=True)
        if len(terms) > 1:
            out: set[str] = set()
            for term in terms:
                got = self.resolve_type((src, term), seen)
                if got is None:
                    return None
                out |= got
            return out
        if expr.startswith("{"):
            members = self._members(expr)
            return None if members is None else set(members)
        util = UTILITY.match(expr)
        if util:
            args = split_str(util.group(2), ",", angle=True)
            if not args:
                return None
            base = self.resolve_type((src, args[0]), seen)
            if base is None:
                return None
            if util.group(1) in ("Partial", "Required", "Readonly", "NonNullable"):
                return base
            if len(args) < 2:
                return None
            listed = {
                lit.strip().strip("'\"")
                for lit in split_str(args[1], "|")
                if lit.strip()[:1] in ("'", '"')
            }
            if not listed:
                return None
            return (base & listed) if util.group(1) == "Pick" else (base - listed)
        if not IDENT.match(expr):
            return None
        found = self.lookup_type(src, expr)
        if found is None or not found[1].readable:
            return None
        home, t = found
        out = set(t.members)
        for parent in t.parents:
            got = self.resolve_type((home, parent), seen)
            if got is None:
                return None
            out |= got
        return out

    def member_type(
        self, typed: Typed, name: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Typed | None:
        """The declared type of one member of a type expression, in its own file's context."""
        src, expr = typed
        expr = expr.strip().rstrip(";").strip()
        if not expr or (src.rel, expr) in seen or len(seen) > MAX_DEPTH:
            return None
        seen = seen | {(src.rel, expr)}
        if expr.startswith("{"):
            members = self._members(expr)
            if members is None or name not in members:
                return None
            return src, members[name]
        terms = split_str(expr, "&", angle=True)
        if len(terms) > 1:
            for term in terms:
                got = self.member_type((src, term), name, seen)
                if got:
                    return got
            return None
        if not IDENT.match(expr):
            return None
        found = self.lookup_type(src, expr)
        if found is None or not found[1].readable:
            return None
        home, t = found
        if name in t.members:
            return home, t.members[name]
        for parent in t.parents:
            got = self.member_type((home, parent), name, seen)
            if got:
                return got
        return None

    # -- expressions -------------------------------------------------------

    def object_keys(self, src: Source, a: int, b: int, depth: int):
        """Keys of the object literal in `src.raw[a:b]`, spreads resolved."""
        if src.code[a] != "{" or src.brackets.get(a) != b - 1:
            return None
        keys: set[str] = set()
        for x, y in split_spans(src.code, a + 1, b - 1, ","):
            part = src.raw[x:y]
            stripped = part.strip()
            if stripped.startswith("..."):
                spread = self.resolve_expr(src, x + part.index("...") + 3, y, depth + 1)
                if spread is None:
                    return None
                keys |= spread
                continue
            m = KEY.match(stripped)
            if not m:
                return None
            keys.add(m.group(1) or m.group(2) or m.group(3))
        return keys

    def enclosing_arrows(self, src: Source, pos: int) -> list[int]:
        """Every arrow whose body contains `pos`, innermost first."""
        found: list[tuple[int, int]] = []
        for m in re.finditer(r"=>", src.code):
            a, b, _is_expr = body_span(src, m.start())
            if a <= pos < b:
                found.append((a, m.start()))
        found.sort(reverse=True)
        return [arrow for _a, arrow in found]

    def _params_span(self, src: Source, arrow_at: int) -> tuple[int, int] | None:
        """The `( ... )` of the arrow at `arrow_at`, stepping over a return annotation."""
        i = arrow_at - 1
        while i >= 0 and src.code[i].isspace():
            i -= 1
        if i < 0:
            return None
        if src.code[i] != ")":
            depth = 0
            j = i
            while j >= 0:
                c = src.code[j]
                if c in ")]}>":
                    depth += 1
                elif c in "([{<":
                    if depth == 0:
                        return None
                    depth -= 1
                elif c == ":" and depth == 0:
                    break
                j -= 1
            if j < 0 or src.code[j] != ":":
                return None
            j -= 1
            while j >= 0 and src.code[j].isspace():
                j -= 1
            if j < 0 or src.code[j] != ")":
                return None
            i = j
        open_at = src.brackets.get(i)
        return None if open_at is None else (open_at, i)

    def param_type(self, src: Source, arrow_at: int, name: str) -> Typed | None:
        """The declared type of parameter `name` of the arrow at `arrow_at`."""
        span = self._params_span(src, arrow_at)
        if span is None:
            return None
        open_at, close = span
        for x, y in split_spans(src.code, open_at + 1, close, ","):
            param = src.raw[x:y].strip()
            cut = split_spans(blank(param, strings=True), 0, len(param), ":")
            if len(cut) < 2:
                continue
            head = param[: cut[0][1]].strip()
            annotation = param[cut[1][0]:].strip()
            if not annotation:
                continue
            if head == name:
                return src, annotation
            if not head.startswith("{"):
                continue
            bound = [p.split(":")[0].strip() for p in split_str(head.strip("{}"), ",")]
            if name in bound:
                return self.member_type((src, annotation), name)
            if "..." + name in bound:
                # `({ id, ...payload }: T)` -- the rest carries every member but the ones
                # named beside it, which is a key set rather than a type this file declares.
                whole = self.resolve_type((src, annotation))
                if whole is None:
                    return None
                rest = whole - {b for b in bound if not b.startswith("...")}
                members = "; ".join(f"{k}: unknown" for k in sorted(rest))
                return src, f"{{{members}}}"
        return None

    def declares_param(self, src: Source, arrow_at: int, name: str) -> bool:
        """Does the arrow bind `name` at all, annotated or not?"""
        span = self._params_span(src, arrow_at)
        if span is None:
            return False
        return bool(re.search(rf"\b{re.escape(name)}\b", src.raw[span[0]: span[1]]))

    def mutation_vars_type(self, src: Source, arrow_at: int) -> Typed | None:
        """`useMutation<Result, Error, Vars>` -- the `Vars` the `mutationFn` is handed."""
        for m in re.finditer(r"\buseMutation\s*(<[^(]*?>)?\s*\(", src.code):
            open_at = m.end() - 1
            close = src.brackets.get(open_at)
            if close is None or not open_at < arrow_at < close:
                continue
            if not m.group(1):
                return None
            args = split_str(src.raw[m.start(1) + 1: m.end(1) - 1], ",", angle=True)
            return (src, args[2]) if len(args) >= 3 else None
        return None

    def binding(self, src: Source, pos: int, name: str, depth: int):
        """The key set of an identifier, bound the way the language binds it."""
        for arrow in self.enclosing_arrows(src, pos):
            annotation = self.param_type(src, arrow, name)
            if annotation is not None:
                return self.resolve_type(annotation)
            if self.declares_param(src, arrow, name):
                return self.resolve_type(self.mutation_vars_type(src, arrow))
        best: tuple[int, int] | None = None
        pat = rf"\b(?:const|let|var)\s+{re.escape(name)}\b"
        for m in re.finditer(pat, src.code[:pos]):
            block = src.enclosing_block(m.start())
            if block is not None and not block[0] < pos < block[1]:
                continue
            if best is None or m.start() > best[0]:
                best = (m.start(), m.end())
        if best is None:
            return None
        i = best[1]
        while i < len(src.code) and src.code[i].isspace():
            i += 1
        if i < len(src.code) and src.code[i] == ":":
            cut = split_spans(src.code, i + 1, len(src.code), "=")
            return self.resolve_type((src, src.raw[cut[0][0]: cut[0][1]])) if cut else None
        if i >= len(src.code) or src.code[i] != "=":
            return None
        a, b = self._value_span(src, i + 1)
        return self.resolve_expr(src, a, b, depth + 1)

    def call_keys(self, src: Source, name: str, depth: int):
        """The key set a named function returns."""
        found = self.lookup_fn(src, name)
        if found is None:
            return None
        home, decl = found
        if decl.ret:
            resolved = self.resolve_type((home, decl.ret))
            if resolved is not None:
                return resolved
        a, b = decl.body
        if decl.body_is_expr:
            return self.resolve_expr(home, a, b, depth + 1)
        returns = [
            a + m.start()
            for m in re.finditer(r"\breturn\b", home.code[a:b])
            if home.enclosing_block(a + m.start()) == (a, b - 1)
        ]
        if len(returns) != 1:
            return None  # several returns: the shape is a branch, not a value
        x, y = self._value_span(home, returns[0] + len("return"))
        return self.resolve_expr(home, x, y, depth + 1)

    def resolve_expr(self, src: Source, a: int, b: int, depth: int = 0):
        """The key set an expression puts on the wire, or None when it cannot be read."""
        if depth > MAX_DEPTH:
            return None
        while a < b and src.code[a].isspace():
            a += 1
        while b > a and src.code[b - 1].isspace():
            b -= 1
        text = src.raw[a:b]
        if not text.strip():
            return None
        if src.code[a] == "{":
            return self.object_keys(src, a, b, depth)
        if IDENT.match(text):
            return self.binding(src, a, text, depth)
        head = CALL_HEAD.match(text)
        if head and text.endswith(")"):
            return self.call_keys(src, head.group(1), depth)
        return None


# --- the comparison --------------------------------------------------------


@dataclass
class PanelCall:
    """One `api.post` / `api.put` in the panel, with whatever could be read of its body."""

    file: str
    line: int
    method: str
    url_expr: str
    path: str | None
    keys: tuple[str, ...] | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.file, self.method, self.url_expr)


def route_path(url_expr: str) -> str | None:
    """`` `/services/${id}` `` -> `/api/services/{}`, or None when it is not a literal."""
    u = url_expr.strip()
    if len(u) < 2 or u[0] not in "'\"`" or u[-1] != u[0]:
        return None
    u = re.sub(r"\$\{[^}]*\}", "{}", u[1:-1])
    if not u.startswith("/") or "${" in u:
        return None
    return "/api" + u


def collect_panel_calls(index: PanelIndex) -> list[PanelCall]:
    """Every body-carrying `api.post` / `api.put` the panel makes."""
    calls: list[PanelCall] = []
    for src in index.sources:
        for m in CALL.finditer(src.code):
            open_at = m.end() - 1
            close = src.brackets.get(open_at)
            if close is None:
                continue
            args = split_spans(src.code, open_at + 1, close, ",")
            if len(args) < 2:
                continue  # no body: nothing is sent, so nothing can be dropped
            url_expr = src.raw[args[0][0]: args[0][1]].strip()
            keys = index.resolve_expr(src, args[1][0], args[1][1])
            calls.append(
                PanelCall(
                    file=src.rel,
                    line=src.line_of(m.start()),
                    method=m.group(1).upper(),
                    url_expr=url_expr,
                    path=route_path(url_expr),
                    keys=None if keys is None else tuple(sorted(keys)),
                )
            )
    return calls


def normalise(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def compare(
    calls: list[PanelCall],
    models: dict[str, ApiModel],
    routes: dict[tuple[str, str], ApiRoute],
):
    """Give every call a verdict: compared, uncompared, or no model to compare against."""
    by_path: dict[tuple[str, str], ApiRoute] = {}
    for (method, path), route in routes.items():
        by_path.setdefault((method.upper(), normalise(path)), route)

    findings: list[tuple[tuple[str, str, str], str]] = []
    uncompared: list[tuple[PanelCall, str]] = []
    compared = unmodelled = 0

    for call in calls:
        route = by_path.get((call.method, normalise(call.path))) if call.path else None
        if route is None:
            uncompared.append(
                (call, "route not a literal" if call.path is None else "no such route")
            )
            continue
        if not route.model:
            if call.keys is None:
                uncompared.append((call, "body unreadable"))
            else:
                unmodelled += 1  # the route reads a free-form dict; nothing to compare
            continue
        if call.keys is None:
            uncompared.append((call, "body unreadable"))
            continue
        model = models.get(route.model)
        if model is None:
            uncompared.append((call, f"model {route.model} not collected"))
            continue
        compared += 1
        for name in call.keys:
            if name not in model.fields:
                findings.append(
                    (
                        (call.file, call.method, name),
                        f"{call.file}:{call.line} {call.method} {call.path} sends "
                        f"`{name}`, which {route.model} does not declare",
                    )
                )
    stats = {
        "bodies": len(calls),
        "compared": compared,
        "unmodelled": unmodelled,
        "uncompared": len(uncompared),
    }
    return findings, uncompared, stats


def run(repo_root: Path):
    """Collect both sides and compare them. Shared with the test that mirrors this gate."""
    models, routes = collect_api_contracts(repo_root)
    index = PanelIndex(repo_root)
    calls = collect_panel_calls(index)
    return (calls, *compare(calls, models, routes))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare panel request bodies to the models")
    parser.add_argument("--report", action="store_true", help="list every call and what it sends")
    options = parser.parse_args(argv)

    calls, findings, uncompared, stats = run(Path(__file__).resolve().parent.parent)
    divergent = [msg for key, msg in findings if key not in ALLOWED_PANEL_KEYS]

    print(f"PANEL_BODY_COUNT {stats['bodies']}")
    print(f"PANEL_COMPARED_COUNT {stats['compared']}")
    print(f"PANEL_UNMODELLED_COUNT {stats['unmodelled']}")
    print(f"PANEL_UNCOMPARED_COUNT {stats['uncompared']}")
    print(f"PANEL_EXEMPT_COUNT {len(findings) - len(divergent)}")
    print(f"PANEL_DIVERGENCE_COUNT {len(divergent)}")

    if options.report:
        for call in sorted(calls, key=lambda c: (c.file, c.line)):
            shape = "UNREADABLE" if call.keys is None else ",".join(call.keys)
            print(f"  {call.file}:{call.line} {call.method} {call.url_expr} -> {shape}")
        return 0

    observed = {call.key for call, _why in uncompared}
    unlisted = sorted(
        {(call.key, why) for call, why in uncompared if call.key not in ALLOWED_UNCOMPARED}
    )
    if unlisted:
        print("Panel calls this gate cannot compare (make them readable, or list them):")
        for (f, method, url), why in unlisted:
            print(f"  {f}: {method} {url} -- {why}")
        return 1

    if divergent:
        print("Panel keys the model of the route does not declare:")
        for msg in sorted(divergent):
            print(f"  {msg}")
        return 1

    stale = sorted(set(ALLOWED_UNCOMPARED) - observed)
    if stale:
        print("Uncompared-call exemptions that no longer match anything (remove them):")
        for f, method, url in stale:
            print(f"  {f}: {method} {url}")
        return 1

    stale_keys = sorted(set(ALLOWED_PANEL_KEYS) - {key for key, _ in findings})
    if stale_keys:
        print("Panel key exemptions that no longer match anything (remove them):")
        for f, method, name in stale_keys:
            print(f"  {f}: {method} {name}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
