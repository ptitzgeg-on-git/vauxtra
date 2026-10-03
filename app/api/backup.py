import base64
import json
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.settings import _PROTECTED_PLACEHOLDERS, _PROTECTED_SETTINGS, _VALID_SETTINGS
from app.auth import require_auth
from app.config import decrypt_from_backup, decrypt_secret, encrypt_for_backup, encrypt_secret
from app.limiter import limiter
from app.models import add_log, get_db
from app.security import mask_secret_url, validate_password_strength
from app.text import plural, verb

router = APIRouter()


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    ).fetchone()
    return result is not None


# Tables emptied by a restore, children before parents. Same as POST /api/reset minus
# api_keys (only key prefixes are exported). Restore re-inserts explicit ids, so any
# survivor would re-point at the wrong row. Tables with a cascade are listed anyway so the
# wipe does not depend on the foreign_keys pragma. A test pins this list to the schema.
_RESTORE_WIPE_TABLES = (
    "service_alerts",
    "service_tags",
    "service_push_targets",
    "service_environments",
    "uptime_events",
    "services",
    "webhook_delivery_log",
    "webhooks",
    "service_templates",
    "providers",
    "tags",
    "environments",
    "domains",
    "docker_endpoints",
    "scheduler_state",
    "logs",
)

# Deliberately not wiped (asserted by the same test). settings is wiped separately down
# to the protected keys; api_keys is never touched.
_RESTORE_KEEPS = frozenset({"settings", "api_keys"})

# Wiped by a restore but never exported: history and bookkeeping of the instance itself,
# which must not be replayed onto another one. A test keeps this, the wipe list and the
# export in agreement.
_NOT_EXPORTED_ON_PURPOSE = frozenset({
    "logs",
    "scheduler_state",
    "uptime_events",
    "webhook_delivery_log",
})

# Settings a backup carries but a restore deliberately ignores, so they are not reported as
# lost: the protected keys belong to the running instance (an old session_epoch would
# revive revoked sessions), and webhook_log_purge_done only costs an idempotent re-run.
_RESTORE_DROPS_ON_PURPOSE = frozenset(_PROTECTED_SETTINGS) | {"webhook_log_purge_done"}


_BACKUP_VERSION = "8"  # Version 8 encrypts the webhook URLs too, and says which fields

# Fields a secure export encrypts, written into the file. Version 7 files have no list and
# only encrypted provider passwords, which is what the legacy fallback assumes.
_ENCRYPTED_FIELDS = ("providers.password", "webhooks.url", "settings.webhook_url")
_LEGACY_ENCRYPTED_FIELDS = ("providers.password",)


class SecureBackupRequest(BaseModel):
    passphrase: str


class RestoreRequest(BaseModel):
    backup: dict
    passphrase: str = ""


def _webhook_without_url(row) -> dict:
    """A webhook row for the plain export, URL masked.

    An Apprise URL is a credential. import_backup restores such a webhook disabled.
    """
    data = dict(row)
    data["url_masked"] = mask_secret_url(decrypt_secret(data.pop("url", "")))
    data["url"] = ""
    return data


@router.get("/api/backup")
def export_backup(request: Request):
    """Export backup WITHOUT sensitive data (passwords and webhook URLs cleared)."""
    # Same scope as /api/backup/secure: this file still carries the full topology,
    # provider URLs and usernames.
    require_auth(request, scope="admin")
    conn = get_db()
    try:
        data = {
            "version":             _BACKUP_VERSION,
            "exported_at":         datetime.now(UTC).isoformat(),
            "secrets_included":    False,
            "providers":           [
                {**dict(r), "password": ""}
                for r in conn.execute(
                    "SELECT id,name,type,url,username,extra,enabled,created_at FROM providers"
                ).fetchall()
            ],
            "services":            [dict(r) for r in conn.execute("SELECT * FROM services").fetchall()],
            "tags":                [dict(r) for r in conn.execute("SELECT * FROM tags").fetchall()],
            "service_tags":        [dict(r) for r in conn.execute("SELECT * FROM service_tags").fetchall()],
            "service_push_targets":[dict(r) for r in conn.execute("SELECT * FROM service_push_targets").fetchall()],
            "environments":        [dict(r) for r in conn.execute("SELECT * FROM environments").fetchall()],
            "service_environments":[dict(r) for r in conn.execute("SELECT * FROM service_environments").fetchall()],
            "domains":             [dict(r) for r in conn.execute("SELECT * FROM domains").fetchall()],
            "webhooks":            [
                _webhook_without_url(r)
                for r in conn.execute("SELECT * FROM webhooks").fetchall()
            ],
            "service_alerts":      [dict(r) for r in conn.execute("SELECT * FROM service_alerts").fetchall()],
            "settings":            [
                # `webhook_url` is the legacy global Apprise URL: same secret, same file.
                dict(r) for r in conn.execute(
                    "SELECT key, value FROM settings "
                    "WHERE key NOT IN ('app_password_hash', 'webhook_url')"
                ).fetchall()
            ],
            "docker_endpoints":    [dict(r) for r in conn.execute("SELECT * FROM docker_endpoints").fetchall()],
            "service_templates":   [dict(r) for r in conn.execute("SELECT * FROM service_templates").fetchall()],
            "api_keys":            [
                {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": r["scopes"], "created_at": r["created_at"]}
                for r in conn.execute("SELECT id, name, prefix, scopes, created_at FROM api_keys").fetchall()
            ] if _table_exists(conn, "api_keys") else [],
        }
    finally:
        conn.close()

    filename = f"vauxtra-backup-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(data, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/backup/secure")
