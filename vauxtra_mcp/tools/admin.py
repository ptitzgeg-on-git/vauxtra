"""MCP tools — auth, settings, domains, tags, envs, webhooks, api keys, backups."""
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def get_auth_status() -> dict[str, Any]:
    """Return current authentication/setup state for this API client."""
    r = client.get("/auth/me")
    client.check(r)
    return r.json()


@mcp.tool()
def auth_login(password: str) -> dict[str, Any]:
    """Create an authenticated session using the admin password.

    The session cookie is kept for the bridge's lifetime. Prefer VAUXTRA_API_KEY: a key
    has its own scopes, while a password login is always admin and rate-limited.
    Refused when VAUXTRA_API_KEY is set, because the server reads a session before a key
    and the login would silently raise a read-only bridge to admin.
    """
    if client.has_api_key():
        raise ValueError(
            "VAUXTRA_API_KEY is set and this bridge authenticates with it. A password session "
            "would be admin and would override the key's scope; use a key with the scope you need."
        )
    r = client.post("/auth/login", json={"password": password})
    client.check(r)
    return r.json()


@mcp.tool()
def auth_logout() -> dict[str, Any]:
    """Clear the authenticated session, on the server and in this bridge."""
    r = client.post("/auth/logout")
    client.check(r)
    client.clear_session()
    return r.json()


@mcp.tool()
def setup_password(password: str) -> dict[str, Any]:
    """Set the initial admin password when auth is not yet configured."""
    r = client.post("/auth/setup-password", json={"password": password})
    client.check(r)
    return r.json()


@mcp.tool()
def change_password(current_password: str, new_password: str) -> dict[str, Any]:
    """Change the admin password."""
    r = client.post("/auth/change-password", json={"current_password": current_password, "new_password": new_password})
    client.check(r)
    return r.json()


@mcp.tool()
def mark_setup_complete() -> dict[str, Any]:
    """Mark setup wizard as complete on the server."""
    r = client.post("/auth/setup-complete")
    client.check(r)
    return r.json()


@mcp.tool()
def get_settings() -> dict[str, Any]:
    """Get all global Vauxtra settings."""
    r = client.get("/settings")
    client.check(r)
    return r.json()


