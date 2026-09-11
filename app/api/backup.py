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

router = APIRouter()


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    ).fetchone()
    return result is not None


# Emptied by a restore, in this order. The same list `POST /api/reset` uses, minus
# `api_keys`: only the prefix of a key is ever exported, never its hash, so wiping that
# table would lock the operator's own automation out of the instance it just restored, and
# a restore could not put a usable key back either. Everything else has to go, because the
# restore re-inserts explicit ids -- a surviving row does not dangle, it silently re-points
# at whatever record now holds its id.
#
# Children before parents: nothing above may be resurrected by a cascade fired from a row
# deleted below it.
#
# `_restore_wipe_covers_the_schema` holds this list to the schema, so a table added later
# fails a test instead of quietly outliving every restore -- which is how the four below
# came to be missing in the first place:
#   - `webhook_delivery_log` gained its cascade from `webhooks` in schema 11 and is listed
#     anyway, for the same reason as `uptime_events` below: the wipe must not depend on a
#     pragma being on. The retry job reads the destination off the log row rather than off
#     `webhooks`, so a queued send left behind kept firing at a webhook the restored set
#     does not contain.
#   - `scheduler_state` keys its alert bookkeeping by (service_id, webhook_id).
#   - `service_templates` is in neither export, so it survived a restore with its provider
#     columns blanked by the cascade and `tag_ids_json` naming other people's tags.
#   - `uptime_events` was already emptied by its ON DELETE CASCADE on services; it is listed
#     anyway so the wipe does not depend on a pragma being on.
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

# Kept out of the wipe on purpose, and asserted by that same test so the exemption stays a
# decision rather than an oversight. `settings` is wiped separately, down to the protected
# keys; `api_keys` is never touched.
_RESTORE_KEEPS = frozenset({"settings", "api_keys"})


_BACKUP_VERSION = "8"  # Version 8 encrypts the webhook URLs too, and says which fields

# What a secure export encrypts with the passphrase, written into the file so a restore
# never has to guess. A version 7 file carries no such list: it encrypted the provider
# passwords and nothing else, which is exactly what the fallback below assumes, so old
# backups keep restoring.
_ENCRYPTED_FIELDS = ("providers.password", "webhooks.url", "settings.webhook_url")
_LEGACY_ENCRYPTED_FIELDS = ("providers.password",)


class SecureBackupRequest(BaseModel):
    passphrase: str


class RestoreRequest(BaseModel):
    backup: dict
    passphrase: str = ""


def _webhook_without_url(row) -> dict:
    """A webhook row for the *plain* export, with its URL removed.

    An Apprise URL is not a field of a webhook, it is the webhook: `discord://<id>/
    <token>` is enough to post as the operator. The file says `secrets_included: false`
    and the docs say "credentials cleared" -- which is precisely what makes it the file
    an operator forwards to a colleague or attaches to a ticket. The masked form is kept
    so the entry is still identifiable, and `import_backup` restores such a webhook
    disabled rather than writing `discord://***` back as a real URL.
    """
    data = dict(row)
    data["url_masked"] = mask_secret_url(data.pop("url", ""))
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

    # The admin password rule, applied here for a stronger reason than it is applied there:
    # this passphrase guards a file that leaves the instance. Whoever holds the export
    # attacks it offline, as fast as their hardware allows, and 600 000 PBKDF2 iterations
    # buy time against a guess, not against a wordlist that already contains it. Eight
    # characters sat below the floor the same operator's own login had to clear.
    #
    # Only the export is gated. `import_backup` never checked a length and still does not,
    # so a file made under the old rule keeps restoring.
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

        # An Apprise URL is the credential, exactly like a provider password -- and it
        # was leaving in clear in the file whose whole purpose is "encrypted credentials".
        webhooks = []
        for r in conn.execute("SELECT * FROM webhooks").fetchall():
            w = dict(r)
            url = w.get("url", "")
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

    # A file written before version 8 has no list; back then only the provider passwords
    # were encrypted, so its webhook URLs must be taken as-is rather than run through a
    # decryption that would fail on plain text.
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
            raise HTTPException(400, f"Failed to decrypt {label}. Wrong passphrase? ({e})")

    # Dry run BEFORE destroying anything: a wrong passphrase must fail with the database
    # untouched. Decryption is the only expensive validation, so it runs first -- and it
    # covers the webhook URLs too, otherwise a file whose provider list is empty would be
    # wiped in before anything proved the passphrase right.
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
        # One execute() per table, NOT executescript(): executescript() issues an implicit
        # COMMIT before running, which would close the transaction opened above and make the
        # rollback handlers below no-ops on an already-destroyed database.
        for table in _RESTORE_WIPE_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed literal table names
        # Never wipe the credentials: a restore must not be able to drop the instance
        # back to anonymous-admin.
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

        for dom in data.get("domains", []):
            name = dom.get("name") if isinstance(dom, dict) else dom
            created_at = dom.get("created_at") if isinstance(dom, dict) else None
            if not name:
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
            # Stored in clear like it always was: `_try_send_apprise` hands the URL to
            # apprise as-is. The instance key protects the provider passwords, not this.
            webhook_url = _unseal(wh.get("url") or "", "webhooks.url", "webhook URLs")
            # A plain export carries no URL. Restoring the row enabled would leave a
            # webhook that can never fire and logs an error on every alert; restoring it
            # disabled keeps the name, the scope and the rules the operator configured,
            # and says plainly that one field has to be typed back in.
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
                    webhook_url,
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

        for setting in data.get("settings", []):
            # Whitelist: `_VALID_SETTINGS` is the same list `POST /api/settings` writes
            # through, so an imported file reaches no key an operator could not set by
            # hand -- `app_password_hash` above all, which would let a backup file choose
            # the admin password.
            #
            # It is not a filter on content, and this comment used to claim it was: it said
            # `public_target_sources` was blocked, and that key comes straight through. It
            # is left through on purpose. A restore has to give back the configuration it
            # saved, and a backup file is already trusted with provider URLs and Docker
            # endpoints -- singling out one setting would cost a real restore and stop
            # nothing. What bounds it is elsewhere: both routes now require `admin`, and
            # `detect_server_public_ip` refuses an answer that is not publicly routable.
            key = setting.get("key")
            if key not in _VALID_SETTINGS:
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
    svc_count = len(data.get("services", []))
    prv_count = len(data.get("providers", []))
    # Reported, not buried: without this the operator has no way of knowing that some
    # notification targets came back switched off.
    return {
        "ok": True,
        "services": svc_count,
        "providers": prv_count,
        "webhooks_needing_url": webhooks_needing_url,
    }
