import os
import sqlite3
from contextlib import contextmanager

from app.config import DATA_DIR, DB_PATH  # noqa: F401 — re-exported for test patching
from app.db import get_connection

# Increment this constant whenever a new ALTER is added to _migrate().
# The value is stored in the settings table and logged on startup.
SCHEMA_VERSION = 11


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

        CREATE TABLE IF NOT EXISTS api_keys (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            key_hash     TEXT    NOT NULL UNIQUE,
            prefix       TEXT    NOT NULL,
            scopes       TEXT    NOT NULL DEFAULT 'read',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT
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

        -- A route a service left behind on a proxy it no longer uses, whose removal the
        -- provider refused. Kept here, not as the service's proxy: as its proxy, the drift
        -- check reported it missing and the auto-reconcile recreated it. The scheduler
        -- retries the removal each cycle.
        CREATE TABLE IF NOT EXISTS pending_proxy_removals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
            host_id     TEXT    NOT NULL,
            public_host TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(provider_id, host_id)
        );
    """)
    _migrate(conn)
    _update_schema_version(conn)
    conn.commit()
    conn.close()


def ensure_default_docker_endpoint(conn: sqlite3.Connection) -> None:
    """Guarantee exactly one default Docker endpoint. Does not commit; the caller owns that.

    Called from `init_db` at startup and from `POST /api/reset`, which now empties
    `docker_endpoints` -- an endpoint an operator added carries a host and its TLS material,
    and a reset that keeps those is not the reset the button offers. Without this the
    instance would come back from a reset with no Docker host at all until the next restart.
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


def _migrate(conn: sqlite3.Connection) -> None:
    for sql in [
        "ALTER TABLE providers ADD COLUMN extra TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE services ADD COLUMN status       TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE services ADD COLUMN last_checked TEXT",
        # `services.environment` was the single free-text environment, replaced by the
        # `service_environments` join before 1.1. Nothing has read it since -- not the API,
        # not the frontend, not the backup restore, which lists its columns explicitly --
        # so it is dropped below rather than kept as a column every row carries empty.
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
        # M11: 1 when an admin stored the URL, so it may reach the local network. The
        # senders re-check the target on every send and refuse a local one otherwise.
        # Rows written before the column existed keep working (DEFAULT 1); the routes
        # always set it explicitly.
        "ALTER TABLE webhooks ADD COLUMN local_target_allowed INTEGER NOT NULL DEFAULT 1",
    ]:
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
    _rename_labels_with_separators(conn)
    _ensure_unique_template_names(conn)
    _cancel_unowned_pending_deliveries(conn)


_TEMPLATE_NAME_MAX = 64  # mirrors TemplateIn.name_not_empty in app/api/templates.py


def _ensure_unique_template_names(conn: sqlite3.Connection) -> None:
    """B17: `service_templates.name` had no unique index, so the 409 of a concurrent save
    could never fire and two templates ended with one name. Duplicates written before are
    renamed "<name> (<id>)" (the oldest keeps its name), then the index is created.
    The base is cut so the new name stays within the 64 characters `TemplateIn` accepts,
    otherwise the renamed template could no longer be saved nor survive a restore."""
    rows = conn.execute("SELECT id, name FROM service_templates ORDER BY id").fetchall()
    taken = {r["name"] for r in rows}
    seen: set[str] = set()
    for row in rows:
        if row["name"] not in seen:
            seen.add(row["name"])
            continue
        attempt = 0
        while True:
            suffix = f" ({row['id']})" if attempt == 0 else f" ({row['id']}-{attempt})"
            base = (row["name"] or "")[: max(0, _TEMPLATE_NAME_MAX - len(suffix))].rstrip()
            new_name = f"{base}{suffix}".strip()
            if new_name not in taken:
                break
            attempt += 1
        taken.add(new_name)
        seen.add(new_name)
        conn.execute("UPDATE service_templates SET name=? WHERE id=?", (new_name, row["id"]))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_service_templates_name ON service_templates(name)")


_UNOWNED_DELIVERIES_MARKER = "migration_unowned_deliveries_cancelled"


def _cancel_unowned_pending_deliveries(conn: sqlite3.Connection) -> None:
    """N20: settle, once, the queued sends written before they carried their webhook id.

    Those rows hold the URL as it was when the send failed, and the retry job follows the
    webhook only through `webhook_id`: an edited or revoked URL kept receiving them for up
    to a day and a half. They are cancelled on the first start after the upgrade; rows
    queued later carry their webhook and follow it. Marked in `settings` so a send queued
    without a webhook after that (none of today's callers does) is not cancelled at the
    next restart.
    """
    try:
        if conn.execute(
            "SELECT 1 FROM settings WHERE key=?", (_UNOWNED_DELIVERIES_MARKER,)
        ).fetchone():
            return
        cancelled = conn.execute(
            """UPDATE webhook_delivery_log
               SET status='cancelled', updated_at=datetime('now')
               WHERE status='pending' AND webhook_id IS NULL"""
        ).rowcount
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
            (_UNOWNED_DELIVERIES_MARKER,),
        )
    except sqlite3.Error as exc:
        add_log("error", f"Could not settle the queued webhook deliveries without a webhook: {exc}", conn)
        return
    if cancelled:
        add_log(
            "warn",
            f"Cancelled {cancelled} queued webhook deliveries written by an older version "
            "without their webhook: they would have gone to the URL stored at the time.",
            conn,
        )


