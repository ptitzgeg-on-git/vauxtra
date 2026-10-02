import os
import sqlite3
from contextlib import contextmanager

from app.config import DATA_DIR, DB_PATH  # noqa: F401 (re-exported for test patching)
from app.db import get_connection
from app.security import redact_query_secrets

# Bump whenever a statement is added to `_MIGRATIONS`; it is the only record of which
# schema a database has. tests/test_regressions_v2.py pins the pair.
SCHEMA_VERSION = 12


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
    """Create the schema and run pending migrations. Idempotent; runs at every startup."""
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
            scope_type TEXT    NOT NULL DEFAULT 'all',
            scope_ref_id INTEGER,
            repeat_interval_minutes INTEGER NOT NULL DEFAULT 0,
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

        CREATE TABLE IF NOT EXISTS service_templates (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT    NOT NULL,
            description        TEXT    NOT NULL DEFAULT '',
            forward_scheme     TEXT    NOT NULL DEFAULT 'http',
            target_port        INTEGER,
            websocket          INTEGER NOT NULL DEFAULT 0,
            expose_mode        TEXT    NOT NULL DEFAULT 'proxy_dns',
            proxy_provider_id  INTEGER REFERENCES providers(id) ON DELETE SET NULL,
            dns_provider_id    INTEGER REFERENCES providers(id) ON DELETE SET NULL,
            tunnel_provider_id INTEGER REFERENCES providers(id) ON DELETE SET NULL,
            public_target_mode TEXT    NOT NULL DEFAULT 'manual',
            domain             TEXT    NOT NULL DEFAULT '',
            dns_ip             TEXT    NOT NULL DEFAULT '',
            tag_ids_json       TEXT    NOT NULL DEFAULT '[]',
            environment_ids_json TEXT  NOT NULL DEFAULT '[]',
            icon_url           TEXT    NOT NULL DEFAULT '',
            created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS webhook_delivery_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id    INTEGER REFERENCES webhooks(id) ON DELETE CASCADE,
            url           TEXT    NOT NULL,
            title         TEXT    NOT NULL DEFAULT '',
            body          TEXT    NOT NULL DEFAULT '',
            status        TEXT    NOT NULL DEFAULT 'pending',
            attempt       INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            error_msg     TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)
    _migrate(conn)
    _update_schema_version(conn)
    conn.commit()
    conn.close()


def ensure_default_docker_endpoint(conn: sqlite3.Connection) -> None:
    """Guarantee exactly one default Docker endpoint. Does not commit.

    Called at startup and after a reset, which empties docker_endpoints.
    """
    default_host = (
        os.getenv("DOCKER_HOST") or "unix:///var/run/docker.sock"
    ).strip() or "unix:///var/run/docker.sock"

    if conn.execute("SELECT COUNT(*) FROM docker_endpoints").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO docker_endpoints (name, docker_host, enabled, is_default) VALUES (?,?,1,1)",
            ("Local Docker", default_host),
        )
        return

    if not conn.execute("SELECT 1 FROM docker_endpoints WHERE is_default=1").fetchone():
        first = conn.execute("SELECT id FROM docker_endpoints ORDER BY id LIMIT 1").fetchone()
        if first:
            conn.execute("UPDATE docker_endpoints SET is_default=1 WHERE id=?", (first["id"],))


# Replayed in full on every startup, so each statement has to be idempotent: the loop
# below swallows "duplicate column name" and re-raises nothing else into the journal.
_MIGRATIONS = [
    "ALTER TABLE providers ADD COLUMN extra TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE services ADD COLUMN status       TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE services ADD COLUMN last_checked TEXT",
    # services.environment (pre-1.1 free text) is dropped later; nothing reads it.
    "ALTER TABLE services ADD COLUMN icon_url TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE services ADD COLUMN tunnel_provider_id INTEGER REFERENCES providers(id) ON DELETE SET NULL",
    "ALTER TABLE services ADD COLUMN expose_mode TEXT NOT NULL DEFAULT 'proxy_dns'",
    "ALTER TABLE services ADD COLUMN public_target_mode TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE services ADD COLUMN auto_update_dns INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE services ADD COLUMN tunnel_hostname TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE webhooks ADD COLUMN alert_on_any_down INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE webhooks ADD COLUMN alert_on_any_up INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE webhooks ADD COLUMN alert_on_integration_down INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE webhooks ADD COLUMN alert_on_integration_up INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE webhooks ADD COLUMN min_down_minutes INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE webhooks ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'all'",
    "ALTER TABLE webhooks ADD COLUMN scope_ref_id INTEGER",
    "ALTER TABLE webhooks ADD COLUMN repeat_interval_minutes INTEGER NOT NULL DEFAULT 0",
    # The default reads older templates as naming no environment, which is what they held.
    "ALTER TABLE service_templates ADD COLUMN environment_ids_json TEXT NOT NULL DEFAULT '[]'",
]