@mcp.tool()
def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Save one or more global settings. Send only the keys you mean to change.

    The answer lists `saved` and `ignored` (read-only keys such as schema_version and
    setup_completed). A refused value fails the whole call with 400 and writes nothing;
    webhook_url and webhook_enabled are retired, use `create_webhook`.
    `not_applied` lists keys stored but not applied to the running scheduler: report
    those as saved but needing a restart, even though `ok` is true.
    """
    r = client.post("/settings", json=settings)
    client.check(r)
    return r.json()


@mcp.tool()
def list_domains() -> list[str]:
    """List configured root domains."""
    r = client.get("/domains")
    client.check(r)
    return r.json()


@mcp.tool()
def add_domain(name: str) -> dict[str, Any]:
    """Add a root domain for services."""
    r = client.post("/domains", json={"name": name})
    client.check(r)
    return r.json()


@mcp.tool()
def delete_domain(name: str) -> dict[str, Any]:
    """Delete a root domain. Case and a trailing dot do not matter; unknown names raise."""
    r = client.delete(f"/domains/{name}")
    client.check(r)
    return r.json()


@mcp.tool()
def list_tags() -> list[dict[str, Any]]:
    """List all tags."""
    r = client.get("/tags")
    client.check(r)
    return r.json()


@mcp.tool()
def create_tag(name: str, color: Literal[
        "azure",
        "blue",
        "cyan",
        "dark",
        "green",
        "indigo",
        "lime",
        "orange",
        "pink",
        "purple",
        "red",
        "secondary",
        "teal",
        "yellow",
    ] = "blue") -> dict[str, Any]:
    """
    Create a tag.

    `color` is a `Literal` because `TagIn` does not refuse an unknown colour: it silently
    replaces it with blue. A caller that asked for one thing and got another, with a 200 and
    no mention of the substitution, has no way to notice. The fourteen names are the palette
    the API accepts, repeated here by hand -- `scripts/check_api_mcp_parity.py` fails the
    build if the two lists stop matching.
    """
    r = client.post("/tags", json={"name": name, "color": color})
    client.check(r)
    return r.json()


@mcp.tool()
def update_tag(tag_id: int, name: str, color: Literal[
        "azure",
        "blue",
        "cyan",
        "dark",
        "green",
        "indigo",
        "lime",
        "orange",
        "pink",
        "purple",
        "red",
        "secondary",
        "teal",
        "yellow",
    ] = "blue") -> dict[str, Any]:
    """
    Update a tag by id.

    This replaces both fields, so pass the name back even when only the colour changes.
    `color` carries the same `Literal` as `create_tag`, for the same reason: an unknown
    colour is quietly turned into blue rather than refused.
    """
    r = client.put(f"/tags/{tag_id}", json={"name": name, "color": color})
    client.check(r)
    return r.json()


@mcp.tool()
def delete_tag(tag_id: int) -> dict[str, Any]:
    """Delete a tag by id. Never refused.

    Services carrying it are unlinked (they stay published); templates naming it drop it
    on their next read. Call `list_services` and `list_templates` first if you need to know
    what is affected: afterwards only the log records it.
    """
    r = client.delete(f"/tags/{tag_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def list_environments() -> list[dict[str, Any]]:
    """List all environments."""
    r = client.get("/environments")
    client.check(r)
    return r.json()


@mcp.tool()
def create_environment(name: str, color: str = "blue") -> dict[str, Any]:
    """Create an environment."""
    r = client.post("/environments", json={"name": name, "color": color})
    client.check(r)
    return r.json()


@mcp.tool()
def update_environment(environment_id: int, name: str, color: str = "blue") -> dict[str, Any]:
    """Update an environment by id."""
    r = client.put(f"/environments/{environment_id}", json={"name": name, "color": color})
    client.check(r)
    return r.json()


@mcp.tool()
def delete_environment(environment_id: int) -> dict[str, Any]:
    """Delete an environment by id. Nothing refuses it; the services set to it are unlinked.

    `service_environments` declares `ON DELETE CASCADE`, so every service set to this
    environment keeps its hostname and stays published, and loses only the label it was
    grouped and filtered by. Unlike a tag, no service template names an environment, so
    nothing else changes. The journal line written here names the services it counted.
    """
    r = client.delete(f"/environments/{environment_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def list_webhooks() -> list[dict[str, Any]]:
    """List all webhooks. URLs come back masked (`discord://***`) and cannot be read back."""
    r = client.get("/webhooks")
    client.check(r)
    return r.json()


@mcp.tool()
def create_webhook(name: str, url: str, enabled: bool = True) -> dict[str, Any]:
    """Create a webhook notification target.

    Pass `enabled=False` to create one that is configured but silent -- a target prepared
    ahead of the migration that will need it, without it firing in the meantime. It can be
    turned on later with `update_webhook`, which is the same flag.
    """
    r = client.post("/webhooks", json={"name": name, "url": url, "enabled": enabled})
    client.check(r)
    return r.json()


@mcp.tool()
def update_webhook(
    webhook_id: int,
    name: str | None = None,
    url: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Update a webhook by id. Omitted fields keep their stored value.

    `url` is optional on purpose: `list_webhooks` returns it masked, so an agent that had to
    supply one in order to flip `enabled` could only pass the mask back -- which the API now
    refuses rather than store. Leave it out unless you have been given the real URL.
    """
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if url is not None:
        payload["url"] = url
    if enabled is not None:
        payload["enabled"] = enabled
    r = client.put(f"/webhooks/{webhook_id}", json=payload)
    client.check(r)
    return r.json()


