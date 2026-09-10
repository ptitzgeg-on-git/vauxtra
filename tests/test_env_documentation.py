"""Every environment variable the code reads must be named in `.env.example`.

Six were not. Three of them are real operator controls that no operator could have found:
`VAUXTRA_PROVIDER_PLUGINS` is an entire extension mechanism -- it imports arbitrary Python
modules named in the variable -- and `VAUXTRA_REWRITE_LOCALHOST` / `VAUXTRA_LOCALHOST_ALIAS`
decide whether a provider URL pointing at `localhost` is silently rewritten to
`host.docker.internal`, which is exactly the behaviour somebody debugging "why can't the
container reach my proxy" needs to know exists.

A variable that only appears in the source is not configuration, it is a secret handshake.
This test is the sync check: add a reader, document it, or say here why it is not an
operator control.
"""

import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Where a variable read at runtime can come from.
_SOURCE_ROOTS = ("app", "vauxtra_mcp")

_READER = re.compile(
    r"(?:os\.environ\.get|os\.getenv)\(\s*[\"']([A-Z][A-Z_0-9]*)[\"']",
    re.S,
)

# `.env.example` lines, live or commented out: `NAME=`, `# NAME=`.
_DECLARED = re.compile(r"^\s*#?\s*([A-Z][A-Z_0-9]*)\s*=", re.M)

# Read by the code, deliberately absent from `.env.example`. Each needs a reason.
_NOT_OPERATOR_CONTROLS = {
    # Stamped into the image by the Dockerfile at build time (`ARG APP_VERSION`). Setting it
    # by hand would only make the instance lie about which build it is running.
    "APP_VERSION",
}


def _env_vars_read_by_the_code() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for root in _SOURCE_ROOTS:
        for path in (_REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for match in _READER.finditer(path.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(match.group(1), set()).add(
                    path.relative_to(_REPO).as_posix()
                )
    return found


def _env_vars_declared_in_the_example() -> set[str]:
    text = (_REPO / ".env.example").read_text(encoding="utf-8")
    return set(_DECLARED.findall(text))


class EveryVariableIsDocumentedTests(unittest.TestCase):
    def test_the_code_reads_nothing_an_operator_cannot_find(self) -> None:
        read = _env_vars_read_by_the_code()
        declared = _env_vars_declared_in_the_example()

        undocumented = {
            name: sorted(where)
            for name, where in read.items()
            if name not in declared and name not in _NOT_OPERATOR_CONTROLS
        }
        self.assertEqual(
            undocumented,
            {},
            "These variables change how the application behaves and appear nowhere an "
            "operator would look. Document them in `.env.example`, or add them to "
            "`_NOT_OPERATOR_CONTROLS` with the reason they are not configuration.",
        )

    def test_the_example_does_not_advertise_a_variable_nothing_reads(self) -> None:
        """The other direction: a documented knob that turns nothing.

        `TZ` is the exception that proves the rule -- no Python line reads it, the C library
        does, which is why the Dockerfile sets it.
        """
        read = set(_env_vars_read_by_the_code())
        declared = _env_vars_declared_in_the_example()
        read_by_the_platform = {"TZ"}

        orphans = sorted(declared - read - read_by_the_platform)
        self.assertEqual(
            orphans,
            [],
            "`.env.example` offers these and no code reads them: an operator sets one and "
            "nothing happens.",
        )

    def test_the_exception_list_stays_honest(self) -> None:
        """An entry that no longer has a reader is an exemption nobody will re-examine."""
        read = set(_env_vars_read_by_the_code())
        stale = sorted(name for name in _NOT_OPERATOR_CONTROLS if name not in read)
        self.assertEqual(stale, [], "Nothing reads these any more; drop them from the list.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