@limiter.limit("5/minute")
def export_backup_secure(request: Request, body: SecureBackupRequest):
    """Export backup WITH secrets encrypted using user-provided passphrase.

    Uses PBKDF2-HMAC-SHA256 (600k iterations) + Fernet (AES-128-CBC + HMAC).
    The salt is included in the backup file for decryption.
    """
    require_auth(request, scope="admin")

    # Same strength rule as the admin password: the file leaves the instance and can be
    # attacked offline. Import does not check length, so older files still restore.
    passphrase_ok, passphrase_reason = validate_password_strength(body.passphrase)
    if not passphrase_ok:
        # The shared validator words its messages for a login password.
        raise HTTPException(400, passphrase_reason.replace("Password", "Passphrase", 1))

    # Generate random salt for this backup
    salt = os.urandom(16)
    salt_b64 = base64.urlsafe_b64encode(salt).decode()

    conn = get_db()
    try:
        # Get providers with decrypted then re-encrypted passwords
        providers = []
        for r in conn.execute("SELECT * FROM providers").fetchall():
            p = dict(r)
            # Decrypt from instance key, re-encrypt with backup passphrase
            plaintext_pwd = decrypt_secret(p.get("password", ""))
            p["password"] = encrypt_for_backup(plaintext_pwd, body.passphrase, salt) if plaintext_pwd else ""
            providers.append(p)

        # An Apprise URL is a credential, encrypted like a provider password.
        webhooks = []
        for r in conn.execute("SELECT * FROM webhooks").fetchall():
            w = dict(r)
            url = decrypt_secret(w.get("url", ""))
            w["url"] = encrypt_for_backup(url, body.passphrase, salt) if url else ""
            webhooks.append(w)

        # Same secret under its legacy global key.
        settings_rows = []
        for r in conn.execute(
            "SELECT key, value FROM settings WHERE key NOT IN ('app_password_hash')"
        ).fetchall():
            s = dict(r)
            if s["key"] == "webhook_url" and s["value"]:
                s["value"] = encrypt_for_backup(s["value"], body.passphrase, salt)
            settings_rows.append(s)

        # Get docker endpoints
        docker_endpoints = [dict(r) for r in conn.execute("SELECT * FROM docker_endpoints").fetchall()]

        data = {
            "version":             _BACKUP_VERSION,
            "exported_at":         datetime.now(UTC).isoformat(),
            "secrets_included":    True,
            "encryption_salt":     salt_b64,
            "encrypted_fields":    list(_ENCRYPTED_FIELDS),
            "providers":           providers,
            "services":            [dict(r) for r in conn.execute("SELECT * FROM services").fetchall()],
            "tags":                [dict(r) for r in conn.execute("SELECT * FROM tags").fetchall()],
            "service_tags":        [dict(r) for r in conn.execute("SELECT * FROM service_tags").fetchall()],
            "service_push_targets":[dict(r) for r in conn.execute("SELECT * FROM service_push_targets").fetchall()],
            "environments":        [dict(r) for r in conn.execute("SELECT * FROM environments").fetchall()],
            "service_environments":[dict(r) for r in conn.execute("SELECT * FROM service_environments").fetchall()],
            "domains":             [dict(r) for r in conn.execute("SELECT * FROM domains").fetchall()],
            "webhooks":            webhooks,
            "service_alerts":      [dict(r) for r in conn.execute("SELECT * FROM service_alerts").fetchall()],
            "settings":            settings_rows,
            "docker_endpoints":    docker_endpoints,
            "service_templates":   [dict(r) for r in conn.execute("SELECT * FROM service_templates").fetchall()],
            "api_keys":            [
                {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": r["scopes"], "created_at": r["created_at"]}
                for r in conn.execute("SELECT id, name, prefix, scopes, created_at FROM api_keys").fetchall()
            ] if _table_exists(conn, "api_keys") else [],
        }
    finally:
        conn.close()

    filename = f"vauxtra-backup-secure-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    add_log("info", "Secure backup exported with encrypted secrets")
    return Response(
        content=json.dumps(data, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/restore")
@limiter.limit("3/minute")
def import_backup(request: Request, body: RestoreRequest):
    """Restore from backup. If backup contains encrypted secrets, passphrase is required."""
    # Never behind the setup bypass: a restore wipes and rewrites the whole database,
    # including the admin password hash. It always requires a real admin credential.
    require_auth(request, scope="admin")

    data = body.backup
    if not isinstance(data, dict) or "version" not in data:
        raise HTTPException(400, "Invalid backup format")

    secrets_included = data.get("secrets_included", False)
    salt_b64 = data.get("encryption_salt", "")

    if secrets_included and not body.passphrase:
        raise HTTPException(400, "This backup contains encrypted secrets. Passphrase is required.")

    if secrets_included and not salt_b64:
        raise HTTPException(400, "Backup is corrupted: missing encryption salt")

    salt = base64.urlsafe_b64decode(salt_b64) if salt_b64 else b""

    # Files before version 8 have no list; their webhook URLs are plain text.
    encrypted_fields = set(data.get("encrypted_fields") or _LEGACY_ENCRYPTED_FIELDS)

    def _unseal(value: str, field: str, label: str) -> str:
        """Decrypt one backup value, or return it untouched if it was never encrypted."""
        if not value or not secrets_included or not body.passphrase:
            return value
        if field not in encrypted_fields:
            return value
        try:
            return decrypt_from_backup(value, body.passphrase, salt)
        except Exception as e:
            # InvalidToken has an empty message; fall back to the class name.
            reason = str(e).strip() or type(e).__name__
            raise HTTPException(
                400,
                f"Failed to decrypt {label}. The passphrase does not match the one "
                f"this backup was written with ({reason}).",
            )

    # Decrypt everything before wiping, so a wrong passphrase leaves the database untouched.
    if secrets_included and body.passphrase:
        for p in data.get("providers", []):
            _unseal(p.get("password", ""), "providers.password", "provider secrets")
        for w in data.get("webhooks", []):
            _unseal(w.get("url", ""), "webhooks.url", "webhook URLs")
        for s in data.get("settings", []):
            if s.get("key") == "webhook_url":
                _unseal(s.get("value", ""), "settings.webhook_url", "the webhook URL")

    conn = get_db()
    try:
        conn.execute("BEGIN EXCLUSIVE")
        # Counted before the wipe, to report templates an older file could not restore.
        templates_before = conn.execute("SELECT COUNT(*) FROM service_templates").fetchone()[0]
        # One execute() per table, NOT executescript(): executescript() issues an implicit
        # COMMIT before running, which would close the transaction opened above and make the
        # rollback handlers below no-ops on an already-destroyed database.
        for table in _RESTORE_WIPE_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed literal table names
        # Never wipe the credentials.
        conn.execute(
            f"DELETE FROM settings WHERE key NOT IN ({_PROTECTED_PLACEHOLDERS})",  # noqa: S608
            _PROTECTED_SETTINGS,
        )

        # Restore providers - decrypt from backup passphrase, re-encrypt with instance key
        for p in data.get("providers", []):
            password = p.get("password", "")
            if password:
                password = encrypt_secret(
                    _unseal(password, "providers.password", "provider secrets")
                )

            conn.execute(
                """INSERT OR REPLACE INTO providers
                   (id, name, type, url, username, password, extra, enabled, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (p.get("id"), p.get("name"), p.get("type"), p.get("url"),
                 p.get("username", ""), password,
                 p.get("extra", "{}"), p.get("enabled", 1), p.get("created_at")),
            )

        for tag in data.get("tags", []):
            conn.execute(
                "INSERT OR REPLACE INTO tags (id, name, color, created_at) VALUES (?,?,?,?)",
                (tag.get("id"), tag.get("name"), tag.get("color", "blue"), tag.get("created_at")),
            )

        for env in data.get("environments", []):
            conn.execute(
                "INSERT OR REPLACE INTO environments (id, name, color, created_at) VALUES (?,?,?,?)",
                (env.get("id"), env.get("name"), env.get("color", "blue"), env.get("created_at")),
            )

        domains_without_name = 0
        settings_not_restored: list[str] = []

        for dom in data.get("domains", []):
            name = dom.get("name") if isinstance(dom, dict) else dom
            created_at = dom.get("created_at") if isinstance(dom, dict) else None
            if not name:
                # Counted and reported: services may still reference this domain.
                domains_without_name += 1
                continue
            if created_at:
                conn.execute(
                    "INSERT OR REPLACE INTO domains (name, created_at) VALUES (?,?)",
                    (name, created_at),
                )
            else:
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))

        webhooks_needing_url = 0
        for wh in data.get("webhooks", []):
            webhook_url = _unseal(wh.get("url") or "", "webhooks.url", "webhook URLs")
            # A plain export has no URL: restore the webhook disabled, keeping its rules.
            webhook_enabled = wh.get("enabled", 1) if webhook_url else 0
            if not webhook_url:
                webhooks_needing_url += 1
            conn.execute(
                """INSERT OR REPLACE INTO webhooks
                   (id, name, url, enabled, scope_type, scope_ref_id, repeat_interval_minutes,
                    alert_on_any_down, alert_on_any_up, alert_on_integration_down,
                    alert_on_integration_up, min_down_minutes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    wh.get("id"),
                    wh.get("name"),
                    encrypt_secret(webhook_url),
                    webhook_enabled,
                    wh.get("scope_type", "all"),
                    wh.get("scope_ref_id"),
                    wh.get("repeat_interval_minutes", 0),
                    wh.get("alert_on_any_down", 0),
                    wh.get("alert_on_any_up", 0),
                    wh.get("alert_on_integration_down", 0),
                    wh.get("alert_on_integration_up", 0),
                    wh.get("min_down_minutes", 0),
                    wh.get("created_at"),
                ),
            )

        for svc in data.get("services", []):
            conn.execute(
                """INSERT OR REPLACE INTO services
                   (id, subdomain, domain, target_ip, target_port, forward_scheme,
                    websocket, dns_provider_id, proxy_provider_id, tunnel_provider_id,
                    expose_mode, public_target_mode, auto_update_dns, tunnel_hostname,
                    dns_ip, npm_host_id, enabled, status, last_checked, created_at, icon_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    svc.get("id"),
                    svc.get("subdomain"),
                    svc.get("domain"),
                    svc.get("target_ip"),
                    svc.get("target_port"),
                    svc.get("forward_scheme", "http"),
                    svc.get("websocket", 0),
                    svc.get("dns_provider_id"),
                    svc.get("proxy_provider_id"),
                    svc.get("tunnel_provider_id"),
                    svc.get("expose_mode", "proxy_dns"),
                    svc.get("public_target_mode", "manual"),
                    svc.get("auto_update_dns", 0),
                    svc.get("tunnel_hostname", ""),
                    svc.get("dns_ip", ""),
                    svc.get("npm_host_id"),
                    svc.get("enabled", 1),
                    svc.get("status", "unknown"),
                    svc.get("last_checked"),
                    svc.get("created_at"),
                    svc.get("icon_url", ""),
                ),
            )

        for st in data.get("service_tags", []):
            conn.execute(
                "INSERT OR IGNORE INTO service_tags (service_id, tag_id) VALUES (?,?)",
                (st.get("service_id"), st.get("tag_id")),
            )

        for spt in data.get("service_push_targets", []):
            created_at = spt.get("created_at")
            if created_at:
                conn.execute(
                    "INSERT OR IGNORE INTO service_push_targets (service_id, provider_id, role, created_at) VALUES (?,?,?,?)",
                    (
                        spt.get("service_id"),
                        spt.get("provider_id"),
                        spt.get("role"),
                        created_at,
                    ),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO service_push_targets (service_id, provider_id, role) VALUES (?,?,?)",
                    (
                        spt.get("service_id"),
                        spt.get("provider_id"),
                        spt.get("role"),
                    ),
                )

        for se in data.get("service_environments", []):
            conn.execute(
                "INSERT OR IGNORE INTO service_environments (service_id, environment_id) VALUES (?,?)",
                (se.get("service_id"), se.get("environment_id")),
            )

        for sa in data.get("service_alerts", []):
            conn.execute(
                """INSERT OR IGNORE INTO service_alerts
                   (service_id, webhook_id, on_up, on_down, min_down_minutes)
                   VALUES (?,?,?,?,?)""",
                (sa.get("service_id"), sa.get("webhook_id"),
                 sa.get("on_up", 1), sa.get("on_down", 1), sa.get("min_down_minutes", 0)),
            )

        # After providers and tags: the provider columns are real foreign keys. Explicit ids
        # mean no remapping is needed.
        for tpl in data.get("service_templates", []):
            conn.execute(
                """INSERT OR REPLACE INTO service_templates
                   (id, name, description, forward_scheme, target_port, websocket,
                    expose_mode, proxy_provider_id, dns_provider_id, tunnel_provider_id,
                    public_target_mode, domain, dns_ip, tag_ids_json,
                    environment_ids_json, icon_url, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tpl.get("id"),
                    tpl.get("name"),
                    tpl.get("description", ""),
                    tpl.get("forward_scheme", "http"),
                    tpl.get("target_port"),
                    tpl.get("websocket", 0),
                    tpl.get("expose_mode", "proxy_dns"),
                    tpl.get("proxy_provider_id"),
                    tpl.get("dns_provider_id"),
                    tpl.get("tunnel_provider_id"),
                    tpl.get("public_target_mode", "manual"),
                    tpl.get("domain", ""),
                    tpl.get("dns_ip", ""),
                    tpl.get("tag_ids_json", "[]"),
                    # Absent from exports made before templates had environments.
                    tpl.get("environment_ids_json", "[]"),
                    tpl.get("icon_url", ""),
                    tpl.get("created_at"),
                ),
            )

        for setting in data.get("settings", []):
            # Same whitelist as POST /api/settings, so a file cannot set the password hash.
            # public_target_sources is allowed on purpose: restore is admin-only and the
            # detected IP must be publicly routable.
            key = setting.get("key")
            if key not in _VALID_SETTINGS:
                # Report keys dropped unintentionally (retired or from a newer version); the
                # restore still succeeds.
                if key and key not in _RESTORE_DROPS_ON_PURPOSE:
                    settings_not_restored.append(str(key))
                continue
            value = setting.get("value")
            if key == "webhook_url":
                value = _unseal(value or "", "settings.webhook_url", "the webhook URL")
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, value),
            )

        # Restore docker endpoints
        for ep in data.get("docker_endpoints", []):
            conn.execute(
                """INSERT OR REPLACE INTO docker_endpoints
                   (id, name, docker_host, enabled, is_default, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (ep.get("id"), ep.get("name"), ep.get("docker_host", ep.get("url", "")),
                 ep.get("enabled", 1), ep.get("is_default", 0), ep.get("created_at")),
            )

        # A successful restore should always leave the instance out of first-launch mode,
        # even if the imported backup has no settings rows.
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_completed', '1')"
        )

        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(500, str(e))

    conn.close()
    add_log("info", f"Backup restored (version {data.get('version')})")
    # Extra log lines only when something was lost.
    if settings_not_restored:
        n = len(settings_not_restored)
        add_log(
            "warning",
            "Backup restored: "
            + plural(n, "setting")
            + " in the file "
            + verb(n, "is", "are")
            + " not accepted by this version, and "
            + verb(n, "was", "were")
            + " dropped rather than restored ("
            + ", ".join(sorted(settings_not_restored))
            + ")",
        )
    if domains_without_name:
        add_log(
            "warning",
            "Backup restored: "
            + plural(domains_without_name, "domain")
            + " in the file carried no name and could not be recreated",
        )
    tpl_count = len(data.get("service_templates", []))
    if templates_before and not tpl_count:
        # Not a failure, but the operator must know why the Templates page is empty.
        add_log(
            "warning",
            "Backup restored: "
            + plural(templates_before, "service template")
            + " "
            + verb(templates_before, "was", "were")
            + " emptied and the file carried none to put back "
            + "(it was written by a version whose export did not include them)",
        )
    svc_count = len(data.get("services", []))
    prv_count = len(data.get("providers", []))
    # Report disabled webhooks, dropped settings and nameless domains.
    return {
        "ok": True,
        "services": svc_count,
        "providers": prv_count,
        "templates": tpl_count,
        "webhooks_needing_url": webhooks_needing_url,
        "settings_not_restored": sorted(settings_not_restored),
        "domains_without_name": domains_without_name,
    }
