import base64
import binascii
import json
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from app.api.environments import _VALID_COLORS as _ENV_COLORS
from app.api.environments import _clean_name
from app.api.settings import _PROTECTED_PLACEHOLDERS, _PROTECTED_SETTINGS, _VALID_SETTINGS
from app.api.tags import TagIn
from app.api.templates import TemplateIn
from app.auth import require_auth
from app.config import (
    decrypt_from_backup,
    decrypt_secret,
    derive_fernet_from_passphrase,
    encrypt_for_backup,
    encrypt_secret,
)
from app.limiter import limiter
from app.models import add_log, get_db, legacy_label
from app.security import mask_secret_url, validate_password_strength

router = APIRouter()
_logger = logging.getLogger(__name__)


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
#   - `service_templates` survived a restore with its provider columns blanked by the
#     cascade and `tag_ids_json` naming other people's tags. Both exports now carry it and
#     the restore writes it back after the providers and tags it refers to.
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
    "pending_proxy_removals",
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


# Every list a backup may carry. `domains` also accepts bare names.
_RESTORE_SECTIONS = (
    "providers", "tags", "environments", "domains", "webhooks", "services", "service_tags",
    "service_push_targets", "service_environments", "service_alerts", "settings",
    "docker_endpoints", "service_templates",
)


def _check_restore_shape(data: dict) -> None:
    """N3: a section that is not a list, or an entry that is not an object, is a 400.

    Each one used to reach a `.get()` and end as a 500.
    """
    for section in _RESTORE_SECTIONS:
        value = data.get(section)
        if value is None:
            continue
        if not isinstance(value, list):
            raise HTTPException(400, f"Invalid backup format: {section!r} is not a list")
        for item in value:
            if isinstance(item, dict) or (section == "domains" and isinstance(item, str)):
                continue
            raise HTTPException(400, f"Invalid backup format: an entry of {section!r} is not an object")


def _restorable_labels(rows: list[dict], kind: str, report: dict) -> tuple[list[dict], set]:
    """The tags or environments of the backup, renamed or left out under today's rules.

    C9: one name created before ':' and ',' were refused used to fail the whole restore,
    which is the disaster-recovery path. Such a name is renamed the way the startup
    migration renames it; a name still invalid after that is left out. Both are reported.
    Returns the rows to write and the ids left out.
    """
    taken = {
        str(r.get("name") or "").strip()
        for r in rows
        if ":" not in str(r.get("name") or "") and "," not in str(r.get("name") or "")
    }
    seen: set[str] = set()
    kept: list[dict] = []
    dropped: set = set()
    for row in rows:
        original = str(row.get("name") or "").strip()
        name = original
        if ":" in name or "," in name:
            name = legacy_label(name, taken, row.get("id"))
            taken.add(name)
            report["renamed"].append(f"{kind} {original!r:.40} renamed {name!r}")
        try:
            if kind == "tag":
                checked = TagIn(name=name, color=str(row.get("color") or "blue"))
                name, color = checked.name, checked.color
            else:
                name = _clean_name({"name": name})
                color = row.get("color") if row.get("color") in _ENV_COLORS else "blue"
        except (ValidationError, HTTPException):
            report["skipped"].append(f"{kind} {original!r:.40}: invalid name")
            dropped.add(row.get("id"))
            continue
        if name in seen:
            # The names are unique: INSERT OR REPLACE would have dropped the first one.
            report["skipped"].append(f"{kind} {original!r:.40}: duplicate name")
            dropped.add(row.get("id"))
            continue
        seen.add(name)
        kept.append({**row, "name": name, "color": color})
    return kept, dropped


def _restorable_templates(rows: list[dict], report: dict) -> list[dict]:
    """N9: the templates the backup holds, held to the rules of `POST /api/templates`."""
    fields = set(TemplateIn.model_fields) - {"tag_ids"}
    kept: list[dict] = []
    names: set[str] = set()
    for tpl in rows:
        if not tpl.get("name"):
            continue
        try:
            checked = TemplateIn(**{k: v for k, v in tpl.items() if k in fields and v is not None})
        except (ValidationError, TypeError):
            report["skipped"].append(f"template {str(tpl.get('name'))!r:.40}: invalid")
            continue
        # B17: names are unique now; under `INSERT OR REPLACE` a second template of the
        # same name would silently replace the first one. The name is compared and stored
        # as the API stores it (stripped), so " web" and "web" are one name here too.
        if checked.name in names:
            report["skipped"].append(f"template {str(tpl.get('name'))!r:.40}: duplicate name")
            continue
        names.add(checked.name)
        kept.append({**tpl, "name": checked.name})
    return kept


