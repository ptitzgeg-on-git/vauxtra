"""What may be written in the `scopes` column, and the three files that each claim to say.

Two questions nothing in the suite used to ask.

*Where a scope name stops.* `_split_scopes` called a bare `str.strip()`, so the boundary
between "padding" and "part of the name" was wherever the Unicode tables happen to put it.
Measured on this build, against a column holding one code point in front of `admin`:
U+0009, U+000A, U+0020, U+00A0 and U+2007 all granted admin, and U+200B granted nothing.
Two invisible prefixes opened `GET /api/settings/api-keys` and a third did not, and no line
in the source said which was which -- a reader could not have predicted the answer, and the
one who wrote the strip did not choose it. The rule now written down is the six ASCII
blanks and nothing else, so U+00A0 and U+2007 join U+200B on the refused side and the
answer is the same for every blank a paste can carry. `RefusedScopePadding` walks all six.
The ASCII cases are unchanged: `" admin"` grants admin exactly as it did, which is the
decision already pinned in `tests/test_api_key_scope_residues.py`.

*What the scope names are.* They are written down three times -- `VALID_SCOPES`, the
`Literal[...]` on `ApiKeyCreate.scopes` (both `app/api/api_keys.py`) and the keys of
`_SCOPE_LEVEL` (`app/auth.py`) -- and nothing compared them. They agree today; the point of
`TheThreeScopeListsAgree` is the day one of them is edited alone. The failure modes are not
symmetric, which is why the comparison is worth its lines: a name added to `_SCOPE_LEVEL`
alone cannot be granted to any key, while a name added to the other two alone produces keys
that authorize nothing at all -- `_scope_satisfies` reads an unknown scope as -1, and
`_get_auth_context` refuses a key whose scopes are all unknown. Both are silent.
"""

import hashlib
import os
import tempfile
import unittest
from typing import get_args
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as app_db
import app.scheduler as scheduler
from app import models
from app.api.api_keys import _SCOPE_PADDING, VALID_SCOPES, ApiKeyCreate, _split_scopes

PASSWORD = "test-password-long-enough"

# `require_auth(request, scope="admin")`, the route an `admin` scope and nothing else opens.
ADMIN_ROUTE = "/api/settings/api-keys"

PADDING = "padding"
PART_OF_THE_NAME = "part of the scope name"

# Written as `chr(...)` so the source carries no invisible character of its own: a literal
# no-break space in this file would be the same trap the column held.
# label -> (code point, what this build decides it is)
CODE_POINTS = {
    "U+0009 character tabulation": (chr(0x0009), PADDING),
    "U+000A line feed": (chr(0x000A), PADDING),
    "U+0020 space": (chr(0x0020), PADDING),
    "U+00A0 no-break space": (chr(0x00A0), PART_OF_THE_NAME),
    "U+2007 figure space": (chr(0x2007), PART_OF_THE_NAME),
    "U+200B zero-width space": (chr(0x200B), PART_OF_THE_NAME),
}


class ScopePaddingIsNamed(unittest.TestCase):
    """The rule is a constant in the source, not whatever `str.strip()` decides today."""

    def test_the_padding_is_exactly_the_six_ascii_blanks(self):
        """Widening this set widens what an invisible prefix can be, so it has to be seen.

        The code points are written out rather than compared to `string.whitespace`, which
        is what the constant is built from: comparing it to itself would pass whatever that
        name came to mean.
        """
        self.assertEqual(
            sorted(ord(c) for c in _SCOPE_PADDING),
            [0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20],
            "app/api/api_keys.py:_SCOPE_PADDING is no longer tab, line feed, vertical tab, "
            "form feed, carriage return and space -- every code point added to it is one "
            "more invisible prefix that grants whatever follows it",
        )

    def test_a_bare_strip_would_not_answer_the_same_way(self):
        """The reason the rule is named: the two disagree, and only on the blanks that hide.

        This is the measurement that made the decision, kept executable. If a future Python
        stops stripping U+00A0 this test fails -- and it should, because the sentence in
        `_split_scopes` about what a bare strip costs would have stopped being true.
        """
        disagree = sorted(
            label
            for label, (code_point, _verdict) in CODE_POINTS.items()
            if (code_point + "admin").strip() != (code_point + "admin").strip(_SCOPE_PADDING)
        )
        self.assertEqual(
            disagree,
            ["U+00A0 no-break space", "U+2007 figure space"],
            "the named padding and a bare str.strip() no longer differ where the docstring "
            "of _split_scopes says they do",
        )


