"""A gate nobody has ever seen fail is a claim. This is the proof that this one refuses.

The drift it exists for was live when it was written. `CAPABILITY_FALLBACK` in
`frontend/src/lib/providers.ts` named four of the six capabilities in `ProviderCapability`,
and it had never been compared with `PROVIDER_TYPES`, so five cells disagreed at once:
`certificates` and `supports_auto_public_target` were absent altogether, and `proxy` did not
list `cloudflare_tunnel`. The last test here rebuilds exactly that table, against this
repository's real backend, and checks the gate names all five. The one before it runs the
gate against this repository, so `python -m pytest tests/` catches the next drift even if
the CI step were removed.

The tests in between each move one declaration, because that is the only way to tell the
gate's three rules apart, and one of them moves a declaration the gate must ignore: a type
the backend does not describe is exactly what a floor is for.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_capability_parity.py"
_spec = importlib.util.spec_from_file_location("check_capability_parity", _GATE_PATH)
parity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity)

REPO_ROOT = Path(__file__).resolve().parent.parent

FACTORY_SOURCE = '''"""A throwaway catalogue, small enough to reason about."""

PROVIDER_TYPES = {
    "npm": {
        "label": "Nginx Proxy Manager",
        "capabilities": {"proxy": True, "dns": False, "certificates": True},
    },
    "cloudflare": {
        "label": "Cloudflare",
        "capabilities": {"proxy": False, "dns": True, "supports_auto_public_target": True},
    },
    "legacy": {"label": "A type from before capabilities existed"},
}
'''

API_SOURCE = """export type ProviderCapability =
  | 'proxy'
  | 'dns'
  | 'supports_auto_public_target'
  | 'certificates';
"""

FALLBACK_SOURCE = """const CAPABILITY_FALLBACK: Partial<Record<ProviderCapability, ReadonlySet<string>>> = {
%s
};
"""

AGREEING = {
    "proxy": ["npm"],
    "dns": ["cloudflare"],
    "supports_auto_public_target": ["cloudflare"],
    "certificates": ["npm"],
}

HISTORICAL_FALLBACK = FALLBACK_SOURCE % "\n".join(
    [
        "  proxy: new Set(['npm', 'traefik', 'zoraxy']),",
        "  dns: new Set(['cloudflare', 'pihole', 'adguard', 'technitium', 'powerdns', 'desec']),",
        "  public_dns: new Set(['cloudflare', 'desec']),",
        "  supports_tunnel: new Set(['cloudflare_tunnel']),",
    ]
)


def _write(root: Path, relative: Path, text: str) -> None:
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_text(text, encoding="utf-8")


def _tree(
    directory: str,
    *,
    entries: dict[str, list[str]] | None = None,
    factory: str = FACTORY_SOURCE,
    api: str = API_SOURCE,
) -> Path:
    """A throwaway repository whose three declarations are set independently."""
    root = Path(directory)
    listed = AGREEING if entries is None else entries
    body = "\n".join(
        "  {}: new Set([{}]),".format(cap, ", ".join(f"'{name}'" for name in names))
        for cap, names in listed.items()
    )
    _write(root, parity.FACTORY, factory)
    _write(root, parity.FALLBACK_TS, FALLBACK_SOURCE % body)
    _write(root, parity.API_TS, api)
    return root


def _without(capability: str) -> dict[str, list[str]]:
    return {cap: list(names) for cap, names in AGREEING.items() if cap != capability}


def _with(capability: str, names: list[str]) -> dict[str, list[str]]:
    changed = {cap: list(existing) for cap, existing in AGREEING.items()}
    changed[capability] = names
    return changed


class CapabilityParityGateTests(unittest.TestCase):
    def test_a_tree_that_agrees_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(parity.check(_tree(directory)), [])

    def test_a_capability_the_table_never_names_is_caught(self) -> None:
        """The shape `certificates` was in: absent, and therefore false for everything."""
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, entries=_without("certificates")))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("certificates: factory.py declares npm=True", problems[0])
        self.assertIn("has no entry for it", problems[0])

    def test_a_type_missing_from_a_set_is_caught(self) -> None:
        """The shape `cloudflare_tunnel` was in: the capability is named, the type is not."""
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, entries=_with("proxy", [])))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("proxy: factory.py declares npm=True", problems[0])
        self.assertIn("does not list it", problems[0])

    def test_a_type_the_floor_should_not_claim_is_caught(self) -> None:
        """Drift runs both ways: the backend says no and the floor says yes."""
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, entries=_with("proxy", ["npm", "cloudflare"])))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("proxy: factory.py declares cloudflare=False", problems[0])
        self.assertIn("lists it", problems[0])

    def test_a_type_the_backend_never_ships_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, entries=_with("dns", ["cloudflare", "ghost"])))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CAPABILITY_FALLBACK lists ghost", problems[0])
        self.assertIn("does not ship", problems[0])

    def test_a_capability_the_union_does_not_name_is_caught(self) -> None:
        """R1 iterates the union, so an unlisted capability would be skipped in silence."""
        narrowed = API_SOURCE.replace("  | 'certificates';", "  | 'supports_tunnel';")
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, api=narrowed))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("certificates: factory.py declares it", problems[0])
        self.assertIn("does not name it", problems[0])

    def test_a_type_the_backend_does_not_describe_is_left_alone(self) -> None:
        """The floor's whole reason to exist, and the one thing this gate must not touch."""
        with tempfile.TemporaryDirectory() as directory:
            problems = parity.check(_tree(directory, entries=_with("proxy", ["npm", "legacy"])))
        self.assertEqual(problems, [])

    def test_a_missing_file_refuses_to_guess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(directory)
            (root / parity.API_TS).unlink()
            with self.assertRaises(ValueError) as caught:
                parity.check(root)
        self.assertIn("types/api.ts is missing", str(caught.exception))

    def test_a_table_it_cannot_find_refuses_to_guess(self) -> None:
        """Reporting agreement between things it never found is the silence it exists to end."""
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(directory)
            (root / parity.FALLBACK_TS).write_text("export const nothing = 1;\n", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                parity.check(root)
        self.assertIn("no CAPABILITY_FALLBACK object literal", str(caught.exception))

    def test_this_repository_agrees_with_its_own_backend(self) -> None:
        """The live check, so the suite alone is enough to catch the next drift."""
        self.assertEqual(parity.check(REPO_ROOT), [])

    def test_the_drift_this_gate_was_written_for_would_have_been_caught(self) -> None:
        """This repository's real backend, with the table exactly as it stood before.

        Five cells, four types, three capabilities: the whole of what a failing
        `GET /providers/types` was quietly telling the expose modal.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (parity.FACTORY, parity.API_TS):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(REPO_ROOT / relative, root / relative)
            _write(root, parity.FALLBACK_TS, HISTORICAL_FALLBACK)
            problems = parity.check(root)

        self.assertEqual(len(problems), 5, problems)
        reported = "\n".join(problems)
        for cell in (
            "certificates: factory.py declares npm=True",
            "certificates: factory.py declares zoraxy=True",
            "supports_auto_public_target: factory.py declares cloudflare=True",
            "supports_auto_public_target: factory.py declares desec=True",
            "proxy: factory.py declares cloudflare_tunnel=True",
        ):
            self.assertIn(cell, reported)


if __name__ == "__main__":
    unittest.main()