def _migrate(conn: sqlite3.Connection) -> None:
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except Exception as e:
            # "duplicate column name" errors are expected on repeated startups (idempotent ALTERs).
            msg = str(e).lower()
            if "duplicate column name" not in msg and "already exists" not in msg:
                import traceback
                add_log("error", f"Unexpected migration error: {e}\n{traceback.format_exc()}")

    ensure_default_docker_endpoint(conn)

    _migrate_encrypt_passwords(conn)
    _backfill_auth_mode(conn)
    _drop_legacy_service_environment(conn)
    _purge_logged_webhook_urls(conn)
    _migrate_legacy_webhook_url(conn)
    _ensure_unique_service_hostnames(conn)
    _rebuild_webhook_delivery_log_fk(conn)
    _encrypt_webhook_urls(conn)


def _encrypt_webhook_urls(conn: sqlite3.Connection) -> None:
    """Encrypt notification URLs still stored in clear: an Apprise URL is its own credential.

    A value that is already a Fernet token is left alone even if this key cannot read it,
    so a restored database with the wrong key is not encrypted twice.
    """
    from app.config import encrypt_secret, is_encrypted
    for table in ("webhooks", "webhook_delivery_log"):
        rows = conn.execute(f"SELECT id, url FROM {table}").fetchall()  # noqa: S608 -- fixed names
        for row in rows:
            if row["url"] and not is_encrypted(row["url"]):
                conn.execute(
                    f"UPDATE {table} SET url=? WHERE id=?",  # noqa: S608 -- fixed names
                    (encrypt_secret(row["url"]), row["id"]),
                )