def _checked_restore_rows(data: dict, report: dict) -> dict[int, dict]:
    """Run the backup's rows through the rules the API applies, before anything is wiped.

    A restore used to write every row as the file spelled it: a provider pointed at the
    cloud metadata address, a Docker endpoint `POST /api/docker/endpoints` refuses, or a
    service whose target the service form rejects all came back and were used as is.

    Returns the normalised service fields keyed by position in `data["services"]`, so the
    stored row is the one a hand-made service would have. A bad provider or Docker
    endpoint is a 400. A service the form refuses today is left out and reported (C9): one
    old row used to fail the restore of the whole file.
    """
    # Late imports: `services` pulls in the provider factory and the sync module.
    from app.api.docker import _is_valid_docker_host
    from app.api.services import ServiceIn
    from app.providers.base import validate_provider_url

    for p in data.get("providers", []):
        try:
            validate_provider_url(str((p or {}).get("url") or ""))
        except (ValueError, AttributeError):
            raise HTTPException(400, f"Invalid provider URL in backup: {(p or {}).get('name')!r:.80}") from None

    for ep in data.get("docker_endpoints", []):
        host = str((ep or {}).get("docker_host", (ep or {}).get("url", "")) or "")
        if host and not _is_valid_docker_host(host):
            raise HTTPException(400, f"Invalid Docker endpoint in backup: {(ep or {}).get('name')!r:.80}")

    services: dict[int, dict] = {}
    seen: set[tuple] = set()
    for index, svc in enumerate(data.get("services", [])):
        try:
            checked = ServiceIn(
                subdomain=str(svc.get("subdomain") or ""),
                domain=str(svc.get("domain") or ""),
                target_ip=str(svc.get("target_ip") or ""),
                target_port=svc.get("target_port"),
                forward_scheme=svc.get("forward_scheme") or "http",
                expose_mode=svc.get("expose_mode") or "proxy_dns",
                tunnel_provider_id=svc.get("tunnel_provider_id"),
                tunnel_hostname=str(svc.get("tunnel_hostname") or ""),
                public_target_mode=svc.get("public_target_mode") or "manual",
                dns_ip=str(svc.get("dns_ip") or ""),
            )
        except (ValidationError, AttributeError, TypeError):
            label = f"{svc.get('subdomain')}.{svc.get('domain')}"
            report["skipped"].append(f"service {label!r:.80}: refused by the service rules")
            continue
        row = checked.model_dump(
            include={"subdomain", "domain", "target_ip", "target_port", "forward_scheme",
                     "expose_mode", "tunnel_hostname", "public_target_mode", "dns_ip"}
        )
        # H3: the rows are written with a plain INSERT, so two rows the unique indexes
        # refuse together are settled here, the first one kept and the other reported.
        # `INSERT OR REPLACE` used to delete the earlier row in silence -- a pair differing
        # only by case was legal before the public hostname index -- and its tags then
        # failed the foreign key and rolled back the whole restore.
        label = f"{row['subdomain']}.{row['domain']}"
        keys = _service_unique_keys(svc.get("id"), row)
        if keys & seen:
            report["skipped"].append(
                f"service {label!r:.80}: same id or public hostname as a service restored before it"
            )
            continue
        seen |= keys
        services[index] = row
    return services


def _service_unique_keys(service_id, row: dict) -> set[tuple]:
    """What the `services` primary key and unique indexes compare, for one restored row.

    The public hostname mirrors `PUBLIC_HOSTNAME_SQL` in `app/models.py`.
    """
    subdomain = str(row.get("subdomain") or "")
    domain = str(row.get("domain") or "")
    tunnel_hostname = str(row.get("tunnel_hostname") or "").strip()
    if row.get("expose_mode") == "tunnel" and tunnel_hostname:
        public = tunnel_hostname.lower()
    else:
        public = f"{subdomain}.{domain}".strip(".").lower()
    keys = {("pair", subdomain, domain), ("public", public)}
    if service_id is not None:
        keys.add(("id", str(service_id)))
    return keys


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


