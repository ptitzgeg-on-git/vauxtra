"""
API key management — create, list, revoke.

Keys are generated with secrets.token_urlsafe(32), stored as SHA-256 hashes.
The full key is returned only at creation time; only the first 10 chars (prefix)
are kept for display purposes -- the `vx_` marker plus seven characters of the
token, which is what the key list prints next to each name.
"""
import hashlib
import secrets
import string
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.auth import require_auth
from app.limiter import limiter
from app.models import add_log, get_db

router = APIRouter()

VALID_SCOPES = frozenset({"read", "write", "admin"})

# The scope vocabulary is written down three times -- here, in the `Literal[...]` on
# `ApiKeyCreate.scopes` below, and as the keys of `_SCOPE_LEVEL` in `app/auth.py`, which is
# the only one of the three that decides anything at request time.
# `tests/test_api_key_scope_vocabulary.py` compares all three as sets.

# The padding `_split_scopes` removes from a stored scope, and the whole of it: space, tab,
# line feed, carriage return, vertical tab, form feed. Named rather than left to a bare
# `str.strip()`, whose boundary runs through the Unicode blanks and cannot be stated in a
# sentence -- see `_split_scopes` for what that cost.
_SCOPE_PADDING = string.whitespace


class ApiKeyCreate(BaseModel):
    name: str
    # Refused rather than normalized. An empty list used to pass -- `val_scopes` checks the
    # values by looping over them, and an empty list has nothing to loop over -- and was
    # stored as an empty column, read back as the scope `""`: a permission no route grants,
    # drawn in the key list as a blank badge. Falling back to `["read"]` instead would hand
    # out a permission the caller never asked for, silently, which is the worse of the two.
    # The constraint sits on the field rather than in the validator because of where it
    # lands, not because of what each one can see: `val_scopes` is a `field_validator` and is
    # handed the whole list, so `if not v: raise ValueError(...)` there would refuse `[]`
    # just as well. What a raised `ValueError` cannot do is appear in
    # `ApiKeyCreate.model_json_schema()`. Measured: with the minimum on the field, the
    # `scopes` property reads `{"default": ["read"], "items": {"enum": ["read", "write",
    # "admin"], "type": "string"}, "minItems": 1, "type": "array"}`; with the same refusal
    # moved into the validator the property is the same document without the `minItems` line.
    # `tests/test_api_key_scope_residues.py::BridgeSignatureParity` compares that generated
    # document field by field against the MCP bridge's own tool parameters, so a constraint
    # the schema cannot carry is a constraint that comparison cannot see.
    # That document is generated on demand, and it guards the request that arrives here and
    # nothing upstream: `DEBUG` is false by default (`app/config.py`, `.env.example`), so
    # `openapi_url` is None and `GET /openapi.json` answers the SPA index page -- a normal
    # install publishes no schema for a client to read. The MCP bridge reads none either; it
    # declares its tools by hand with FastMCP decorators, so the same default and the same
    # minimum have to be written again in `vauxtra_mcp.tools.admin.create_api_key`, and
    # `tests/test_api_key_scope_residues.py` fails if the two drift apart.
    scopes: list[Literal["read", "write", "admin"]] = Field(default=["read"], min_length=1)

    @field_validator("name")
    @classmethod
    def val_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 64:
            raise ValueError("Name must be 1–64 characters")
        return v

    @field_validator("scopes")
    @classmethod
    def val_scopes(cls, v: list[str]) -> list[str]:
        for s in v:
            if s not in VALID_SCOPES:
                raise ValueError(f"Invalid scope: {s}. Allowed: {sorted(VALID_SCOPES)}")
        return list(set(v))


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _split_scopes(stored: str) -> list[str]:
    """Read the `scopes` column, dropping the segments that name nothing.

    Creation refuses an empty scope list now, but the rows written while it did not are
    still in the table, and `"".split(",")` turns an empty column into `[""]` -- one scope,
    named nothing. Returning `[]` says what was actually granted: the key list shows no
    permission instead of a blank badge, and the scope check is asked about nothing rather
    than about a scope that does not exist.

    Segments are stripped before they are weighed, as they are in `app/security.py`,
    `app/public_target.py`, `app/api/settings.py`, `app/api/providers.py`,
    `app/providers/factory.py` and `app/services/docker_analyzer.py`. The two splits that do
    not strip (`app/models.py`) read a `GROUP_CONCAT` of `name:color:id` triples, which
    SQLite writes without spaces. Measured before this line stripped: `_split_scopes(" ")`
    answered `[" "]` and `_split_scopes(" , ")` answered `[" ", " "]` -- a non-empty list of
    scopes named with a space, which is a blank badge in the key list again and, for
    `require_auth(request)`, a caller who has been granted something.

    What counts as padding is `_SCOPE_PADDING` and nothing else. A bare `str.strip()` was
    what this line used to call, and the boundary it drew was not a rule anyone could state:
    measured on this build, a column holding U+0009, U+000A, U+0020, U+00A0 or U+2007 in
    front of `admin` granted admin, while the same column holding U+200B in front of it
    granted nothing. Two invisible prefixes opened the admin routes and a third did not, and
    no line in the source said which was which -- the line was drawn by whichever code
    points the Unicode tables happen to mark as a blank.

    The rule now written down is "a blank a keyboard or a shell produced": the six ASCII
    ones. A no-break space (U+00A0) and a figure space (U+2007) are not that. They arrive by
    pasting out of rendered text, and a permission column is the wrong place to guess what a
    paste meant, so they stay part of the token: the scope becomes a word this build has
    never heard of, `_get_auth_context` refuses the key, and the warning it logs prints the
    stored value, which is how the operator finds out the column holds something invisible.
    U+200B was already refused before this, by accident; it is refused on purpose now.
    `" admin"` still grants admin -- ASCII, already a decision, pinned in
    `tests/test_api_key_scope_residues.py`. Every code point above is walked in
    `tests/test_api_key_scope_vocabulary.py`.
    """
    return [
        scope
        for scope in (segment.strip(_SCOPE_PADDING) for segment in stored.split(","))
        if scope
    ]


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            key_hash     TEXT    NOT NULL UNIQUE,
            prefix       TEXT    NOT NULL,
            scopes       TEXT    NOT NULL DEFAULT 'read',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT
        )
    """)
    conn.commit()


@router.get("/api/settings/api-keys")
def list_api_keys(request: Request):
    require_auth(request, scope="admin")
    conn = get_db()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT id, name, prefix, scopes, created_at, last_used_at FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "prefix": r["prefix"],
                "scopes": _split_scopes(r["scopes"]),
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/api/settings/api-keys", status_code=201)
@limiter.limit("10/minute")
def create_api_key(request: Request, body: ApiKeyCreate):
    require_auth(request, scope="admin")
    conn = get_db()
    try:
        _ensure_table(conn)

        raw_key = f"vx_{secrets.token_urlsafe(32)}"
        key_hash = _hash_key(raw_key)
        prefix = raw_key[:10]
        scopes_str = ",".join(sorted(body.scopes))

        conn.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
            (body.name, key_hash, prefix, scopes_str),
        )
        conn.commit()
        key_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        row = conn.execute(
            "SELECT id, name, prefix, scopes, created_at, last_used_at FROM api_keys WHERE id=?", (key_id,)
        ).fetchone()
    finally:
        conn.close()

    add_log("info", f"API key created: {body.name} (scopes: {scopes_str})")

    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "scopes": _split_scopes(row["scopes"]),
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "key": raw_key,
    }


@router.delete("/api/settings/api-keys/{key_id}")
def revoke_api_key(request: Request, key_id: int):
    require_auth(request, scope="admin")
    conn = get_db()
    try:
        _ensure_table(conn)
        row = conn.execute("SELECT name FROM api_keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            raise HTTPException(404, "API key not found")
        conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        conn.commit()
    finally:
        conn.close()
    add_log("info", f"API key revoked: {row['name']}")
    return {"ok": True}


def verify_api_key(key: str) -> dict | None:
    """Return the key row (id, name, scopes) if valid, or None."""
    conn = get_db()
    try:
        _ensure_table(conn)
        key_hash = _hash_key(key)
        row = conn.execute(
            "SELECT id, name, scopes FROM api_keys WHERE key_hash=?", (key_hash,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE api_keys SET last_used_at=datetime('now') WHERE id=?", (row["id"],)
            )
            conn.commit()
            return {"id": row["id"], "name": row["name"], "scopes": _split_scopes(row["scopes"])}
        return None
    finally:
        conn.close()
