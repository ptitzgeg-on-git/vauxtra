"""What the empty-scope fix left behind, and the coupling that keeps its guard alive.

The hole itself is closed and covered elsewhere (`tests/test_api_key_empty_scopes.py`): a
key created with `"scopes": []` was written with an empty column, read back as the scope
`""`, and accepted by every `require_auth(request)` that names no scope. Three residues
survived it, each measured by executing the code rather than by reading it:

* `_split_scopes` dropped a segment only when it was exactly empty. `_split_scopes(" ")`
  answered `[" "]` and `_split_scopes(" , ")` answered `[" ", " "]`, so a row holding one
  space reproduced the original hole whole: a granted scope named with a space, drawn in
  the key list as a blank badge. It was the only comma-split in the repository that did not
  strip its segments (`app/security.py`, `app/public_target.py`, `app/api/settings.py` and
  `app/providers/factory.py` all do).
* The auth guard refused an empty list rather than a list with nothing usable in it. A row
  holding `"readd"` walked through it: `GET /api/environments` answered 200 while the
  scoped routes answered 403. `_scope_satisfies` already reads unknown scopes as -1, so the
  guard was the only place that still equated "granted nothing" with "empty list". Such a
  row takes a direct write to the table, since creation validates against `VALID_SCOPES`.
* The MCP bridge declared `create_api_key(name, scopes)` with neither a default nor a
  minimum, so a caller could still hand it `[]`. Nothing compared the two signatures:
  `scripts/check_api_mcp_parity.py` compares methods and paths, not argument shapes.

Run over the same corpus of stored columns, the two versions answer identically for every
row naming only known scopes. Five rows moved, four of them towards refusal (`" "`, `" , "`,
`"readd"`, `"READ"`) and one towards a wider grant: `" admin"` now grants admin, where it
used to authenticate and satisfy nothing. That last one is pinned below so it stays a
decision rather than a side effect.

`CommentIsTrue` is about a sentence rather than a request. The field constraint was
justified by "so that `minItems: 1` reaches the published schema, where the MCP bridge and
any other client read it before sending anything", and a normal install publishes no
schema: `DEBUG` is false by default, so `openapi_url` is None. The sentence told the next
reader that the bridge was covered by the API model, which is the reason the bridge kept
its own unconstrained signature.

Its replacement claimed something else that was not true: that the minimum had to sit on
the field because "only something that sees the list itself can refuse an empty one", which
a `field_validator` also does -- it is handed the whole list. Measured here rather than
argued: two models, one refusing `[]` from the field and one from a validator, both refuse
it, and only the first carries `minItems` into `model_json_schema()`. That is the whole of
the difference, and it is the difference `BridgeSignatureParity` below reads.

`ScopeReadingIsCoupled` pins the seam between the two files. `app/auth.py` weighs the list
that `app/api/api_keys.py` hands it and never reads the column itself, so the reading has
to keep normalizing. Nothing else in the tree would notice that call leaving.
"""

import ast
import hashlib
import os
import tempfile
import tokenize
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastmcp.tools import Tool
from pydantic import BaseModel, Field, ValidationError, field_validator

import app.auth as auth
import app.db as app_db
from app import models
from app.api.api_keys import ApiKeyCreate, _split_scopes, verify_api_key
from vauxtra_mcp import client as mcp_client
from vauxtra_mcp.tools.admin import create_api_key as bridge_create_api_key

PASSWORD = "test-password-long-enough"
REPO_ROOT = Path(__file__).resolve().parent.parent

# `require_auth(request)` with no scope: the shape that never consults the granted scopes,
# and the one every residue here walked through.
UNSCOPED_ROUTE = "/api/environments"
# `require_auth(request, scope="admin")`: reached only through `_scope_satisfies`.
ADMIN_ROUTE = "/api/settings/api-keys"

# name -> the raw `scopes` column, exactly as SQLite holds it.
ROWS = {
    "ro": "read",
    "adm": "admin",
    "spaced": " ",
    "separators": " , ",
    "unknown-word": "readd",
    "padded": "read, write ",
    "padded-admin": " admin",
}


def _executable_tokens(path: Path) -> list[tuple[int, str]]:
    """Every token of a module except comments and docstrings, as (line, text)."""
    source = path.read_text(encoding="utf-8")
    docstring_starts = set()
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_starts.add((first.lineno, first.col_offset))

    kept = []
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and token.start in docstring_starts:
                continue
            if token.type in (tokenize.NAME, tokenize.STRING, tokenize.OP, tokenize.NUMBER):
                kept.append((token.start[0], token.string))
    return kept


