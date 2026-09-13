"""The Vauxtra MCP bridge.

`__version__` is what the bridge answers with in the MCP handshake, and it is the version
of Vauxtra it ships beside — not the version of the library it is built on. It lives here
rather than being read from `app.config` because this package imports nothing from `app`:
it reaches a Vauxtra instance over HTTP through `VAUXTRA_URL`, and that instance may well
be running a different release than the checkout this bridge was started from.

`APP_VERSION` is not consulted either. The Dockerfile stamps that at build time and the
bridge is deliberately not in the image, so out here it would always read `dev`.

`tests/test_version_declarations.py` holds this to the version in `frontend/package.json`,
which is where this repository has kept its release number since v1.0.0.
"""

__version__ = "1.4.0"
