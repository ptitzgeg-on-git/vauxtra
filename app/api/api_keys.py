"""
API key management — create, list, revoke.

Keys are generated with secrets.token_urlsafe(32), stored as SHA-256 hashes.
The full key is returned only at creation time; only the first 8 chars (prefix)
are kept for display purposes.
"""
import hashlib
import secrets
from typing import Literal

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator

from app.models import get_db, add_log
from app.auth import require_auth
from app.limiter import limiter

router = APIRouter()

VALID_SCOPES = frozenset({"read", "write", "admin"})


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[Literal["read", "write", "admin"]] = ["read"]

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
                "scopes": r["scopes"].split(","),
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/api/settings/api-keys")
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
        "scopes": row["scopes"].split(","),
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
            return {"id": row["id"], "name": row["name"], "scopes": row["scopes"].split(",")}
        return None
    finally:
        conn.close()