class BlankAndUnknownScopeRows(unittest.TestCase):
    """A temp database holding one row per shape, reached through real HTTP requests.

    `tests/` is not a package, so this fixture is duplicated per file by design.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "scope-residues.test.db")
        models.init_db()

        self._patchers = [
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
            for name, scopes in ROWS.items():
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

    # -- helpers ----------------------------------------------------------------

    def _headers(self, name: str) -> dict:
        return {"Authorization": f"Bearer key-{name}"}

    def _listed(self, name: str) -> list[str]:
        """The scopes the key list draws for that row, asked as the admin key."""
        resp = self.client.get(ADMIN_ROUTE, headers=self._headers("adm"))
        self.assertEqual(resp.status_code, 200, resp.text)
        matching = [k for k in resp.json() if k["name"] == name]
        self.assertEqual(len(matching), 1, f"expected exactly one row named {name}: {resp.text}")
        return matching[0]["scopes"]

    # -- residue 2: a column holding blanks --------------------------------------

    def test_a_column_holding_one_space_is_read_as_no_scope_at_all(self):
        self.assertEqual(_split_scopes(" "), [])

    def test_a_column_holding_separators_alone_is_read_as_no_scope_at_all(self):
        self.assertEqual(_split_scopes(" , "), [])

    def test_a_key_whose_column_holds_one_space_opens_no_unscoped_route(self):
        """The original hole, reproduced by a space instead of by an empty column."""
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("spaced"))
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_a_key_whose_column_holds_separators_opens_no_unscoped_route(self):
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("separators"))
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_a_row_of_blanks_is_listed_as_no_permission(self):
        """`[" "]` draws a badge with nothing written on it; `[]` says what was granted."""
        self.assertEqual(self._listed("spaced"), [])
        self.assertEqual(self._listed("separators"), [])

    # -- residue 3: a scope this build does not know ------------------------------

    def test_a_key_granted_only_an_unknown_scope_opens_no_unscoped_route(self):
        """It satisfies no scoped route, so the routes that ask for none must refuse it too."""
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("unknown-word"))
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_a_key_granted_only_an_unknown_scope_opens_no_scoped_route_either(self):
        resp = self.client.get(ADMIN_ROUTE, headers=self._headers("unknown-word"))
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_the_unknown_scope_is_still_printed_in_the_key_list(self):
        """Refusing it is not hiding it: the operator has to see what the row holds to fix it."""
        self.assertEqual(self._listed("unknown-word"), ["readd"])

    def test_one_unknown_scope_next_to_a_known_one_is_not_a_refusal(self):
        """The guard asks whether anything is usable, not whether everything is."""
        conn = models.get_db()
        try:
            conn.execute("UPDATE api_keys SET scopes=? WHERE name=?", ("readd,read", "unknown-word"))
            conn.commit()
        finally:
            conn.close()
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("unknown-word"))
        self.assertEqual(resp.status_code, 200, resp.text)

    # -- positive witnesses -------------------------------------------------------

    def test_a_read_key_still_reaches_the_unscoped_route(self):
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("ro"))
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_a_read_key_is_refused_on_the_admin_route_by_scope_not_by_rejection(self):
        """403, not 401: the key authenticates, and the scope check is what turns it away."""
        resp = self.client.get(ADMIN_ROUTE, headers=self._headers("ro"))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_an_admin_key_still_reaches_the_admin_route(self):
        resp = self.client.get(ADMIN_ROUTE, headers=self._headers("adm"))
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_real_scopes_survive_the_read_untouched(self):
        self.assertEqual(self._listed("ro"), ["read"])
        self.assertEqual(self._listed("adm"), ["admin"])

    def test_a_padded_column_keeps_every_scope_it_names(self):
        """Stripping drops blanks, not the scopes written next to them."""
        self.assertEqual(_split_scopes("read, write "), ["read", "write"])
        self.assertEqual(self._listed("padded"), ["read", "write"])
        resp = self.client.get(UNSCOPED_ROUTE, headers=self._headers("padded"))
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_a_padded_scope_grants_what_an_unpadded_one_grants(self):
        """The one row this change answers more widely, pinned so it stays deliberate.

        A column holding `" admin"` used to authenticate (200 on the unscoped route) and
        satisfy nothing (403 on the admin route): the key existed, its permission did not.
        Every other comma-split in the repository strips its segments, and the operator who
        wrote that column wrote one scope.
        """
        self.assertEqual(self._listed("padded-admin"), ["admin"])
        resp = self.client.get(ADMIN_ROUTE, headers=self._headers("padded-admin"))
        self.assertEqual(resp.status_code, 200, resp.text)


class ScopeReadingIsCoupled(unittest.TestCase):
    """The guard in `app/auth.py` weighs a list built in `app/api/api_keys.py`.

    Two files, one decision. `_get_auth_context` never reads the `scopes` column: it takes
    whatever `verify_api_key` returns, and `verify_api_key` is what normalizes it. Dropping
    that normalization would not raise anything -- it would quietly change what the guard is
    asked about. Both halves of the seam are pinned here so that removing either one fails a
    test that says why.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR)
        models.DATA_DIR = app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = app_db.DB_PATH = os.path.join(self._tmpdir.name, "scope-seam.test.db")
        models.init_db()
        conn = models.get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                       prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                       created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
            )
            conn.execute(
                "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
                ("padded", hashlib.sha256(b"key-padded").hexdigest(), "padded", "read, write "),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        models.DB_PATH, models.DATA_DIR, app_db.DB_PATH, app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    @staticmethod
    def _calls_in(path: Path, function_name: str) -> set[str]:
        """Every plain-name call inside that function, read from the AST."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        return {
            node.func.id
            for node in ast.walk(target)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    def test_the_reader_still_normalizes_what_the_guard_will_weigh(self):
        self.assertIn(
            "_split_scopes",
            self._calls_in(REPO_ROOT / "app" / "api" / "api_keys.py", "verify_api_key"),
            "verify_api_key stopped normalizing the scopes column; the guard in app/auth.py "
            "weighs whatever this returns and reads no column of its own",
        )

    def test_the_guard_still_takes_its_scopes_from_that_reader(self):
        self.assertIn(
            "verify_api_key",
            self._calls_in(REPO_ROOT / "app" / "auth.py", "_get_auth_context"),
            "the bearer-token branch stopped going through verify_api_key; the normalization "
            "it applies is the reason a blank column reaches the guard as an empty list",
        )

    def test_the_scope_check_reads_exactly_what_the_reader_returns(self):
        """The runtime half: an unnormalized `" write"` satisfies no requirement at all."""
        scopes = verify_api_key("key-padded")["scopes"]
        self.assertEqual(scopes, ["read", "write"])
        self.assertTrue(auth._scope_satisfies(scopes, "write"))
        self.assertFalse(auth._scope_satisfies([" write"], "write"))


class _ConstraintOnTheField(BaseModel):
    """`ApiKeyCreate.scopes` as it ships: the minimum declared on the field."""

    scopes: list[Literal["read", "write", "admin"]] = Field(default=["read"], min_length=1)


class _RefusalInTheValidator(BaseModel):
    """The same refusal written where the comment used to say it could not be written."""

    scopes: list[Literal["read", "write", "admin"]] = Field(default=["read"])

    @field_validator("scopes")
    @classmethod
    def val_scopes(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one scope is required")
        return v


class CommentIsTrue(unittest.TestCase):
    """A normal install publishes no schema, so no client reads the field constraint."""

    def test_debug_is_off_by_default_so_no_schema_is_published(self):
        import app.config as config

        self.assertFalse(config.DEBUG)
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DEBUG=false", env_example)

        import app.main as app_main

        self.assertIsNone(app_main.app.openapi_url)
        self.assertIsNone(app_main.app.docs_url)

    def test_the_bridge_reads_no_schema(self):
        """It declares its tools by hand, so the API model constrains nothing on its side.

        Comments and docstrings are dropped before the search -- two of them now say that
        the bridge reads no schema, and a detector that fires on the sentence describing it
        would answer the same whether or not the code ever changed.
        """
        mentions = [
            f"{path.relative_to(REPO_ROOT)}:{line}"
            for path in (REPO_ROOT / "vauxtra_mcp").rglob("*.py")
            for line, text in _executable_tokens(path)
            if "openapi" in text.lower()
        ]
        self.assertEqual(mentions, [])

    def test_every_bridge_tool_is_declared_by_hand(self):
        """The other half of the same claim: the tool list is written, not derived."""
        admin = REPO_ROOT / "vauxtra_mcp" / "tools" / "admin.py"
        tree = ast.parse(admin.read_text(encoding="utf-8"))
        decorated = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
                for d in node.decorator_list
            )
        ]
        self.assertIn("create_api_key", decorated)

    def test_the_field_comment_no_longer_claims_a_published_schema(self):
        """The sentence that said clients read `minItems` before sending anything."""
        source = (REPO_ROOT / "app" / "api" / "api_keys.py").read_text(encoding="utf-8")
        self.assertNotIn("published schema", source)

    def test_the_field_comment_no_longer_claims_the_validator_is_blind(self):
        """The sentence that said only the field constraint can see the list at all."""
        source = (REPO_ROOT / "app" / "api" / "api_keys.py").read_text(encoding="utf-8")
        self.assertNotIn("sees the list itself", source)

    def test_a_validator_refuses_an_empty_list_just_as_well(self):
        """Both halves of the claim the comment now makes, measured on the spot.

        A `field_validator` is handed the whole list, so the refusal reads the same from
        either place. What differs is where it lands: only the field constraint reaches
        the generated document, which is the one thing the parity test above can compare.
        """
        for model in (_ConstraintOnTheField, _RefusalInTheValidator):
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model(scopes=[])

        on_the_field = _ConstraintOnTheField.model_json_schema()["properties"]["scopes"]
        in_the_validator = _RefusalInTheValidator.model_json_schema()["properties"]["scopes"]
        self.assertEqual(on_the_field.get("minItems"), 1)
        self.assertIsNone(in_the_validator.get("minItems"))
        self.assertEqual(
            {k: v for k, v in on_the_field.items() if k not in ("title", "minItems")},
            {k: v for k, v in in_the_validator.items() if k != "title"},
            "the two documents should differ by the `minItems` line and nothing else",
        )

    def test_the_real_model_carries_the_constraint_on_the_field(self):
        """The measurement above is about the shipped field, not a lookalike."""
        self.assertEqual(
            ApiKeyCreate.model_json_schema()["properties"]["scopes"].get("minItems"), 1
        )
        with self.assertRaises(ValidationError):
            ApiKeyCreate(name="n", scopes=[])


class BridgeSignatureParity(unittest.TestCase):
    """The bridge repeats the API model's scope constraint, because nothing derives it."""

    @staticmethod
    def _api_schema() -> dict:
        schema = ApiKeyCreate.model_json_schema()["properties"]["scopes"]
        return {k: v for k, v in schema.items() if k != "title"}

    @staticmethod
    def _bridge_schema() -> dict:
        return Tool.from_function(bridge_create_api_key).parameters["properties"]["scopes"]

    def test_the_bridge_demands_at_least_one_scope_like_the_api_model(self):
        self.assertEqual(self._bridge_schema().get("minItems"), 1)
        self.assertEqual(self._api_schema().get("minItems"), 1)

    def test_the_bridge_offers_the_same_default_as_the_api_model(self):
        self.assertEqual(self._bridge_schema().get("default"), ["read"])
        self.assertEqual(self._api_schema().get("default"), ["read"])

    def test_the_two_scope_declarations_are_the_same_document(self):
        """Names, minimum and default at once: one assertion the next drift cannot pass."""
        self.assertEqual(self._bridge_schema(), self._api_schema())

    def test_scopes_is_optional_on_both_sides(self):
        """A caller who says nothing gets `read`, on either road in."""
        self.assertNotIn("scopes", Tool.from_function(bridge_create_api_key).parameters["required"])
        self.assertNotIn("scopes", ApiKeyCreate.model_json_schema().get("required", []))


class _FakeResponse:
    """Stands in for the API answer, so nothing here opens a socket."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class BridgeRefusesAnEmptyScopeList(unittest.IsolatedAsyncioTestCase):
    """The parity above, exercised: what the tool does with the arguments a model sends.

    `Tool.from_function` builds the call wrapper the MCP server exposes, so the validation
    met here is the validation a model meets. Measured against the signature this replaces,
    `{"name": ..., "scopes": []}` ran the body and posted the empty list to the API.
    """

    def setUp(self) -> None:
        self.tool = Tool.from_function(bridge_create_api_key)
        self.sent: list[dict] = []

        def _post(path, json=None, **_kwargs):
            self.sent.append({"path": path, "json": json})
            return _FakeResponse({"id": 1, "name": json["name"], "scopes": json["scopes"]})

        self._patchers = [
            patch.object(mcp_client, "post", _post),
            patch.object(mcp_client, "check", lambda response: response),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()

    async def test_an_empty_scope_list_never_reaches_the_api(self):
        with self.assertRaises(Exception) as caught:
            await self.tool.run({"name": "monitoring", "scopes": []})
        self.assertIn("at least 1 item", str(caught.exception))
        self.assertEqual(self.sent, [], "the bridge posted a scope list the API would refuse")

    async def test_omitting_the_scopes_sends_the_default_the_api_model_carries(self):
        """Positive witness: optional means defaulted, not absent."""
        await self.tool.run({"name": "dashboard"})
        self.assertEqual([call["json"]["scopes"] for call in self.sent], [["read"]])

    async def test_a_real_scope_list_is_passed_through_untouched(self):
        """Positive witness: the minimum refuses the empty list and nothing else."""
        await self.tool.run({"name": "operator", "scopes": ["read", "write"]})
        self.assertEqual([call["json"]["scopes"] for call in self.sent], [["read", "write"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
