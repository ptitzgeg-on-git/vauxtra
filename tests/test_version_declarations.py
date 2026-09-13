"""The bridge's version has to be Vauxtra's, and it has to be the one it actually reports.

`FastMCP(...)` takes a `version` keyword. Left out, it answers the MCP handshake with the
version of the *library*, so `serverInfo` read `{"name": "Vauxtra", "version": "3.2.4"}` —
a release of Vauxtra that has never existed, moving with every fastmcp bump instead of
with this repository.

Two things can go wrong from here, and neither one shows up in an ordinary run:

  * The release number is bumped in `frontend/package.json` and the bridge is forgotten.
    That has already happened to that file on its own: `v1.0.2` was tagged while it still
    said `1.0.1`, and nothing anywhere said a word.
  * The `version=` argument is dropped or renamed. The bridge still starts, still lists
    every tool, and goes quietly back to announcing fastmcp's version.

So one test reads the declaration and the other reads what the built instance carries.
"""

import json
import pathlib
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _release_version() -> str:
    """Where this repository has kept its release number since v1.0.0."""
    manifest = json.loads((_REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
    return manifest["version"]


class VersionDeclarationsTests(unittest.TestCase):
    def test_the_bridge_declares_the_release_version(self) -> None:
        from vauxtra_mcp import __version__

        self.assertEqual(
            __version__,
            _release_version(),
            "vauxtra_mcp/__init__.py and frontend/package.json disagree about which "
            "release this is. They move together when the version is bumped.",
        )

    def test_the_bridge_reports_that_version_in_the_handshake(self) -> None:
        from vauxtra_mcp import __version__
        from vauxtra_mcp.app import mcp

        self.assertEqual(
            mcp.version,
            __version__,
            "The FastMCP instance is not carrying the declared version, so the handshake "
            "answers with fastmcp's instead. See the `version=` argument in "
            "vauxtra_mcp/app.py.",
        )


if __name__ == "__main__":
    unittest.main()