LABEL_MAX_LENGTH = 32


def legacy_label(name: str, taken: set[str], ident) -> str:
    """*name* with ':' and ',' replaced by '-', and unique among *taken*.

    Tag and environment names may no longer hold either character: service rows carry them
    as "name:color:id" joined by commas, and such a name was split apart there. Names
    created before the rule are renamed rather than refused, at startup and on restore.
    """
    base = str(name or "").strip().replace(":", "-").replace(",", "-")[:LABEL_MAX_LENGTH].strip()
    base = base or "label"
    if base not in taken:
        return base
    # B8: "<base>-<id>" can itself be taken (an operator's own "a-b-7", or the label next
    # to it): the UPDATE then failed on the UNIQUE index and the startup with it. A counter
    # is added until the name is free.
    attempt = 0
    while True:
        suffix = f"-{ident}" if attempt == 0 else f"-{ident}-{attempt}"
        candidate = f"{base[: LABEL_MAX_LENGTH - len(suffix)]}{suffix}"
        if candidate not in taken:
            return candidate
        attempt += 1


def _rename_labels_with_separators(conn: sqlite3.Connection) -> None:
    """C9: rename the tags and environments created before ':' and ',' were refused."""
    for table in ("tags", "environments"):
        rows = conn.execute(
            f"SELECT id, name FROM {table} WHERE name LIKE '%:%' OR name LIKE '%,%'"  # noqa: S608
        ).fetchall()
        if not rows:
            continue
        taken = {r["name"] for r in conn.execute(f"SELECT name FROM {table}")}  # noqa: S608
        for row in rows:
            new_name = legacy_label(row["name"], taken, row["id"])
            taken.discard(row["name"])
            taken.add(new_name)
            conn.execute(f"UPDATE {table} SET name=? WHERE id=?", (new_name, row["id"]))  # noqa: S608


def _rebuild_webhook_delivery_log_fk(conn: sqlite3.Connection) -> None:
    """Deleting a webhook left its queued sends behind, and the retry job kept firing them.

    `webhook_delivery_log.webhook_id` named a webhook without referencing one, so
    `DELETE FROM webhooks WHERE id=?` -- the whole body of `delete_webhook` -- removed the
    row and nothing else. The retry job reads the destination off the log row rather than
    off `webhooks`, so Vauxtra went on POSTing to a URL the operator had just revoked, for
    the full length of the backoff: up to twenty-four hours after the delete.

    Two things are needed and neither replaces the other. The cascade stops it happening
    again; the copy below drops the rows it has already happened to, which no cascade can
    reach retroactively. Rows with a NULL `webhook_id` are kept -- an ad-hoc send has no
    parent webhook and is not an orphan.

    SQLite cannot add a foreign key to an existing column, so the table is rebuilt. Three
    details make that safe:

      - `PRAGMA foreign_keys` is a no-op inside a transaction, so it is set before BEGIN and
        restored after COMMIT. It has to be off: `ALTER TABLE ... RENAME` would otherwise
        rewrite references pointing at the table being replaced.
      - The statements are issued one `execute()` at a time. `executescript()` COMMITs before
        it runs, which would turn the rollback below into a no-op -- the lesson the restore
        path already carries at `app/api/backup.py`.
      - `foreign_key_check` runs before the COMMIT, not after, so a database that somehow
        still violates the new constraint keeps its old table instead of a half-built one.

    Idempotent: it reads the pragma rather than the schema version, so an install whose
    version row was written by a failed earlier attempt is still repaired.
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
    """Two services on one hostname is not a configuration, it is a race.

    Both rows resolve to the same proxy host and the same DNS record, so whichever is pushed
    last wins and the drift check then reports the other as permanently wrong. Nothing
    prevented it: the table carried no constraint and neither write endpoint looked.

    The index is only created when the database allows it. An install that already holds
    duplicates keeps booting -- it is told which rows to merge instead of being wedged shut,
    which is the same reasoning as the defensive `check_interval` read at startup.
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
            # On the migration connection: it may hold the write lock here, and a second
            # connection waited out the busy timeout before failing the boot.
            conn,
        )
        return
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_services_hostname ON services (subdomain, domain)"
    )
    # N24: the name a service actually publishes. A tunnel service publishes
    # `tunnel_hostname`, which the pair above does not see, so two concurrent creations of
    # one tunnel hostname (or a tunnel and a proxy service on one name) both got through
    # the check `_conflicting_service` makes before the INSERT. Same expression as
    # `_service_public_hostname` in `app/api/services.py`.
    try:
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_services_public_hostname ON services ({PUBLIC_HOSTNAME_SQL})")
    except sqlite3.IntegrityError:
        add_log(
            "warn",
            "Two services publish the same hostname (one of them through a tunnel), which "
            "prevents the public hostname index. Merge or delete the extra rows.",
            conn,
        )