class ScopePaddingIsWalkedCodePointByCodePoint(unittest.TestCase):
    """`_split_scopes` alone: what survives the read, for each code point in the table."""

    def test_each_code_point_is_padding_or_stays_part_of_the_name(self):
        for label, (code_point, verdict) in CODE_POINTS.items():
            with self.subTest(label=label, verdict=verdict):
                read = _split_scopes(code_point + "admin")
                if verdict == PADDING:
                    self.assertEqual(read, ["admin"], f"{label} was expected to be padding")
                else:
                    self.assertEqual(
                        read,
                        [code_point + "admin"],
                        f"{label} was expected to stay part of the scope name",
                    )

    def test_padding_is_removed_from_both_ends_of_every_segment(self):
        """Positive witness: the rule is about the ends, not about the first one only."""
        tab = CODE_POINTS["U+0009 character tabulation"][0]
        self.assertEqual(_split_scopes(f"{tab}read{tab},{tab}write{tab}"), ["read", "write"])

    def test_a_segment_of_padding_alone_names_nothing(self):
        """Positive witness: an all-blank segment is still dropped, as it was before."""
        tab = CODE_POINTS["U+0009 character tabulation"][0]
        self.assertEqual(_split_scopes(f"{tab},{tab}"), [])

    def test_a_refused_blank_alone_is_not_dropped(self):
        """It is a scope name made of one character, and the key list has to print it.

        Hiding it would leave the operator with a key that opens nothing and a list that
        shows nothing, which is the shape the empty-column hole already cost once.
        """
        nbsp = CODE_POINTS["U+00A0 no-break space"][0]
        self.assertEqual(_split_scopes(nbsp), [nbsp])


