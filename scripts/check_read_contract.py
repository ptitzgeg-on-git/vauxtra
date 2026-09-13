"""Two declared types reading the same bytes have to say the same thing.

`scripts/check_panel_contract.py` guards the panel's write side: every `api.post` and
`api.put` body against the Pydantic model of the route it posts to. This is the read side,
which had no gate at all.

Nothing checks a GET answer anywhere. There is no `response_model=` in `app/` -- every GET
route returns a bare dict or list assembled in Python -- and `api.get<T>(url)` in
`frontend/src/api/client.ts` ends in `return _axios.get<T>(url, config) as unknown as
Promise<T>`: a cast with no validation behind it. So `T` is a claim about bytes that nobody
verifies, and a wrong `T` is not a type error anywhere. It is an `undefined` at runtime, in
a branch, on somebody's dashboard.

Comparing every `T` against the real answer would mean running the backend, which this job
does not do. But one thing is decidable by reading alone: when two places read the SAME
bytes -- the same route, or the same react-query cache key -- and each names its own `T`,
at most one of them can be right. That is what this compares, in three rules.

  R1  One route, one shape. Every `api.get<T>(url)` on a URL, and every `useQuery<T>` on a
      `queryKey`, is resolved to a structure and compared with the others on the same URL
      or key. A key both sides declare has to carry the same type and the same optionality.
      A key only one side declares is a narrowing -- reading fewer fields than arrive is not
      a contradiction -- and is counted, not failed.

  R2  No shadowed name. `frontend/src/types/api.ts` is where a shape shared by more than one
      panel goes. A module that declares a name that file already owns has made a second
      declaration nobody has to keep in step with the first, and the stale one is the copy
      sitting where a reader looks first.

  R3  One answer, one cache entry. A `useQuery` or `queryOptions` whose `queryFn` reads a
      URL is paired with that URL verbatim -- `/logs?per_page=8` and `/logs?page=${page}`
      are one route but two questions, and two questions belong in two entries. Readers
      asking byte-for-byte the same thing have to share one key, because a key is a
      lifetime: two keys over one answer means every place that invalidates has to remember
      both names. A key hoisted into a `const` is resolved to the array it was declared
      with: a shared hook naming its key once is the arrangement this rule asks for, and
      reading only a literal `queryKey` would have left it blind to its own remedy.

Each rule was written after it had already caught something, and every count below is what
this file printed on the commit before that rule's fix.

R2 found five, each a second declaration of a name `types/api.ts` already owned: a
`DockerContainer` claiming a `ports` array the handler has never sent while missing eight
keys it does send; an `AuthStatus` making `setup_required` optional where the route has
always sent a bool; a `Provider` holding five keys of eleven; a `ProviderValidationResult`
with neither of the two fields the route has sent since diagnostics were translated; and a
`GuidedStep` that collided with the shared name while meaning something narrower.

R1 found fourteen. Twelve are one payload: `/certificates/expiry` had three spellings, one
per reader, and two of them disagreed about whether six keys -- `expires_on`,
`days_remaining`, `expired`, `expiring_soon`, `provider_id`, `provider_name` -- are optional
or required. The remaining two are the `AuthStatus` shadow seen from the other side, once
under its route and once under its cache key, which is the point: a name collision and a
disagreement about bytes are the same defect described twice.

R3 found one. `/auth/me` says who the caller is, and six components asked it: the boot gate,
the layout banner, the sidebar and the dashboard under `['auth-status']`, the two settings
tabs under `['auth-me']`. Of the eight places that invalidate once the answer could have
changed, five named `['auth-status']` alone. The settings copy also declared no `staleTime`,
which means zero, so opening Settings drew a skeleton and spent a round trip re-fetching an
answer the shell already held. `/logs` is read under three keys and is not a finding: those
three ask three different questions of one route, which is what a key is for.

The resolver is `check_panel_contract`'s, unchanged, for the reason that file argues at
length: a resolver that guesses is worse than one that refuses. Every shape it cannot read
-- `unknown`, an index signature, a union of object types -- is skipped rather than
approximated, and a skipped group has to say in `REASONS` below why nobody can compare it.
A reason for a group that compares cleanly is dead and fails the build, the same way a dead
exemption fails in the write-side gate.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_panel_contract import (  # noqa: E402
    IDENT,
    UTILITY,
    PanelIndex,
    Source,
    split_spans,
    split_str,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_TYPES = "types/api.ts"

GET_CALL = re.compile(r"\bapi\.get\s*<")
USE_QUERY = re.compile(r"\buseQuery\s*<")
#: R3 does not care what `T` is, only which URL was asked for, so it matches an
#: `api.get` written with or without a type argument.
ANY_GET = re.compile(r"\bapi\.get\s*[<(]")
#: R3 reads the pairing wherever the key and the URL are written together: a `useQuery`
#: with or without a type argument, and a `queryOptions` whose object a hook hands to
#: `useQuery` later. Requiring the type argument would have left the rule blind to its own
#: fix -- `useAuthStatus` spells the key and the URL inside `queryOptions` and nowhere else.
QUERY_BLOCK = re.compile(r"\b(?:useQuery|queryOptions)\s*[<(]")
ARRAY_OF = re.compile(r"^(?:Array|ReadonlyArray)\s*<(.*)>$", re.S)
QUOTED = re.compile(r"^['\"](.*)['\"]$", re.S)

# The shapes with no name of their own that still say something definite. `any`, `unknown`
# and `object` are deliberately not here: those three say nothing, which is what makes them
# impossible to contradict.
PRIMITIVES = frozenset(
    {"string", "number", "boolean", "bigint", "null", "undefined", "void", "never"}
)


# Groups whose observers cannot be compared, and why. Keyed the way the report names them.
# A group listed here that turns out to compare cleanly fails the build: an excuse nobody
# needs any more is an excuse nobody rereads.
REASONS = {
    "route:/settings": (
        "`GET /api/settings` answers the settings table as a flat string map, and that is "
        "genuinely what it is: the keys are rows, not fields. `AppSettings` names the ones "
        "the General tab writes; `Record<string, string>` is the honest shape for a reader "
        "that only looks one key up. An index signature declares no key, so there is "
        "nothing on that side to disagree with."
    ),
    "key:['settings']": (
        "The same two readers as `route:/settings`, reached through the cache entry rather "
        "than the endpoint. `AppSettings` and `Record<string, string>` are both right about "
        "these bytes for the same reason they are on the route: the settings table is rows, "
        "not fields, and the reader that looks one key up has nothing to state about the "
        "rest."
    ),
    "route:/providers/health": (
        "The Dashboard reads this as `unknown` on purpose -- it only counts the entries "
        "that came back -- and `unknown` makes no claim for anything to contradict. The "
        "Providers page names `ProvidersHealthMap`, which is `Record<string, "
        "ProviderHealthSummary>`; an index signature declares no key either, so there is "
        "nothing on that side to compare with."
    ),
    "key:['providers-health']": (
        "The same two readers as `route:/providers/health`, and one of them is wider here "
        "than inside its own query function: the `useQuery` says `ProvidersHealthMap | { "
        "items?: ProvidersHealthMap }` while the `api.get` it wraps says the map alone. "
        "The union is deliberate -- the route answered a wrapped object for one version "
        "and `unwrapHealthMap` still takes either spelling -- and a union of object types "
        "is not a shape this resolver reads."
    ),
}


def unwrap(expr: str) -> tuple[str, int]:
    """`T[]` and `Array<T>` down to `T`, and how many levels came off."""
    depth = 0
    while True:
        expr = expr.strip()
        if expr.startswith("(") and expr.endswith(")"):
            expr = expr[1:-1]
            continue
        m = ARRAY_OF.match(expr)
        if m and len(split_str(m.group(1), ",", angle=True)) == 1:
            expr, depth = m.group(1), depth + 1
            continue
        if expr.endswith("[]") and len(split_str(expr[:-2], "|", angle=True)) == 1:
            expr, depth = expr[:-2], depth + 1
            continue
        return expr, depth


def shape(ix: PanelIndex, src: Source, expr: str, seen=frozenset(), depth: int = 0):
    """A type expression as a structure two of them can be compared by.

    `('array', inner)` | `('object', {key: (optional, inner)})` | `('opaque', text)` | None.

    The two failure values are different on purpose. `('opaque', text)` is a shape that
    makes no claim about any key -- an index signature, a union, `unknown` -- so nothing
    it is paired with can contradict it. `None` is a shape this resolver could not read,
    which is not the same as one with nothing in it. Both end a comparison; only the
    second is an admission.
    """
    expr = " ".join(expr.strip().rstrip(";").split())
    if not expr or (src.rel, expr) in seen or len(seen) > 6 or depth > 4:
        return None
    seen = seen | {(src.rel, expr)}
    inner, levels = unwrap(expr)
    if levels:
        got = shape(ix, src, inner, seen, depth)
        if got is None:
            return None
        for _ in range(levels):
            got = ("array", got)
        return got
    if len(split_str(expr, "|", angle=True)) > 1:
        return ("opaque", expr)
    terms = split_str(expr, "&", angle=True)
    if len(terms) > 1:
        members: dict = {}
        for term in terms:
            got = shape(ix, src, term, seen, depth)
            if got is None or got[0] != "object":
                return None
            members.update(got[1])
        return ("object", members)
    if expr.startswith("{"):
        raw = ix._members(expr)
        if raw is None:
            return None
        return ("object", {
            k: (opt, shape(ix, src, t, seen, depth + 1)) for k, (t, opt) in raw.items()
        })
    util = UTILITY.match(expr)
    if util:
        #: `Template` is `Omit<TemplateIn, 'websocket'>` with a `websocket` of its own
        #: put back, and refusing that would have meant an exemption for a shape the
        #: panel states exactly. `Layout.tsx` used to read `/auth/me` as
        #: `Pick<AuthStatus, 'auth_mode'>` and goes through the shared hook now, so no
        #: reader spells a `Pick` today -- the branch stays because the next one will.
        args = split_str(util.group(2), ",", angle=True)
        inner = shape(ix, src, args[0], seen, depth)
        if inner is None or inner[0] != "object":
            return None
        members = dict(inner[1])
        kind = util.group(1)
        if kind == "Partial":
            members = {k: (True, v) for k, (_, v) in members.items()}
        elif kind == "Required":
            members = {k: (False, v) for k, (_, v) in members.items()}
        elif kind in ("Pick", "Omit"):
            keep: set[str] = set()
            for part in split_str(args[1], "|", angle=True) if len(args) == 2 else []:
                quoted = QUOTED.match(part.strip())
                if quoted is None:
                    return None  # a computed key selector: not ours to guess at
                keep.add(quoted.group(1))
            if not keep:
                return None
            members = {k: v for k, v in members.items() if (k in keep) == (kind == "Pick")}
        return ("object", members)
    if not IDENT.match(expr):
        return ("opaque", expr)
    if expr in PRIMITIVES:
        #: A primitive is the one unnamed shape that still says something definite, and
        #: four readers of `/domains` say `string[]`. Calling that opaque would have made
        #: the gate ask for a written excuse to compare two identical declarations.
        return ("prim", expr)
    found = ix.lookup_type(src, expr)
    if found is None:
        #: `unknown`, `any`, and anything declared outside the panel. A name with no
        #: declaration here claims no key, which is exactly what opaque means.
        return ("opaque", expr)
    home, t = found
    if not t.readable:
        return None
    members = {}
    for parent in t.parents:
        got = shape(ix, home, parent, seen, depth)
        if got is None or got[0] != "object":
            return None
        members.update(got[1])
    for key, member in t.members.items():
        members[key] = (key in t.optional, shape(ix, home, member, seen, depth + 1))
    return ("object", members)


def compare(a, b, path: str = "", conflicts=None, narrowed=None):
    """Where two shapes for the same bytes disagree, and where one just reads less.

    A key both sides declare has to carry the same type and the same optionality: at most
    one of two different answers about the same bytes is right. A key only one side
    declares is a narrowing -- a caller that reads three fields out of nine is not
    contradicting the six it ignores -- so it is recorded separately and does not fail.
    """
    conflicts = [] if conflicts is None else conflicts
    narrowed = [] if narrowed is None else narrowed
    if a is None or b is None or a[0] == "opaque" or b[0] == "opaque":
        return conflicts, narrowed
    if a[0] == "prim" and b[0] == "prim":
        if a[1] != b[1]:
            conflicts.append(f"{path or '(root)'}: {a[1]} on one side, {b[1]} on the other")
        return conflicts, narrowed
    if a[0] != b[0]:
        conflicts.append(f"{path or '(root)'}: {a[0]} on one side, {b[0]} on the other")
        return conflicts, narrowed
    if a[0] == "array":
        return compare(a[1], b[1], path + "[]", conflicts, narrowed)
    for key in sorted(set(a[1]) | set(b[1])):
        here = f"{path}.{key}" if path else key
        if key not in a[1] or key not in b[1]:
            narrowed.append(here)
            continue
        if a[1][key][0] != b[1][key][0]:
            conflicts.append(f"{here}: optional on one side, required on the other")
        compare(a[1][key][1], b[1][key][1], here, conflicts, narrowed)
    return conflicts, narrowed


def comparable(a, b) -> bool:
    """Whether two shapes make claims that can contradict each other at all.

    Array levels are peeled together first: `Provider[]` against `ProviderItem[]` is a
    question about `Provider` against `ProviderItem`, and `Record<string, string>[]`
    against anything is still no question at all.
    """
    while a is not None and b is not None and a[0] == "array" and b[0] == "array":
        a, b = a[1], b[1]
    if a is None or b is None:
        return False
    if a[0] == "opaque" and b[0] == "opaque":
        #: Two readers writing the same opaque expression agree about it whether or not
        #: this file can read it, and asking for a written excuse to compare a declaration
        #: with itself is how an exemption table fills up with noise.
        return a[1] == b[1]
    return a[0] != "opaque" and b[0] != "opaque"


def type_arg(src: Source, at: int) -> tuple[str | None, int]:
    """The first type argument of the generic opening at `at`, and where it closes."""
    depth, i = 0, at
    while i < len(src.code):
        if src.code[i] == "<":
            depth += 1
        elif src.code[i] == ">":
            depth -= 1
            if depth == 0:
                whole = src.raw[at + 1:i].strip()
                args = split_str(whole, ",", angle=True)
                return (args[0] if args else None), i
        i += 1
    return None, at


def first_arg(src: Source, open_at: int) -> str | None:
    close = src.brackets.get(open_at)
    if close is None:
        return None
    spans = split_spans(src.code, open_at + 1, close, ",", angle=True)
    return src.raw[spans[0][0]:spans[0][1]].strip() if spans else None


def route_of(url: str) -> str:
    """`/services/${id}?full=1` and `/services/${sid}` are one route and one answer."""
    plain = url.strip().strip("`'\"")
    return re.sub(r"\$\{[^}]*\}", "*", plain).split("?")[0].rstrip("/") or "/"


def const_array(src: Source, name: str) -> str | None:
    """The array literal a module-level `const` of that name is declared with.

    A key hoisted into a named constant is still a literal key: `useAuthStatus` declares
    `AUTH_STATUS_KEY = ['auth-status'] as const` and writes `queryKey: AUTH_STATUS_KEY`
    four lines below. Reading only the inline spelling would have left the rule blind to
    the one hook that exists because the rule caught this URL under two keys.
    """
    if IDENT.match(name) is None:
        return None
    for m in re.finditer(rf"\b(?:const|let|var)\s+{re.escape(name)}\b", src.code):
        eq = src.code.find("=", m.end())
        stop = src.code.find(";", m.end())
        if eq < 0 or 0 <= stop < eq:
            continue
        i = eq + 1
        while i < len(src.code) and src.code[i].isspace():
            i += 1
        close = src.brackets.get(i)
        if i < len(src.code) and src.code[i] == "[" and close is not None:
            #: `as const` sits outside the brackets, so slicing the pair drops it.
            return " ".join(src.raw[i : close + 1].split())
    return None


def key_of(src: Source, open_at: int) -> str | None:
    """The `queryKey` of the options object opening at `open_at`, normalised.

    react-query caches by the serialised key, so two files writing `['service', id]` share
    a cache entry for each id and have to agree about it. A segment that is not a literal
    is written `*` for the same reason a path parameter is: which one it is does not change
    whose bytes land in the entry.
    """
    close = src.brackets.get(open_at)
    if close is None:
        return None
    for a, b in split_spans(src.code, open_at + 1, close, ",", angle=True):
        field = src.raw[a:b].lstrip()
        if not field.startswith("queryKey"):
            continue
        value = " ".join(field.split(":", 1)[1].split())
        if not (value.startswith("[") and value.endswith("]")):
            resolved = const_array(src, value)
            if resolved is None:
                return None
            value = resolved
        parts = []
        for part in split_str(value[1:-1], ",", angle=True):
            m = QUOTED.match(part)
            parts.append(f"'{m.group(1)}'" if m else "*")
        if not parts or not parts[0].startswith("'"):
            #: A key whose first segment is a variable names a namespace this file cannot
            #: read, and `[kind]` in one panel is not `[other]` in another just because
            #: both come out as `[*]`. Grouping those would invent a disagreement.
            return None
        return "[" + ", ".join(parts) + "]"
    return None


@dataclass
class Observer:
    """One place that declares what a GET answer looks like."""

    src: Source
    line: int
    expr: str

    @property
    def at(self) -> str:
        return f"{self.src.rel}:{self.line}"


def collect(ix: PanelIndex) -> dict[str, list[Observer]]:
    """Every `api.get<T>(url)` and `useQuery<T>({queryKey})`, grouped by the bytes it reads.

    A site usually shows up twice -- `useQuery<Provider[]>({queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers')})` is one read declared in two places
    -- and both groupings are kept. They catch different mistakes: the route groups two
    files reading one endpoint, the key groups two files sharing one cache entry, and a
    panel can get either of those wrong without the other.
    """
    routes: dict[str, list[Observer]] = {}
    keys: dict[str, list[Observer]] = {}
    for src in ix.sources:
        for m in GET_CALL.finditer(src.code):
            expr, end = type_arg(src, m.end() - 1)
            if expr is None:
                continue
            open_at = src.code.find("(", end)
            url = first_arg(src, open_at) if open_at >= 0 else None
            if url is not None:
                routes.setdefault(route_of(url), []).append(
                    Observer(src, src.line_of(m.start()), expr)
                )
        for m in USE_QUERY.finditer(src.code):
            expr, end = type_arg(src, m.end() - 1)
            if expr is None:
                continue
            open_at = src.code.find("{", end)
            key = key_of(src, open_at) if open_at >= 0 else None
            if key is not None:
                keys.setdefault(key, []).append(Observer(src, src.line_of(m.start()), expr))
    groups = {f"route:{name}": obs for name, obs in routes.items()}
    groups.update({f"key:{name}": obs for name, obs in keys.items()})
    return groups


def shadowed(ix: PanelIndex) -> list[tuple[str, str]]:
    """Names a panel module declares that `types/api.ts` already declares.

    Two declarations of one answer is two things to keep in step, and the one that drifts
    is the copy sitting where a reader looks first. Every defect this rule was written
    from was of that kind: the local copy was the stale one, every time.
    """
    shared = ix.by_stem[SHARED_TYPES.rsplit(".", 1)[0]]
    owned = set(shared.types)
    return [
        (src.rel, name)
        for src in ix.sources
        if src.rel != SHARED_TYPES
        for name in sorted(set(src.types) & owned)
    ]

def url_of(src: Source, open_at: int) -> str | None:
    """The URL the `queryFn` of the options object opening at `open_at` reads, verbatim.

    Verbatim, not routed: `/logs?per_page=8` and `/logs?page=${page}` are one route and two
    different questions, and two questions belong in two cache entries. Only readers asking
    byte-for-byte the same thing are answered by the same bytes.
    """
    close = src.brackets.get(open_at)
    if close is None:
        return None
    m = ANY_GET.search(src.code, open_at, close)
    if m is None:
        return None
    if src.code[m.end() - 1] == "<":
        _, end = type_arg(src, m.end() - 1)
        paren = src.code.find("(", end)
    else:
        paren = m.end() - 1
    if paren < 0 or paren >= close:
        return None
    url = first_arg(src, paren)
    return " ".join(url.split()) if url is not None else None


def options_at(src: Source, m: re.Match[str]) -> int | None:
    """Where the inline options object of the call `m` matched opens, if it has one."""
    if src.code[m.end() - 1] == "<":
        _, end = type_arg(src, m.end() - 1)
        paren = src.code.find("(", end)
        if paren < 0:
            return None
    else:
        paren = m.end() - 1
    i = paren + 1
    while i < len(src.code) and src.code[i].isspace():
        i += 1
    #: `useQuery(authStatusQuery)` passes an object built elsewhere. The pairing is read at
    #: the `queryOptions` that builds it, and reading it here too would count it twice.
    return i if i < len(src.code) and src.code[i] == "{" else None


def split_cache(ix: PanelIndex) -> tuple[int, list[str]]:
    """URLs read under more than one react-query key.

    A cache key is a lifetime. Two keys over one URL is two lifetimes for one answer, and
    then every place that invalidates after the answer changed has to remember both names,
    which is a thing the compiler cannot check and people do not do.
    """
    seen: dict[str, dict[str, list[str]]] = {}
    paired = 0
    for src in ix.sources:
        for m in QUERY_BLOCK.finditer(src.code):
            open_at = options_at(src, m)
            if open_at is None:
                continue
            key = key_of(src, open_at)
            url = url_of(src, open_at)
            if key is None or url is None:
                continue
            paired += 1
            seen.setdefault(url, {}).setdefault(key, []).append(
                f"{src.rel}:{src.line_of(m.start())}"
            )
    split = []
    for url in sorted(seen):
        if len(seen[url]) < 2:
            continue
        readers = " and ".join(
            f"{key} at {', '.join(sorted(at))}" for key, at in sorted(seen[url].items())
        )
        split.append(f"{url}: {readers}")
    return paired, split

def run(ix: PanelIndex):
    """Compare every group, and say what could not be compared."""
    groups = collect(ix)
    stats = {"gets": 0, "queries": 0, "groups": 0, "compared": 0, "skipped": 0}
    stats["gets"] = sum(len(o) for n, o in groups.items() if n.startswith("route:"))
    stats["queries"] = sum(len(o) for n, o in groups.items() if n.startswith("key:"))
    conflicts: list[str] = []
    narrowed: set[tuple[str, str]] = set()
    unexplained: list[str] = []
    used: set[str] = set()

    for name in sorted(groups):
        obs = groups[name]
        if len(obs) < 2:
            continue
        stats["groups"] += 1
        shapes = [(o, shape(ix, o.src, o.expr)) for o in obs]
        seen: set[str] = set()
        opaque = 0
        for i, (oa, sa) in enumerate(shapes):
            for ob, sb in shapes[i + 1:]:
                if not comparable(sa, sb):
                    stats["skipped"] += 1
                    opaque += 1
                    continue
                stats["compared"] += 1
                found, less = compare(sa, sb)
                narrowed |= {(name, key) for key in less}
                for line in found:
                    if line in seen:
                        continue  # one disagreement, however many pairs show it
                    seen.add(line)
                    conflicts.append(
                        f"{name}\n"
                        f"    {oa.at} declares {oa.expr}\n"
                        f"    {ob.at} declares {ob.expr}\n"
                        f"    {line}"
                    )
        if opaque:
            if name in REASONS:
                used.add(name)
            else:
                unexplained.append(
                    f"{name}: {opaque} pair(s) unreadable -- "
                    + ", ".join(sorted({f"{o.at} {o.expr}" for o, _ in shapes}))
                )
    return stats, conflicts, sorted(narrowed), unexplained, used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare what the panel says GET answers are")
    parser.add_argument("--report", action="store_true", help="list every group and its readers")
    options = parser.parse_args(argv)

    ix = PanelIndex(REPO_ROOT)
    stats, conflicts, narrowed, unexplained, used = run(ix)
    shadows = shadowed(ix)
    paired, split = split_cache(ix)

    print(f"READ_GET_COUNT {stats['gets']}")
    print(f"READ_QUERY_COUNT {stats['queries']}")
    print(f"READ_GROUP_COUNT {stats['groups']}")
    print(f"READ_COMPARED_COUNT {stats['compared']}")
    print(f"READ_SKIPPED_COUNT {stats['skipped']}")
    print(f"READ_NARROWING_COUNT {len(narrowed)}")
    print(f"READ_SHADOW_COUNT {len(shadows)}")
    print(f"READ_PAIRED_COUNT {paired}")
    print(f"READ_SPLIT_COUNT {len(split)}")
    print(f"READ_EXEMPT_COUNT {len(used)}")
    print(f"READ_DIVERGENCE_COUNT {len(conflicts)}")

    if options.report:
        groups = collect(ix)
        for name in sorted(groups):
            obs = groups[name]
            print(f"  {name}  ({len(obs)} reader(s))")
            for o in sorted(obs, key=lambda o: (o.src.rel, o.line)):
                got = shape(ix, o.src, o.expr)
                kind = "unreadable" if got is None else got[0]
                print(f"      {o.at}  {o.expr}  -> {kind}")
        for name, key in narrowed:
            print(f"  narrowed: {name} does not read {key} everywhere")
        return 0

    if conflicts:
        print("Two readers of the same bytes disagree about them:")
        for line in conflicts:
            print(f"  {line}")
        return 1

    if shadows:
        print(f"Names {SHARED_TYPES} already declares, declared a second time:")
        for rel, name in shadows:
            print(f"  {rel}: {name} -- import it from @/types/api instead")
        return 1

    if split:
        print("One answer, more than one cache entry:")
        for line in split:
            print(f"  {line}")
        return 1

    if unexplained:
        print("Groups this gate cannot compare (make them readable, or say why in REASONS):")
        for line in unexplained:
            print(f"  {line}")
        return 1

    dead = sorted(set(REASONS) - used)
    if dead:
        print("REASONS entries that no longer explain anything (remove them):")
        for name in dead:
            print(f"  {name}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