PUBLIC_HOSTNAME_SQL = (
    "CASE WHEN expose_mode = 'tunnel' AND trim(tunnel_hostname) <> '' "
    "THEN lower(trim(tunnel_hostname)) "
    "ELSE lower(trim(subdomain || '.' || domain, '.')) END"
)


def _drop_legacy_service_environment(conn: sqlite3.Connection) -> None:
    """Remove `services.environment`, superseded by the `service_environments` join.

    Guarded rather than assumed: `DROP COLUMN` needs SQLite 3.35, and an installation on an
    older build keeps the empty column instead of failing to start. Nothing reads it either
    way, so there is nothing to fall back to.
    """
    try:
        conn.execute("ALTER TABLE services DROP COLUMN environment")
    except sqlite3.OperationalError:
        pass  # already gone, or a SQLite too old to drop it


def _migrate_legacy_webhook_url(conn: sqlite3.Connection) -> None:
    """Move `settings.webhook_url` into the `webhooks` table, where delivery reads.

    That setting is the pre-1.1 global notification URL. It kept its whole surface -- it was
    writable, it was masked on the way out, and `POST /api/settings/test-webhook` sent a real
    notification through it that arrived. What it no longer had was a delivery path:
    `_fire_service_webhooks` and `_fire_global_webhooks` both read `webhooks.url`, and no
    migration ever copied the setting across. So an operator could configure alerting, test
    it successfully, and never be told about a single outage.

    A silent no-op that passes its own test is worse than an obvious gap, so the value is
    moved rather than dropped: one webhook, enabled if `webhook_enabled` said so, scoped to
    everything and subscribed to any service going down and coming back -- which is what a
    single global URL meant. The two settings keys are then deleted, so this runs once.
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

    already = conn.execute("SELECT id FROM webhooks WHERE url=?", (url,)).fetchone()
    if not already:
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
    """Delete log lines that captured a full Apprise URL before those were masked.

    Masking the four `[Webhook]` log calls stops new leaks; it does nothing about the
    rows already in the table, and those stay readable by any API key -- a `read` key
    included -- for as long as the retention window keeps them. A single transient
    Discord outage was enough to write one.

    Runs once, marked in `settings`. Only `[Webhook]` lines carrying a `://` are
    touched, so the operator's ordinary history survives.
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
    """Stamp `auth_mode=password` on instances that already had a hash before this marker.

    Without it, every existing protected install would look -- to the new check -- like one
    that was never given a password, and the downgrade would stay invisible on exactly the
    instances that have something to lose.
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
    """Encrypt any plaintext provider passwords still in the database (one-time migration)."""
    from app.config import encrypt_secret, fernet
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


#: The level column is free text and was written both ways: 10 call sites say "warn",
#: 2 say "warning". One spelling reaches the database, the other is folded into it, so a
#: filter on "warning" is not silently missing rows.
_LEVEL_ALIASES = {"warn": "warning"}


def normalise_log_level(level: str) -> str:
    """The spelling stored for `level`: lowercased, trimmed, `warn` folded into `warning`."""
    cleaned = (level or "").strip().lower()
    return _LEVEL_ALIASES.get(cleaned, cleaned)


def add_log(level: str, message: str, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = get_db()
    conn.execute(
        "INSERT INTO logs (level, message) VALUES (?, ?)",
        (normalise_log_level(level), message),
    )
    if own:
        conn.commit()
        conn.close()


def _parse_labels(raw: str | None) -> list[dict]:
    """Read the `name:color:id` list, comma-joined, that tags and environments share.

    Names cannot hold `:` or `,` (refused by the tag and environment routes): either one
    would split a label apart here.
    """
    if not raw:
        return []
    result = []
    for chunk in raw.split(","):
        parts = chunk.split(":")
        if len(parts) == 3:
            result.append({"name": parts[0], "color": parts[1], "id": int(parts[2])})
    return result


def parse_tags(tags_raw: str | None) -> list[dict]:
    return _parse_labels(tags_raw)


def parse_environments(envs_raw: str | None) -> list[dict]:
    return _parse_labels(envs_raw)


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
