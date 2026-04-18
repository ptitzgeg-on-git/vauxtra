import json
import os
import base64
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from app.models import get_db, add_log
from app.auth import require_auth
from app.limiter import limiter
from app.config import (
    decrypt_secret, encrypt_secret,
    encrypt_for_backup, decrypt_from_backup
)

router = APIRouter()


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    ).fetchone()
    return result is not None


_BACKUP_VERSION = "7"  # Version 7 supports encrypted secrets


class SecureBackupRequest(BaseModel):
    passphrase: str


class RestoreRequest(BaseModel):
    backup: dict
    passphrase: str = ""


@router.get("/api/backup")
def export_backup(request: Request):
    """Export backup WITHOUT sensitive data (passwords cleared)."""
    require_auth(request)
    conn = get_db()
    try:
        data = {
            "version":             _BACKUP_VERSION,
            "exported_at":         datetime.now(timezone.utc).isoformat(),
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
            "webhooks":            [dict(r) for r in conn.execute("SELECT * FROM webhooks").fetchall()],
            "service_alerts":      [dict(r) for r in conn.execute("SELECT * FROM service_alerts").fetchall()],
            "settings":            [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()],
            "docker_endpoints":    [dict(r) for r in conn.execute("SELECT * FROM docker_endpoints").fetchall()],
            "api_keys":            [
                {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": r["scopes"], "created_at": r["created_at"]}
                for r in conn.execute("SELECT id, name, prefix, scopes, created_at FROM api_keys").fetchall()
            ] if _table_exists(conn, "api_keys") else [],
        }
    finally:
        conn.close()

    filename = f"vauxtra-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
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

    if len(body.passphrase) < 8:
        raise HTTPException(400, "Passphrase must be at least 8 characters")

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

        # Get docker endpoints
        docker_endpoints = [dict(r) for r in conn.execute("SELECT * FROM docker_endpoints").fetchall()]

        data = {
            "version":             _BACKUP_VERSION,
            "exported_at":         datetime.now(timezone.utc).isoformat(),
            "secrets_included":    True,
            "encryption_salt":     salt_b64,
            "providers":           providers,
            "services":            [dict(r) for r in conn.execute("SELECT * FROM services").fetchall()],
            "tags":                [dict(r) for r in conn.execute("SELECT * FROM tags").fetchall()],
            "service_tags":        [dict(r) for r in conn.execute("SELECT * FROM service_tags").fetchall()],
            "service_push_targets":[dict(r) for r in conn.execute("SELECT * FROM service_push_targets").fetchall()],
            "environments":        [dict(r) for r in conn.execute("SELECT * FROM environments").fetchall()],
            "service_environments":[dict(r) for r in conn.execute("SELECT * FROM service_environments").fetchall()],
            "domains":             [dict(r) for r in conn.execute("SELECT * FROM domains").fetchall()],
            "webhooks":            [dict(r) for r in conn.execute("SELECT * FROM webhooks").fetchall()],
            "service_alerts":      [dict(r) for r in conn.execute("SELECT * FROM service_alerts").fetchall()],
            "settings":            [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()],
            "docker_endpoints":    docker_endpoints,
            "api_keys":            [
                {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": r["scopes"], "created_at": r["created_at"]}
                for r in conn.execute("SELECT id, name, prefix, scopes, created_at FROM api_keys").fetchall()
            ] if _table_exists(conn, "api_keys") else [],
        }
    finally:
        conn.close()

    filename = f"vauxtra-backup-secure-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
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

    conn = get_db()
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.executescript("""
            DELETE FROM service_alerts;
            DELETE FROM service_tags;
            DELETE FROM service_push_targets;
            DELETE FROM service_environments;
            DELETE FROM services;
            DELETE FROM providers;
            DELETE FROM tags;
            DELETE FROM environments;
            DELETE FROM webhooks;
            DELETE FROM domains;
            DELETE FROM docker_endpoints;
            DELETE FROM logs;
            DELETE FROM settings;
        """)

        # Restore providers - decrypt from backup passphrase, re-encrypt with instance key
        for p in data.get("providers", []):
            password = p.get("password", "")
            if secrets_included and password and body.passphrase:
                try:
                    # Decrypt from backup, re-encrypt with instance secret
                    decrypted = decrypt_from_backup(password, body.passphrase, salt)
                    password = encrypt_secret(decrypted)
                except Exception as e:
                    raise HTTPException(400, f"Failed to decrypt provider secrets. Wrong passphrase? ({e})")
            
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

        for wh in data.get("webhooks", []):
            conn.execute(
                "INSERT OR REPLACE INTO webhooks (id, name, url, enabled, created_at) VALUES (?,?,?,?,?)",
                (wh.get("id"), wh.get("name"), wh.get("url"), wh.get("enabled", 1), wh.get("created_at")),
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
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (setting.get("key"), setting.get("value")),
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

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(500, str(e))

    conn.close()
    add_log("info", f"Backup restored (version {data.get('version')})")
    svc_count = len(data.get("services", []))
    prv_count = len(data.get("providers", []))
    return {"ok": True, "services": svc_count, "providers": prv_count}
