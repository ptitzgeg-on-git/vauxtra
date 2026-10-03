"""The Vauxtra MCP bridge.

`__version__` is the Vauxtra release this bridge ships with, reported in the MCP
handshake. Declared here because the package imports nothing from `app` (it talks to an
instance over HTTP). tests/test_version_declarations.py keeps it equal to
frontend/package.json.
"""

__version__ = "1.7.0"