class RefusedScopePadding(unittest.TestCase):
    """The same table, decided by real requests: one key per code point, all granted admin.

    `tests/` is not a package, so this fixture is duplicated per file by design.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "scope-padding.test.db")
        models.init_db()

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            # Without a password every request is granted `admin`, and no scope is weighed.
            patch.object(auth, "APP_PASSWORD", PASSWORD),
        ]
        for p in self._patchers:
            p.start()

        import app.main as app_main

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

        conn = models.get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                       prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                       created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
            )
            rows = [(self._key_name(label), code_point + "admin")
                    for label, (code_point, _v) in CODE_POINTS.items()]
            # The witness: an unpadded `admin` row, so a refusal below means the padding and
            # not a broken fixture, a wrong route or a password that was never configured.
            rows.append(("plain", "admin"))
            for name, scopes in rows:
                conn.execute(
                    "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                    (name, hashlib.sha256(f"key-{name}".encode()).hexdigest(), name[:8], scopes),
                )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    @staticmethod
    def _key_name(label: str) -> str:
        return label.split()[0]

    def _reach_the_admin_route(self, name: str):
        return self.client.get(ADMIN_ROUTE, headers={"Authorization": f"Bearer key-{name}"})

    def test_an_unpadded_admin_row_opens_the_admin_route(self):
        """The positive witness, first: without it every refusal below proves nothing."""
        self.assertEqual(self._reach_the_admin_route("plain").status_code, 200)

    def test_each_code_point_in_front_of_admin_grants_or_refuses_deliberately(self):
        """Six rows, six answers, each one written down next to the code point that gets it.

        An ASCII blank in front of `admin` grants admin: the operator typed a scope and a
        stray space, which is the reading already pinned in the residues file. Anything else
        is refused: nobody types a no-break space, it arrives by paste, and the key is turned
        away with a log line that prints the stored value so the row can be fixed.
        """
        for label, (_code_point, verdict) in CODE_POINTS.items():
            with self.subTest(label=label, verdict=verdict):
                resp = self._reach_the_admin_route(self._key_name(label))
                if verdict == PADDING:
                    self.assertEqual(
                        resp.status_code,
                        200,
                        f"{label} is padding, so this row grants admin: {resp.text}",
                    )
                else:
                    self.assertEqual(
                        resp.status_code,
                        401,
                        f"{label} stays part of the scope name, so this row names a scope "
                        f"this build does not know and authorizes nothing: {resp.text}",
                    )

    def test_a_refused_row_is_still_printed_in_the_key_list(self):
        """Refusing it is not hiding it -- the operator has to see the row to repair it."""
        nbsp, _ = CODE_POINTS["U+00A0 no-break space"]
        listed = self._reach_the_admin_route("plain").json()
        rows = {k["name"]: k["scopes"] for k in listed}
        self.assertEqual(rows["U+00A0"], [nbsp + "admin"])


class TheThreeScopeListsAgree(unittest.TestCase):
    """One vocabulary, three written copies, and until now no line comparing them."""

    @staticmethod
    def _literal_scopes() -> set[str]:
        """The `Literal[...]` inside `list[...]` on `ApiKeyCreate.scopes`."""
        annotation = ApiKeyCreate.model_fields["scopes"].annotation
        (literal,) = get_args(annotation)
        return set(get_args(literal))

    def test_the_literal_reader_finds_something(self):
        """The witness: a comparison against an empty set would pass for the wrong reason.

        If the annotation is ever rewritten the extraction above can quietly answer `set()`,
        and every comparison below would then be `set() == set()` for one side and a real
        failure for the other. Asked separately so the message says which happened.
        """
        self.assertTrue(
            self._literal_scopes(),
            "the Literal[...] on ApiKeyCreate.scopes could not be read; the comparisons "
            "below would be measuring nothing",
        )

    def test_valid_scopes_and_the_literal_name_the_same_scopes(self):
        valid, literal = set(VALID_SCOPES), self._literal_scopes()
        self.assertEqual(
            valid,
            literal,
            "app/api/api_keys.py drifted from itself: VALID_SCOPES and the Literal[...] on "
            f"ApiKeyCreate.scopes disagree. Only in VALID_SCOPES: {sorted(valid - literal)}; "
            f"only in the Literal: {sorted(literal - valid)}. The Literal is what refuses a "
            "creation request; VALID_SCOPES is what val_scopes names in the error message.",
        )

    def test_valid_scopes_and_the_scope_ladder_name_the_same_scopes(self):
        valid, ladder = set(VALID_SCOPES), set(auth._SCOPE_LEVEL)
        self.assertEqual(
            valid,
            ladder,
            "VALID_SCOPES (app/api/api_keys.py) and _SCOPE_LEVEL (app/auth.py) disagree. "
            f"Only in VALID_SCOPES: {sorted(valid - ladder)}; only in _SCOPE_LEVEL: "
            f"{sorted(ladder - valid)}. A scope creation accepts but the ladder does not "
            "know is read as -1 and authorizes no route; a scope the ladder knows but "
            "creation refuses can never be granted to a key.",
        )

    def test_all_three_declarations_name_the_same_scopes(self):
        """One assertion the next drift cannot pass, whichever of the three moved."""
        declarations = {
            "VALID_SCOPES (app/api/api_keys.py)": set(VALID_SCOPES),
            "Literal[...] on ApiKeyCreate.scopes (app/api/api_keys.py)": self._literal_scopes(),
            "_SCOPE_LEVEL keys (app/auth.py)": set(auth._SCOPE_LEVEL),
        }
        distinct = {frozenset(names) for names in declarations.values()}
        self.assertEqual(
            len(distinct),
            1,
            "the scope vocabulary is written down three times and they no longer match: "
            + "; ".join(f"{where} = {sorted(names)}" for where, names in declarations.items()),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