@mcp.tool()
def delete_webhook(webhook_id: int) -> dict[str, Any]:
    """Delete a webhook by id."""
    r = client.delete(f"/webhooks/{webhook_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def test_webhook_url(url: str) -> dict[str, Any]:
    """Test an Apprise URL without creating a webhook."""
    r = client.post("/webhooks/test-url", json={"url": url})
    client.check(r)
    return r.json()


@mcp.tool()
def test_webhook(webhook_id: int) -> dict[str, Any]:
    """Send a test notification to an existing webhook."""
    r = client.post(f"/webhooks/{webhook_id}/test")
    client.check(r)
    return r.json()


@mcp.tool()
def get_service_alerts(service_id: int) -> list[dict[str, Any]]:
    """List per-service webhook alert rules."""
    r = client.get(f"/services/{service_id}/alerts")
    client.check(r)
    return r.json()


@mcp.tool()
def set_service_alerts(service_id: int, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace all per-service alert rules."""
    r = client.post(f"/services/{service_id}/alerts", json={"alerts": alerts})
    client.check(r)
    return r.json()


@mcp.tool()
def list_api_keys() -> list[dict[str, Any]]:
    """List API keys (secret value is never returned)."""
    r = client.get("/settings/api-keys")
    client.check(r)
    return r.json()


@mcp.tool()
def create_api_key(
    name: str,
    scopes: Annotated[list[Literal["read", "write", "admin"]], Field(min_length=1)] = ("read",),
) -> dict[str, Any]:
    """Create an API key and return the secret once.

    `scopes` needs at least one of read, write, admin (default read), mirroring
    ApiKeyCreate; tests/test_api_key_scope_residues.py keeps the two in sync. A tuple
    default because ruff B006 forbids a list; the tool schema is the same.
    """
    r = client.post("/settings/api-keys", json={"name": name, "scopes": list(scopes)})
    client.check(r)
    return r.json()


@mcp.tool()
def revoke_api_key(key_id: int) -> dict[str, Any]:
    """Revoke an API key by id."""
    r = client.delete(f"/settings/api-keys/{key_id}")
    client.check(r)
    return r.json()


@mcp.tool()
def clear_logs() -> dict[str, Any]:
    """Delete every entry in the activity log. Needs an `admin` key (403 for `write`).

    The clear is itself logged, so the log then holds one line saying it was emptied.
    Report that line: it is the only record that earlier entries existed.
    """
    r = client.post("/logs/clear")
    client.check(r)
    return r.json()


@mcp.tool()
def test_global_webhook() -> dict[str, Any]:
    """Send a real test notification to every enabled webhook.

    Answers `{"ok": bool, "results": [{"id", "name", "ok", "error"}]}` -- `ok` is true only
    when every target accepted. 400 when no enabled webhook exists.
    """
    r = client.post("/settings/test-webhook")
    client.check(r)
    return r.json()


@mcp.tool()
def create_backup() -> dict[str, Any]:
    """Export a backup without credentials."""
    r = client.get("/backup")
    client.check(r)
    return r.json()


def _backup_dir() -> Path:
    configured = os.environ.get("VAUXTRA_MCP_BACKUP_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".vauxtra-mcp" / "backups"


@mcp.tool()
def create_secure_backup(passphrase: str) -> dict[str, Any]:
    """Export a backup with credentials encrypted by passphrase, into a file on this machine.

    The file goes to `VAUXTRA_MCP_BACKUP_DIR` (default `~/.vauxtra-mcp/backups`), readable by
    its owner only, and only its path comes back. Returned here, the backup would sit in the
    conversation next to the passphrase that decrypts it.
    """
    r = client.post("/backup/secure", json={"passphrase": passphrase})
    client.check(r)
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"vauxtra-secure-{datetime.now(UTC):%Y%m%dT%H%M%S.%fZ}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(r.text)
    counts = {key: len(value) for key, value in r.json().items() if isinstance(value, list)}
    return {"ok": True, "path": str(path), "counts": counts}


@mcp.tool()
def restore_backup(backup: dict[str, Any], passphrase: str = "") -> dict[str, Any]:
    """Restore from a backup payload. WARNING: this replaces current data."""
    r = client.post("/restore", json={"backup": backup, "passphrase": passphrase})
    client.check(r)
    return r.json()


@mcp.tool()
def reset_all_data() -> dict[str, Any]:
    """WARNING: delete all app data (services/providers/settings/logs)."""
    r = client.post("/reset")
    client.check(r)
    return r.json()


@mcp.tool()
def stream_logs_snapshot(max_events: int = 10, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Read a bounded snapshot of the SSE log stream. Needs an `admin` key (403 otherwise).

    Reads up to `max_events` and returns; no stream is kept open. `timeout_seconds` is how
    long to wait: on a quiet instance the events read so far come back with
    `timed_out: true`, and an empty list means quiet, not failure.
    """
    max_events = max(1, min(max_events, 200))
    timeout_seconds = max(1.0, min(timeout_seconds, 30.0))

    events: list[dict[str, Any]] = []
    timed_out = False
    with httpx.Client(base_url=client.VAUXTRA_URL, timeout=timeout_seconds,
                      cookies=client.session_jar()) as c:
        with c.stream("GET", "/api/logs/stream", headers=client.auth_headers()) as r:
            if r.is_error:
                # The body has not been read yet on a streamed response, and `check` needs it
                # to find the API's `detail`.
                r.read()
                client.check(r)
            try:
                for line in r.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        events.append({"raw": payload})
                    if len(events) >= max_events:
                        break
            except httpx.ReadTimeout:
                timed_out = True

    return {
        "count": len(events),
        "events": events,
        "timed_out": timed_out,
    }