def _rebuild_webhook_delivery_log_fk(conn: sqlite3.Connection) -> None:
    """Add ON DELETE CASCADE from webhook_delivery_log.webhook_id to webhooks.

    SQLite cannot add a foreign key in place, so the table is rebuilt. Orphaned rows are
    dropped; rows with NULL webhook_id (ad-hoc sends) are kept. foreign_keys is toggled
    outside the transaction, statements run one by one so rollback works, and
    foreign_key_check runs before COMMIT. Idempotent: checks the pragma, not the version.
    """
    try:
        existing = conn.execute("PRAGMA foreign_key_list(webhook_delivery_log)").fetchall()
    except sqlite3.Error:
        return
    if any(row["table"] == "webhooks" for row in existing):
        return

    previous_isolation = conn.isolation_level
    conn.commit()
    conn.isolation_level = None  # drive BEGIN/COMMIT by hand
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """CREATE TABLE webhook_delivery_log_new (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    webhook_id    INTEGER REFERENCES webhooks(id) ON DELETE CASCADE,
                    url           TEXT    NOT NULL,
                    title         TEXT    NOT NULL DEFAULT '',
                    body          TEXT    NOT NULL DEFAULT '',
                    status        TEXT    NOT NULL DEFAULT 'pending',
                    attempt       INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    error_msg     TEXT    NOT NULL DEFAULT '',
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            conn.execute(
                """INSERT INTO webhook_delivery_log_new
                       (id, webhook_id, url, title, body, status, attempt,
                        next_retry_at, error_msg, created_at, updated_at)
                   SELECT id, webhook_id, url, title, body, status, attempt,
                          next_retry_at, error_msg, created_at, updated_at
                     FROM webhook_delivery_log
                    WHERE webhook_id IS NULL
                       OR webhook_id IN (SELECT id FROM webhooks)"""
            )
            dropped = conn.execute(
                """SELECT COUNT(*) FROM webhook_delivery_log
                    WHERE webhook_id IS NOT NULL
                      AND webhook_id NOT IN (SELECT id FROM webhooks)"""
            ).fetchone()[0]
            conn.execute("DROP TABLE webhook_delivery_log")
            conn.execute("ALTER TABLE webhook_delivery_log_new RENAME TO webhook_delivery_log")
            if conn.execute("PRAGMA foreign_key_check(webhook_delivery_log)").fetchall():
                raise sqlite3.IntegrityError(
                    "webhook_delivery_log still violates its new foreign key"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except Exception as exc:
        import traceback
        add_log("error", f"Could not add the webhook_delivery_log cascade: {exc}\n{traceback.format_exc()}")
        return
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = previous_isolation

    if dropped:
        add_log(
            "warn",
            f"Dropped {dropped} queued webhook deliveries addressed to webhooks that no "
            "longer exist. Vauxtra was still retrying them; it is not any more.",
        )


def _ensure_unique_service_hostnames(conn: sqlite3.Connection) -> None:
    """Add the unique index on service hostnames when the data allows it.

    With existing duplicates the instance still boots and logs which rows to merge.
    """
    dupes = conn.execute(
        """SELECT subdomain, domain, COUNT(*) AS n
             FROM services
            GROUP BY subdomain, domain
           HAVING n > 1
            ORDER BY domain, subdomain"""
    ).fetchall()
    if dupes:
        listed = ", ".join(f"{d['subdomain']}.{d['domain']} (x{d['n']})" for d in dupes)
        add_log(
            "warn",
            f"Duplicate service hostnames prevent the uniqueness index: {listed}. "
            "Merge or delete the extra rows -- until then two services can push over "
            "each other.",
        )
        return
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_services_hostname ON services (subdomain, domain)"
    )


def _drop_legacy_service_environment(conn: sqlite3.Connection) -> None:
    """Remove `services.environment`, superseded by the `service_environments` join.

    Skipped on SQLite older than 3.35 (no DROP COLUMN); nothing reads the column anyway.
    """
    try:
        conn.execute("ALTER TABLE services DROP COLUMN environment")
    except sqlite3.OperationalError:
        pass  # already gone, or a SQLite too old to drop it


def _migrate_legacy_webhook_url(conn: sqlite3.Connection) -> None:
    """Move the pre-1.1 `settings.webhook_url` into the webhooks table, where delivery reads.

    Creates one global webhook (enabled per `webhook_enabled`, notifying on down and up),
    then deletes both settings keys so this runs once.
    """
    row = conn.execute("SELECT value FROM settings WHERE key='webhook_url'").fetchone()
    url = ((row["value"] if row else "") or "").strip()
    if not url:
        # Nothing configured, or already migrated. Clear a lingering `webhook_enabled`.
        conn.execute("DELETE FROM settings WHERE key IN ('webhook_url', 'webhook_enabled')")
        return

    enabled_row = conn.execute(
        "SELECT value FROM settings WHERE key='webhook_enabled'"
    ).fetchone()
    enabled = 1 if (enabled_row and str(enabled_row["value"]).lower() == "true") else 0

    from app.config import decrypt_secret, encrypt_secret
    already = any(
        decrypt_secret(r["url"]) == url for r in conn.execute("SELECT url FROM webhooks").fetchall()
    )
    if not already:
        url = encrypt_secret(url)
        conn.execute(
            """INSERT INTO webhooks
               (name, url, enabled, scope_type, scope_ref_id, repeat_interval_minutes,
                alert_on_any_down, alert_on_any_up)
               VALUES (?,?,?,'all',NULL,0,1,1)""",
            ("Global notifications (migrated)", url, enabled),
        )
        add_log("info", "[Webhook] The global notification URL moved to the webhooks list -- "
                        "it now actually delivers; review its rules in Settings", conn)

    conn.execute("DELETE FROM settings WHERE key IN ('webhook_url', 'webhook_enabled')")


def _purge_logged_webhook_urls(conn: sqlite3.Connection) -> None:
    """Delete log lines that captured a full Apprise URL before URLs were masked.

    Runs once (marked in settings) and only touches `[Webhook]` lines containing `://`.
    """
    done = conn.execute(
        "SELECT 1 FROM settings WHERE key='webhook_log_purge_done'"
    ).fetchone()
    if done:
        return
    try:
        conn.execute(
            "DELETE FROM logs WHERE message LIKE '%[Webhook]%' AND message LIKE '%://%'"
        )
    except Exception as e:
        add_log("error", f"Could not purge logged webhook URLs: {e}", conn)
        return
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('webhook_log_purge_done', '1')"
    )


def _backfill_auth_mode(conn: sqlite3.Connection) -> None:
    """Stamp `auth_mode=password` on instances that already have a password hash.

    Otherwise existing protected installs would look like they never had a password.
    """
    has_hash = conn.execute(
        "SELECT 1 FROM settings WHERE key='app_password_hash' AND value != ''"
    ).fetchone()
    if not has_hash:
        return
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('auth_mode', 'password')"
    )


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
    """Encrypt any plaintext provider passwords still in the database.

    This runs at every start. It used to treat any value this key could not decrypt as
    plaintext, so one start with the wrong SECRET_KEY wrapped every password a second time,
    and putting the right key back did not undo it. A Fernet token is now left alone
    whichever key wrote it, as `_encrypt_webhook_urls` already does.
    """
    from app.config import encrypt_secret, is_encrypted
    rows = conn.execute("SELECT id, password FROM providers WHERE password != ''").fetchall()
    for row in rows:
        if not is_encrypted(row["password"]):
            conn.execute(
                "UPDATE providers SET password=? WHERE id=?",
                (encrypt_secret(row["password"]), row["id"]),
            )


def is_setup_done() -> bool:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    conn.close()
    return count > 0


# "warn" is folded into "warning" on insert so filters find every row.
_LEVEL_ALIASES = {"warn": "warning"}

# Stored log levels in reading order. /metrics zero-fills them so absent() alerts do not
# fire on a quiet instance; "warn" is an alias, not a member.
LOG_LEVELS = ("info", "ok", "warning", "error")

# Statuses of webhook_delivery_log rows, as written by the scheduler. /metrics zero-fills
# them for the same reason as LOG_LEVELS.
WEBHOOK_DELIVERY_STATUSES = ("pending", "delivered", "failed")


def normalise_log_level(level: str) -> str:
    """The spelling stored for `level`: lowercased, trimmed, `warn` folded into `warning`."""
    cleaned = (level or "").strip().lower()
    return _LEVEL_ALIASES.get(cleaned, cleaned)


def add_log(level: str, message: str, conn: sqlite3.Connection | None = None) -> None:
    """Insert a log row. Commits only when it opens its own connection (`conn` is None).

    Messages are passed through redact_query_secrets, since exceptions often quote URLs.
    """
    own = conn is None
    if own:
        conn = get_db()
    conn.execute(
        "INSERT INTO logs (level, message) VALUES (?, ?)",
        (normalise_log_level(level), redact_query_secrets(message)),
    )
    if own:
        conn.commit()
        conn.close()


def labels_by_service(
    conn: sqlite3.Connection, service_ids: list[int]
) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    """Return (tags, environments) per service id, ordered by name.

    Read as one row per link rather than GROUP_CONCAT, because label names may contain
    the "," and ":" separators.
    """
    tags: dict[int, list[dict]] = {}
    envs: dict[int, list[dict]] = {}
    if not service_ids:
        return tags, envs
    placeholders = ",".join(["?"] * len(service_ids))
    for out, table, link, column in (
        (tags, "tags", "service_tags", "tag_id"),
        (envs, "environments", "service_environments", "environment_id"),
    ):
        rows = conn.execute(
            f"""SELECT l.service_id AS service_id, x.id AS id, x.name AS name,
                       x.color AS color
                  FROM {link} l
                  JOIN {table} x ON x.id = l.{column}
                 WHERE l.service_id IN ({placeholders})
                 ORDER BY x.name, x.id""",
            service_ids,
        ).fetchall()
        for r in rows:
            out.setdefault(r["service_id"], []).append(
                {"id": r["id"], "name": r["name"], "color": r["color"]}
            )
    return tags, envs


def row_to_service(
    row, tags: list[dict] | None = None, environments: list[dict] | None = None
) -> dict:
    """Convert a services row to a dict, adding the labels from `labels_by_service`."""
    d = dict(row)
    d["tags"]         = tags or []
    d["environments"] = environments or []
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
