"""A gate nobody has ever seen fail is a claim. This is the proof that this one refuses.

The defect it exists for was live when it was written, in six places in one file, and it
had been live long enough for a whole product mode to never once be examined. The last
class rebuilds each way a row reaches a function -- read from a cursor, iterated over,
handed in as a parameter, aliased -- and checks the gate names the test in every one.

The class before it runs the gate against this repository, so `python -m pytest tests/`
catches the next instance even with the CI step removed.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_row_membership.py"
_spec = importlib.util.spec_from_file_location("check_row_membership", _GATE_PATH)
membership = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(membership)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tree(source: str) -> Path:
    """A throwaway app/ holding one module."""
    root = Path(tempfile.mkdtemp())
    package = root / "app" / "api"
    package.mkdir(parents=True)
    (package / "fake.py").write_text(source, encoding="utf-8")
    return root


READ_FROM_A_CURSOR = '''
def public_host(conn, sid):
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if "expose_mode" in svc:
        return svc["tunnel_hostname"]
    return ""
'''

HANDED_IN_AS_A_PARAMETER = '''
def public_host(service_row):
    return service_row["expose_mode"] if "expose_mode" in service_row else "proxy_dns"
'''

ITERATED_OVER = '''
def modes(conn):
    for row in conn.execute("SELECT * FROM services").fetchall():
        if "expose_mode" in row:
            yield row["expose_mode"]
'''

ALIASED = '''
def public_host(conn, sid):
    fetched = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    svc = fetched
    return "tunnel" if "expose_mode" in svc else ""
'''

ASKING_THE_KEYS = '''
def public_host(conn, sid):
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    if "expose_mode" in svc.keys():
        return svc["expose_mode"]
    return ""
'''

A_PLAIN_DICT = '''
def public_host(payload):
    if "expose_mode" in payload:
        return payload["expose_mode"]
    return ""
'''


class TheGateHoldsThisRepositoryTests(unittest.TestCase):

    def test_no_row_in_this_repository_is_asked_about_its_values(self):
        """The gate runs here, so the suite catches the next instance on its own."""
        problems, inspected = membership.check(REPO_ROOT)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(inspected, 50)

    def test_the_gate_looks_at_the_functions_that_hold_a_row(self):
        """A gate that inspected nothing would also report no problems.

        Left as a floor rather than an exact count: a route added to `app/` that reads a
        row is not a reason for this test to fail, and the count above it is the one that
        would catch a gate gone blind.
        """
        _, inspected = membership.check(REPO_ROOT)
        self.assertGreater(inspected, 0)


class TheGateFindsARowWhicheverWayItArrivedTests(unittest.TestCase):

    def test_a_row_read_from_a_cursor_is_judged(self):
        problems, inspected = membership.check(_tree(READ_FROM_A_CURSOR))
        self.assertEqual(inspected, 1)
        self.assertEqual(len(problems), 1)
        self.assertIn('"expose_mode" in svc', problems[0])
        self.assertIn("app/api/fake.py:4", problems[0])

    def test_a_row_handed_in_as_a_parameter_is_judged(self):
        problems, _ = membership.check(_tree(HANDED_IN_AS_A_PARAMETER))
        self.assertEqual(len(problems), 1)
        self.assertIn('"expose_mode" in service_row', problems[0])

    def test_a_row_iterated_over_is_judged(self):
        problems, _ = membership.check(_tree(ITERATED_OVER))
        self.assertEqual(len(problems), 1)
        self.assertIn('"expose_mode" in row', problems[0])

    def test_an_alias_of_a_row_is_judged(self):
        problems, _ = membership.check(_tree(ALIASED))
        self.assertEqual(len(problems), 1)
        self.assertIn('"expose_mode" in svc', problems[0])

    def test_not_in_is_the_same_question_inverted(self):
        source = READ_FROM_A_CURSOR.replace('"expose_mode" in svc', '"expose_mode" not in svc')
        problems, _ = membership.check(_tree(source))
        self.assertEqual(len(problems), 1)
        self.assertIn('"expose_mode" not in svc', problems[0])

    def test_the_six_sites_of_the_original_defect_are_all_named(self):
        """The shape as it stood in `app/api/sync.py`, both column names it asked about."""
        source = (
            READ_FROM_A_CURSOR
            + HANDED_IN_AS_A_PARAMETER.replace("public_host", "collect_targets")
            + '''
def build_plan(conn, sid):
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    mode = svc["public_target_mode"] if "public_target_mode" in svc else "manual"
    name = svc["tunnel_hostname"] if "tunnel_hostname" in svc else ""
    return mode, name
'''
        )
        problems, _ = membership.check(_tree(source))
        self.assertEqual(len(problems), 4)
        self.assertEqual(
            sorted(p.split("  ")[-1] for p in problems),
            [
                '"expose_mode" in service_row',
                '"expose_mode" in svc',
                '"public_target_mode" in svc',
                '"tunnel_hostname" in svc',
            ],
        )


class TheGateLeavesTheRightThingsAloneTests(unittest.TestCase):

    def test_asking_the_keys_is_the_remedy_and_passes(self):
        problems, inspected = membership.check(_tree(ASKING_THE_KEYS))
        self.assertEqual(problems, [])
        self.assertEqual(inspected, 1)

    def test_a_plain_dict_is_not_a_row(self):
        """A dict already answers on its keys, and most of `app/` passes dicts.

        A gate that cried about those would cry 40 times and be read as noise, which is
        how the six real sites hid in plain sight in the first place.
        """
        problems, inspected = membership.check(_tree(A_PLAIN_DICT))
        self.assertEqual(problems, [])
        self.assertEqual(inspected, 0)

    def test_a_substring_test_on_a_column_is_left_alone(self):
        """`"@" in svc["username"]` reads a value, and a value is a string."""
        source = '''
def looks_like_an_email(conn, sid):
    svc = conn.execute("SELECT * FROM providers WHERE id=?", (sid,)).fetchone()
    return "@" in svc["username"]
'''
        problems, inspected = membership.check(_tree(source))
        self.assertEqual(problems, [])
        self.assertEqual(inspected, 1)

    def test_a_name_rebound_to_something_else_stops_being_a_row(self):
        source = '''
def summarise(conn, sid):
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    svc = dict(svc, expose_mode="tunnel")
    return "expose_mode" in svc
'''
        problems, _ = membership.check(_tree(source))
        self.assertEqual(problems, [])

    def test_a_non_literal_membership_test_is_left_alone(self):
        """`column in svc` with a variable on the left is a different claim to judge."""
        source = '''
def holds(conn, sid, column):
    svc = conn.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
    return column in svc
'''
        problems, _ = membership.check(_tree(source))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