def _templates(conn) -> list[dict]:
    """The service templates, which a restore wipes and therefore has to find in the file."""
    if not _table_exists(conn, "service_templates"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM service_templates").fetchall()]


_TEMPLATE_COLUMNS = (
    ("id", None), ("name", None), ("description", ""), ("forward_scheme", "http"),
    ("target_port", None), ("websocket", 0), ("expose_mode", "proxy_dns"),
    ("proxy_provider_id", None), ("dns_provider_id", None), ("tunnel_provider_id", None),
    ("public_target_mode", "manual"), ("domain", ""), ("dns_ip", ""),
    ("tag_ids_json", "[]"), ("icon_url", ""),
)


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
                # N16: none of the protected keys either. The restore never writes them back,
                # and `app_password_env_fingerprint` is a fast HMAC of the admin password.
                dict(r) for r in conn.execute(
                    f"SELECT key, value FROM settings "  # noqa: S608 - fixed placeholders
                    f"WHERE key NOT IN ({_PROTECTED_PLACEHOLDERS}) AND key <> 'webhook_url'",
                    _PROTECTED_SETTINGS,
                ).fetchall()
            ],
            "docker_endpoints":    [dict(r) for r in conn.execute("SELECT * FROM docker_endpoints").fetchall()],
            "service_templates":   _templates(conn),
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
    # Derived once for the whole file, not once per secret (600 000 rounds each).
    backup_key = derive_fernet_from_passphrase(body.passphrase, salt)

    conn = get_db()
    try:
        # Get providers with decrypted then re-encrypted passwords
        providers = []
        for r in conn.execute("SELECT * FROM providers").fetchall():
            p = dict(r)
            # Decrypt from instance key, re-encrypt with backup passphrase
            plaintext_pwd = decrypt_secret(p.get("password", ""))
            p["password"] = encrypt_for_backup(plaintext_pwd, body.passphrase, salt, backup_key) if plaintext_pwd else ""
            providers.append(p)

        # An Apprise URL is the credential, exactly like a provider password -- and it
        # was leaving in clear in the file whose whole purpose is "encrypted credentials".
        webhooks = []
        for r in conn.execute("SELECT * FROM webhooks").fetchall():
            w = dict(r)
            url = w.get("url", "")
            w["url"] = encrypt_for_backup(url, body.passphrase, salt, backup_key) if url else ""
            webhooks.append(w)

        # Same secret under its legacy global key.
        settings_rows = []
        for r in conn.execute(
            # N16: the protected keys stay out, as in the plain export.
            f"SELECT key, value FROM settings WHERE key NOT IN ({_PROTECTED_PLACEHOLDERS})",  # noqa: S608
            _PROTECTED_SETTINGS,
        ).fetchall():
            s = dict(r)
            if s["key"] == "webhook_url" and s["value"]:
                s["value"] = encrypt_for_backup(s["value"], body.passphrase, salt, backup_key)
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
            "service_templates":   _templates(conn),
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

    _check_restore_shape(data)
    try:
        salt = base64.urlsafe_b64decode(salt_b64) if salt_b64 else b""
    except (binascii.Error, ValueError, TypeError):
        # N3: an undecodable salt used to end as a 500.
        raise HTTPException(400, "Backup is corrupted: invalid encryption salt") from None

    # A file written before version 8 has no list; back then only the provider passwords
    # were encrypted, so its webhook URLs must be taken as-is rather than run through a
    # decryption that would fail on plain text.
    encrypted_fields = set(data.get("encrypted_fields") or _LEGACY_ENCRYPTED_FIELDS)

    # The key is derived once, and every value the dry run decrypts is kept: the second
    # pass runs under BEGIN EXCLUSIVE, and re-deriving there (600 000 rounds per secret)
    # held every writer of the instance for as long.
    backup_key = None
    unsealed: dict[tuple[str, str], str] = {}

    def _unseal(value: str, field: str, label: str) -> str:
        """Decrypt one backup value, or return it untouched if it was never encrypted."""
        nonlocal backup_key
        if not value or not secrets_included or not body.passphrase:
            return value
        if field not in encrypted_fields:
            return value
        if (field, value) in unsealed:
            return unsealed[(field, value)]
        try:
            if backup_key is None:
                backup_key = derive_fernet_from_passphrase(body.passphrase, salt)
            unsealed[(field, value)] = decrypt_from_backup(value, body.passphrase, salt, backup_key)
            return unsealed[(field, value)]
        except Exception as e:
            # `InvalidToken`, the expected failure here, carries no message at all: `str(e)`
            # is empty and the brackets meant to hold the reason printed as a bare "()".
            # Anything else that comes through says something worth keeping.
            reason = str(e).strip() or type(e).__name__
            raise HTTPException(
                400,
                f"Failed to decrypt {label}. The passphrase does not match the one "
                f"this backup was written with ({reason}).",
            )

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

    # Tag and environment names go through the rules `POST /api/tags` and
    # `POST /api/environments` apply, before anything is wiped. A name with ':' or ',' is
    # split apart where service rows carry their tags as "name:color:id", so it is renamed;
    # what is still invalid is left out. Both are reported rather than failing the file.
    report: dict[str, list[str]] = {"skipped": [], "renamed": []}
    tags, dropped_tags = _restorable_labels(data.get("tags") or [], "tag", report)
    environments, dropped_envs = _restorable_labels(data.get("environments") or [], "environment", report)
    templates = _restorable_templates(data.get("service_templates") or [], report)

    checked_services = _checked_restore_rows(data, report)
    kept_service_ids = {
        svc.get("id") for i, svc in enumerate(data.get("services") or []) if i in checked_services
    }
    # A row left out because it repeats a kept row's id must not take the kept row's tags
    # and alerts down with it.
    dropped_services = {
        svc.get("id") for i, svc in enumerate(data.get("services") or []) if i not in checked_services
    } - kept_service_ids

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

        for tag in tags:
            conn.execute(
                "INSERT OR REPLACE INTO tags (id, name, color, created_at) VALUES (?,?,?,?)",
                (tag.get("id"), tag.get("name"), tag.get("color", "blue"), tag.get("created_at")),
            )

        for env in environments:
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
                    alert_on_integration_up, min_down_minutes, created_at, local_target_allowed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    # M11: a webhook saved without `admin` stays unapproved for LAN
                    # targets across a restore. Older backups lack the field: they get
                    # the column default, as the migration gave every existing row.
                    0 if wh.get("local_target_allowed") in (0, "0", False) else 1,
                ),
            )

        # H3: a row without an id takes the next free one from SQLite. Written in file order,
        # it could take the id a later row of the file carries, and that row then failed the
        # primary key and rolled back the whole restore. Rows with an id go first, so the
        # ones without get ids above all of them.
        raw_services = list(enumerate(data.get("services", [])))
        with_id = [p for p in raw_services if isinstance(p[1], dict) and p[1].get("id") is not None]
        first = {i for i, _ in with_id}
        ordered = with_id + [p for p in raw_services if p[0] not in first]
        for index, raw_svc in ordered:
            if index not in checked_services:
                continue  # left out and reported above
            svc = {**raw_svc, **checked_services[index]}
            conn.execute(
                """INSERT INTO services
                   (id, subdomain, domain, target_ip, target_port, forward_scheme,
                    websocket, dns_provider_id, proxy_provider_id, tunnel_provider_id,
                    expose_mode, public_target_mode, auto_update_dns, tunnel_hostname,
                    dns_ip, npm_host_id, enabled, status, last_checked, created_at, icon_url)
                   VALUES (?,?,?,?,?,?,COALESCE(?,0),?,?,?,?,?,COALESCE(?,0),?,?,?,
                           COALESCE(?,1),COALESCE(?,'unknown'),?,COALESCE(?,datetime('now')),
                           COALESCE(?,''))""",
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
            if st.get("service_id") in dropped_services or st.get("tag_id") in dropped_tags:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO service_tags (service_id, tag_id) VALUES (?,?)",
                (st.get("service_id"), st.get("tag_id")),
            )

        for spt in data.get("service_push_targets", []):
            if spt.get("service_id") in dropped_services:
                continue
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
            if se.get("service_id") in dropped_services or se.get("environment_id") in dropped_envs:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO service_environments (service_id, environment_id) VALUES (?,?)",
                (se.get("service_id"), se.get("environment_id")),
            )

        for sa in data.get("service_alerts", []):
            if sa.get("service_id") in dropped_services:
                continue
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

        # The wipe above empties `service_templates`; a file that does not carry them (any
        # export written before they were added) restores none, as before.
        for tpl in templates:
            columns = [c for c, _ in _TEMPLATE_COLUMNS]
            values = [tpl.get(c, default) for c, default in _TEMPLATE_COLUMNS]
            if tpl.get("created_at"):
                columns.append("created_at")
                values.append(tpl["created_at"])
            conn.execute(
                f"INSERT OR REPLACE INTO service_templates ({','.join(columns)}) "  # noqa: S608
                f"VALUES ({','.join('?' * len(columns))})",
                values,
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
    except Exception:
        conn.rollback()
        conn.close()
        # Logged, not echoed: the SQLite message names tables, columns and the values of the
        # row that failed, which is internal detail the reply has no use for.
        _logger.exception("Restore failed; the database was rolled back")
        raise HTTPException(500, "Restore failed; nothing was changed. See the server log.") from None

    conn.close()
    add_log("info", f"Backup restored (version {data.get('version')})")
    for line in report["renamed"] + report["skipped"]:
        add_log("warning", f"Restore: {line}")
    svc_count = len(checked_services)
    prv_count = len(data.get("providers", []))
    # Reported, not buried: without this the operator has no way of knowing that some
    # notification targets came back switched off.
    return {
        "ok": True,
        "services": svc_count,
        "providers": prv_count,
        "webhooks_needing_url": webhooks_needing_url,
        "skipped": report["skipped"],
        "renamed": report["renamed"],
    }
