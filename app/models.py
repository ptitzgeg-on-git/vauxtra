import sqlite3
import os
from contextlib import contextmanager
from app.config import DATA_DIR, DB_PATH  # noqa: F401 — re-exported for test patching
from app.db import get_connection

# Increment this constant whenever a new ALTER is added to _migrate().
# The value is stored in the settings table and logged on startup.
SCHEMA_VERSION = 7


def get_db():
    """Return a SQLite database connection."""
    return get_connection()


@contextmanager
def get_db_ctx():
    """Context manager that guarantees connection cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS providers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            type        TEXT    NOT NULL,
            url         TEXT    NOT NULL,
            username    TEXT    NOT NULL DEFAULT '',
            password    TEXT    NOT NULL DEFAULT '',
            extra       TEXT    NOT NULL DEFAULT '{}',
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS services (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            subdomain         TEXT    NOT NULL,
            domain            TEXT    NOT NULL,
            target_ip         TEXT    NOT NULL,
            target_port       INTEGER NOT NULL,
            forward_scheme    TEXT    NOT NULL DEFAULT 'http',
            websocket         INTEGER NOT NULL DEFAULT 0,
            dns_provider_id   INTEGER REFERENCES providers(id) ON DELETE SET NULL,
            proxy_provider_id INTEGER REFERENCES providers(id) ON DELETE SET NULL,
            tunnel_provider_id INTEGER REFERENCES providers(id) ON DELETE SET NULL,
            expose_mode       TEXT    NOT NULL DEFAULT 'proxy_dns',
            public_target_mode TEXT   NOT NULL DEFAULT 'manual',
            auto_update_dns   INTEGER NOT NULL DEFAULT 0,
            tunnel_hostname   TEXT    NOT NULL DEFAULT '',
            dns_ip            TEXT    NOT NULL DEFAULT '',
            npm_host_id       INTEGER,
            enabled           INTEGER NOT NULL DEFAULT 1,
            status            TEXT    NOT NULL DEFAULT 'unknown',
            last_checked      TEXT,
            created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tags (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            color      TEXT    NOT NULL DEFAULT 'blue',
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS service_tags (
            service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            tag_id     INTEGER NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
            PRIMARY KEY (service_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            level      TEXT    NOT NULL DEFAULT 'info',
            message    TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS uptime_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            status     TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS domains (
            name TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS docker_endpoints (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            docker_host TEXT    NOT NULL UNIQUE,
            enabled     INTEGER NOT NULL DEFAULT 1,
            is_default  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS environments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            color      TEXT    NOT NULL DEFAULT 'blue',
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS service_environments (
            service_id     INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            environment_id INTEGER NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
            PRIMARY KEY (service_id, environment_id)
        );

        CREATE TABLE IF NOT EXISTS webhooks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            url        TEXT    NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS service_alerts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id       INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            webhook_id       INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
            on_up            INTEGER NOT NULL DEFAULT 1,
            on_down          INTEGER NOT NULL DEFAULT 1,
            min_down_minutes INTEGER NOT NULL DEFAULT 0,
            UNIQUE(service_id, webhook_id)
        );

        CREATE TABLE IF NOT EXISTS service_push_targets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
            provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
            role        TEXT    NOT NULL CHECK(role IN ('proxy', 'dns')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(service_id, provider_id, role)
        );

        CREATE TABLE IF NOT EXISTS scheduler_state (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    _migrate(conn)
    _update_schema_version(conn)
    conn.commit()
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    for sql in [
        "ALTER TABLE providers ADD COLUMN extra TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE services ADD COLUMN status       TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE services ADD COLUMN last_checked TEXT",
        "ALTER TABLE services ADD COLUMN environment  TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE services ADD COLUMN icon_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE services ADD COLUMN tunnel_provider_id INTEGER REFERENCES providers(id) ON DELETE SET NULL",
        "ALTER TABLE services ADD COLUMN expose_mode TEXT NOT NULL DEFAULT 'proxy_dns'",
        "ALTER TABLE services ADD COLUMN public_target_mode TEXT NOT NULL DEFAULT 'manual'",
        "ALTER TABLE services ADD COLUMN auto_update_dns INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE services ADD COLUMN tunnel_hostname TEXT NOT NULL DEFAULT ''",
    ]:
        try:
            conn.execute(sql)
        except Exception as e:
            # "duplicate column name" errors are expected on repeated startups (idempotent ALTERs).
            msg = str(e).lower()
            if "duplicate column name" not in msg and "already exists" not in msg:
                import traceback
                add_log("error", f"Unexpected migration error: {e}\n{traceback.format_exc()}")

    # Ensure default docker endpoint exists
    default_host = (os.getenv("DOCKER_HOST") or "unix:///var/run/docker.sock").strip() or "unix:///var/run/docker.sock"
    endpoint_count = conn.execute("SELECT COUNT(*) FROM docker_endpoints").fetchone()[0]
    if endpoint_count == 0:
        conn.execute(
            "INSERT INTO docker_endpoints (name, docker_host, enabled, is_default) VALUES (?,?,1,1)",
            ("Local Docker", default_host),
        )
    else:
        default_exists = conn.execute("SELECT 1 FROM docker_endpoints WHERE is_default=1").fetchone()
        if not default_exists:
            first = conn.execute("SELECT id FROM docker_endpoints ORDER BY id LIMIT 1").fetchone()
            if first:
                conn.execute("UPDATE docker_endpoints SET is_default=1 WHERE id=?", (first["id"],))

    _migrate_encrypt_passwords(conn)


def _update_schema_version(conn: sqlite3.Connection) -> None:
    """Store the current schema version in settings for diagnostics."""
    existing = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    elif int(existing["value"]) != SCHEMA_VERSION:
        conn.execute(
            "UPDATE settings SET value=? WHERE key='schema_version'",
            (str(SCHEMA_VERSION),),
        )


def _migrate_encrypt_passwords(conn: sqlite3.Connection) -> None:
    """Encrypt any plaintext provider passwords still in the database (one-time migration)."""
    from app.config import fernet, encrypt_secret
    rows = conn.execute("SELECT id, password FROM providers WHERE password != ''").fetchall()
    for row in rows:
        pwd = row["password"]
        try:
            fernet.decrypt(pwd.encode())
            # Already encrypted, nothing to do
        except Exception:
            # Plaintext → encrypt
            conn.execute(
                "UPDATE providers SET password=? WHERE id=?",
                (encrypt_secret(pwd), row["id"]),
            )


def is_setup_done() -> bool:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    conn.close()
    return count > 0


def add_log(level: str, message: str, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = get_db()
    conn.execute("INSERT INTO logs (level, message) VALUES (?, ?)", (level, message))
    if own:
        conn.commit()
        conn.close()


def parse_tags(tags_raw: str | None) -> list[dict]:
    if not tags_raw:
        return []
    result = []
    for chunk in tags_raw.split(","):
        parts = chunk.split(":")
        if len(parts) == 3:
            result.append({"name": parts[0], "color": parts[1], "id": int(parts[2])})
    return result


def parse_environments(envs_raw: str | None) -> list[dict]:
    if not envs_raw:
        return []
    result = []
    for chunk in envs_raw.split(","):
        parts = chunk.split(":")
        if len(parts) == 3:
            result.append({"name": parts[0], "color": parts[1], "id": int(parts[2])})
    return result


def row_to_service(row, tags_raw: str | None = None) -> dict:
    """Convert a services DB row to a serializable dict with parsed tags/environments."""
    d = dict(row)
    d["tags"]         = parse_tags(tags_raw or d.pop("tags_raw", None))
    d["environments"] = parse_environments(d.pop("envs_raw", None))
    return d


def set_tags(conn: sqlite3.Connection, service_id: int, tag_ids: list[int]) -> None:
    """Replace all tags for a service."""
    conn.execute("DELETE FROM service_tags WHERE service_id=?", (service_id,))
    for tid in tag_ids:
        conn.execute(
            "INSERT OR IGNORE INTO service_tags (service_id, tag_id) VALUES (?,?)",
            (service_id, tid),
        )


def set_environments(conn: sqlite3.Connection, service_id: int, env_ids: list[int]) -> None:
    """Replace all environment assignments for a service."""
    conn.execute("DELETE FROM service_environments WHERE service_id=?", (service_id,))
    for eid in env_ids:
        conn.execute(
            "INSERT OR IGNORE INTO service_environments (service_id, environment_id) VALUES (?,?)",
            (service_id, eid),
        )


def set_push_targets(
    conn: sqlite3.Connection,
    service_id: int,
    proxy_provider_ids: list[int],
    dns_provider_ids: list[int],
) -> None:
    """Replace extra push targets for a service."""
    conn.execute("DELETE FROM service_push_targets WHERE service_id=?", (service_id,))

    for pid in proxy_provider_ids:
        conn.execute(
            "INSERT OR IGNORE INTO service_push_targets (service_id, provider_id, role) VALUES (?,?,?)",
            (service_id, int(pid), "proxy"),
        )

    for pid in dns_provider_ids:
        conn.execute(
            "INSERT OR IGNORE INTO service_push_targets (service_id, provider_id, role) VALUES (?,?,?)",
            (service_id, int(pid), "dns"),
        )
