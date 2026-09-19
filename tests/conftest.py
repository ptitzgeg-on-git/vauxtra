"""Fail the run if the suite touched the operator's own database.

Tests redirect the database by rebinding `DATA_DIR` and `DB_PATH` on `app.models` and
`app.db` — see `IsolatedDBTestCase` in `test_api_endpoints.py`. A test that believes it did
that and did not still passes: it creates its tables in `data/vauxtra.db`, reads them back,
and every assertion holds. Two modules once set those names in `os.environ` instead, which
`app/config.py` never reads, and for as long as that lasted one of them emptied
`service_templates` before each of its tests and left its fixtures behind. On a developer's
checkout that is the operator's data.

So this does not check how a test redirects the database, only whether the real file came
out of the run as it went in. In CI the file does not exist at checkout and must not exist
afterwards; on a checkout that has one, its bytes must match.

The reading is taken in `pytest_configure`, which runs before collection. A session-scoped
fixture would not set up until the first test, leaving anything a module writes while being
imported outside the window — and importing is when a mistaken `init_db()` at module level
would fire.
"""

import hashlib
import pathlib

import pytest

_DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "vauxtra.db"

_ADVICE = (
    "A test wrote to the database this checkout runs on, instead of a temporary one.\n"
    "Redirect it the way tests/test_api_endpoints.py::IsolatedDBTestCase does: rebind\n"
    "DATA_DIR and DB_PATH on BOTH app.models and app.db before calling init_db(), and\n"
    "restore them in teardown. Setting them in os.environ does nothing -- app/config.py\n"
    "builds both paths from its own location and never reads the environment."
)

_before = None


def _state():
    """What the file is right now, as something comparable."""
    if not _DB.exists():
        return None
    return hashlib.sha256(_DB.read_bytes()).hexdigest()


def pytest_configure(config):
    global _before
    _before = _state()


@pytest.fixture(scope="session", autouse=True)
def operator_database_is_left_alone():
    yield
    after = _state()
    if _before == after:
        return
    if _before is None:
        pytest.fail(f"The suite created {_DB}, which did not exist before it ran.\n\n{_ADVICE}")
    if after is None:
        pytest.fail(f"The suite deleted {_DB}.\n\n{_ADVICE}")
    pytest.fail(f"The suite modified {_DB}.\n\n{_ADVICE}")
