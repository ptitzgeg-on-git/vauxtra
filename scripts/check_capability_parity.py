"""A fallback table that answers "no" is answering, and nobody checked what it said.

`GET /api/providers/types` serves a `capabilities` map per provider type, assembled from
`PROVIDER_TYPES` in `app/providers/factory.py`. Every screen that asks "can this integration
do X?" goes through `metaHasCapability` in `frontend/src/lib/providers.ts`, and that function
ends on a hand-written table, `CAPABILITY_FALLBACK`, whenever the map is absent: before the
query resolves, and for as long as it fails.

That file calls the table a floor, and a floor is the right idea. A type an older backend
never described still has to be grouped somehow. But the table names four of the six
capabilities in `ProviderCapability`, and nothing had ever compared it with the declarations
it stands in for, so it had drifted in five places at once:

  * `certificates` was missing entirely, so `npm` and `zoraxy`, the only two types that hold
    certificates, read as holding none.
  * `supports_auto_public_target` was missing entirely, so `cloudflare` and `desec` read as
    unable to resolve a public target on their own. That one is not cosmetic: the expose
    modal hides the auto-update control when it is false, and rewrites `public_target_mode`
    to `manual` with `auto_update_dns: false` in the payload it sends. Open a service saved
    with automatic DNS updates while this query is failing, change its port, press save, and
    the setting is gone. The control was never on the screen, and no error was either.
  * `proxy` listed `npm`, `traefik` and `zoraxy` but not `cloudflare_tunnel`, which declares
    it. The expose modal derives its tunnel list as `proxyProviders.filter(supports_tunnel)`,
    so the tunnel option left the form the moment this query failed.

A missing entry and a wrong entry are the same defect here, because this table cannot answer
"unknown": `CAPABILITY_FALLBACK[capability]?.has(type) ?? false` is `false` for a capability
it has never heard of, and `false` is a definite answer the screens act on.

So the rule is that the floor agrees with the backend about every type the backend ships,
for every capability it declares for that type. A type `PROVIDER_TYPES` does not ship is not
compared: that is the case the floor exists for, and it is left alone.

Two smaller rules come free from reading both files:

  R2  No stale type. A name in a fallback set that `PROVIDER_TYPES` no longer ships is a
      claim about an integration this build cannot create.
  R3  No unknown capability. One the backend declares and the `ProviderCapability` union in
      `frontend/src/types/api.ts` does not name is a capability no screen can ask for, and
      one R1 would otherwise skip in silence.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FACTORY = Path("app") / "providers" / "factory.py"
FALLBACK_TS = Path("frontend") / "src" / "lib" / "providers.ts"
API_TS = Path("frontend") / "src" / "types" / "api.ts"


def backend_capabilities(source: str) -> dict[str, dict[str, bool]] | None:
    """Read `{type: {capability: declared}}` off the `PROVIDER_TYPES` literal.

    Walked as a syntax tree rather than imported: this gate runs before anything is
    installed, and `factory.py` imports every provider module at module scope.
    """
    for node in ast.parse(source).body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if not any(isinstance(t, ast.Name) and t.id == "PROVIDER_TYPES" for t in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            return None
        out: dict[str, dict[str, bool]] = {}
        for key, entry in zip(node.value.keys, node.value.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(entry, ast.Dict):
                continue
            caps: dict[str, bool] = {}
            for field, field_value in zip(entry.keys, entry.values, strict=True):
                if not (isinstance(field, ast.Constant) and field.value == "capabilities"):
                    continue
                if not isinstance(field_value, ast.Dict):
                    continue
                for cap, declared in zip(field_value.keys, field_value.values, strict=True):
                    if not (isinstance(cap, ast.Constant) and isinstance(declared, ast.Constant)):
                        continue
                    if isinstance(declared.value, bool):
                        caps[str(cap.value)] = declared.value
            out[str(key.value)] = caps
        return out
    return None


def fallback_sets(source: str) -> dict[str, set[str]] | None:
    """Read `{capability: {type, ...}}` off the `CAPABILITY_FALLBACK` object literal."""
    block = re.search(r"CAPABILITY_FALLBACK[^=]*=\s*\{(.*?)\n\};", source, re.S)
    if block is None:
        return None
    out: dict[str, set[str]] = {}
    for cap, body in re.findall(r"(\w+):\s*new Set\(\[([^\]]*)\]\)", block.group(1)):
        out[cap] = set(re.findall(r"'([^']*)'", body))
    return out


def capability_union(source: str) -> list[str]:
    """The `ProviderCapability` union members, in declaration order."""
    block = re.search(r"export type ProviderCapability\s*=(.*?);", source, re.S)
    return re.findall(r"'([a-z_]+)'", block.group(1)) if block else []


def read_tree(root: Path) -> tuple[dict[str, dict[str, bool]], dict[str, set[str]], list[str]]:
    """The three declarations this gate compares.

    Raises `ValueError` naming the one it could not read, rather than reporting agreement
    between things it never found: a gate that passes on an empty reading is the silence
    it was written to end.
    """
    for relative in (FACTORY, FALLBACK_TS, API_TS):
        if not (root / relative).exists():
            raise ValueError(f"{relative.as_posix()} is missing")

    backend = backend_capabilities((root / FACTORY).read_text(encoding="utf-8"))
    if backend is None:
        raise ValueError(f"no PROVIDER_TYPES dict literal in {FACTORY.as_posix()}")

    fallback = fallback_sets((root / FALLBACK_TS).read_text(encoding="utf-8"))
    if fallback is None:
        raise ValueError(f"no CAPABILITY_FALLBACK object literal in {FALLBACK_TS.as_posix()}")

    union = capability_union((root / API_TS).read_text(encoding="utf-8"))
    if not union:
        raise ValueError(f"no ProviderCapability union in {API_TS.as_posix()}")

    return backend, fallback, union


def check(root: Path) -> list[str]:
    """Every disagreement between the floor and the backend, in a stable order."""
    backend, fallback, union = read_tree(root)
    problems: list[str] = []

    # R1: the floor answers what the backend declares, for every type the backend ships.
    for provider_type in sorted(backend):
        for capability in union:
            if capability not in backend[provider_type]:
                continue  # Undeclared, so the floor is the only answer and there is no second.
            declared = backend[provider_type][capability]
            floored = provider_type in fallback.get(capability, set())
            if declared == floored:
                continue
            if capability not in fallback:
                verb = "and CAPABILITY_FALLBACK has no entry for it"
            else:
                verb = f"and CAPABILITY_FALLBACK {'lists' if floored else 'does not list'} it"
            problems.append(
                f"  {capability}: factory.py declares {provider_type}={declared}, {verb}"
            )

    # R2: no stale type.
    for capability in sorted(fallback):
        for provider_type in sorted(fallback[capability] - set(backend)):
            problems.append(
                f"  {capability}: CAPABILITY_FALLBACK lists {provider_type}, "
                f"which PROVIDER_TYPES does not ship"
            )

    # R3: no capability the panel cannot name.
    for capability in sorted({cap for caps in backend.values() for cap in caps} - set(union)):
        problems.append(
            f"  {capability}: factory.py declares it, and the ProviderCapability union "
            f"in types/api.ts does not name it"
        )

    return problems


def main() -> int:
    try:
        problems = check(REPO_ROOT)
        backend, _, union = read_tree(REPO_ROOT)
    except ValueError as unreadable:
        print(f"Capability parity check failed: {unreadable}.")
        return 1

    if problems:
        print("Capability parity check failed: the fallback table and the backend disagree.")
        print("")
        print("\n".join(problems))
        print("")
        print(
            "That table is what every screen reads while GET /providers/types is in flight\n"
            "or failing, and it cannot answer 'unknown': a capability it does not name reads\n"
            "as false, and the screens act on false. Update CAPABILITY_FALLBACK in\n"
            "frontend/src/lib/providers.ts so it agrees with app/providers/factory.py."
        )
        return 1

    declarations = sum(len(set(caps) & set(union)) for caps in backend.values())
    print(
        f"Capability parity check passed ({declarations} declarations across "
        f"{len(backend)} provider types, floor and backend agree)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
