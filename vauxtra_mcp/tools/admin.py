"""MCP tools — auth, settings, domains, tags, envs, webhooks, api keys, backups."""
import json
from typing import Any, Literal

import httpx

from vauxtra_mcp import client
from vauxtra_mcp.app import mcp


@mcp.tool()
def get_auth_status() -> dict[str, Any]:
    """Return current authentication/setup state for this API client."""
    r = client.get("/auth/me")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def auth_login(password: str) -> dict[str, Any]:
    """Create an authenticated session using the admin password."""
    r = client.post("/auth/login", json={"password": password})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def auth_logout() -> dict[str, Any]:
    """Clear authenticated session."""
    r = client.post("/auth/logout")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def setup_password(password: str) -> dict[str, Any]:
    """Set the initial admin password when auth is not yet configured."""
    r = client.post("/auth/setup-password", json={"password": password})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def change_password(current_password: str, new_password: str) -> dict[str, Any]:
    """Change the admin password."""
    r = client.post("/auth/change-password", json={"current_password": current_password, "new_password": new_password})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def mark_setup_complete() -> dict[str, Any]:
    """Mark setup wizard as complete on the server."""
    r = client.post("/auth/setup-complete")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_settings() -> dict[str, Any]:
    """Get all global Vauxtra settings."""
    r = client.get("/settings")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Save one or more global settings."""
    r = client.post("/settings", json=settings)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_domains() -> list[str]:
    """List configured root domains."""
    r = client.get("/domains")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def add_domain(name: str) -> dict[str, Any]:
    """Add a root domain for services."""
    r = client.post("/domains", json={"name": name})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_domain(name: str) -> dict[str, Any]:
    """Delete a root domain by exact name."""
    r = client.delete(f"/domains/{name}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_tags() -> list[dict[str, Any]]:
    """List all tags."""
    r = client.get("/tags")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_tag(name: str, color: str = "blue") -> dict[str, Any]:
    """Create a tag."""
    r = client.post("/tags", json={"name": name, "color": color})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def update_tag(tag_id: int, name: str, color: str = "blue") -> dict[str, Any]:
    """Update a tag by id."""
    r = client.put(f"/tags/{tag_id}", json={"name": name, "color": color})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_tag(tag_id: int) -> dict[str, Any]:
    """Delete a tag by id."""
    r = client.delete(f"/tags/{tag_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_environments() -> list[dict[str, Any]]:
    """List all environments."""
    r = client.get("/environments")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_environment(name: str, color: str = "blue") -> dict[str, Any]:
    """Create an environment."""
    r = client.post("/environments", json={"name": name, "color": color})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def update_environment(environment_id: int, name: str, color: str = "blue") -> dict[str, Any]:
    """Update an environment by id."""
    r = client.put(f"/environments/{environment_id}", json={"name": name, "color": color})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_environment(environment_id: int) -> dict[str, Any]:
    """Delete an environment by id."""
    r = client.delete(f"/environments/{environment_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_webhooks() -> list[dict[str, Any]]:
    """List all webhooks."""
    r = client.get("/webhooks")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_webhook(name: str, url: str) -> dict[str, Any]:
    """Create a webhook notification target."""
    r = client.post("/webhooks", json={"name": name, "url": url})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def update_webhook(webhook_id: int, name: str, url: str, enabled: bool = True) -> dict[str, Any]:
    """Update a webhook by id."""
    r = client.put(
        f"/webhooks/{webhook_id}",
        json={"name": name, "url": url, "enabled": enabled},
    )
    r.raise_for_status()
    return r.json()


@mcp.tool()
def delete_webhook(webhook_id: int) -> dict[str, Any]:
    """Delete a webhook by id."""
    r = client.delete(f"/webhooks/{webhook_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def test_webhook_url(url: str) -> dict[str, Any]:
    """Test an Apprise URL without creating a webhook."""
    r = client.post("/webhooks/test-url", json={"url": url})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def test_webhook(webhook_id: int) -> dict[str, Any]:
    """Send a test notification to an existing webhook."""
    r = client.post(f"/webhooks/{webhook_id}/test")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_service_alerts(service_id: int) -> list[dict[str, Any]]:
    """List per-service webhook alert rules."""
    r = client.get(f"/services/{service_id}/alerts")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def set_service_alerts(service_id: int, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace all per-service alert rules."""
    r = client.post(f"/services/{service_id}/alerts", json={"alerts": alerts})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_api_keys() -> list[dict[str, Any]]:
    """List API keys (secret value is never returned)."""
    r = client.get("/settings/api-keys")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_api_key(name: str, scopes: list[Literal["read", "write", "admin"]]) -> dict[str, Any]:
    """Create an API key and return the secret once."""
    r = client.post("/settings/api-keys", json={"name": name, "scopes": scopes})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def revoke_api_key(key_id: int) -> dict[str, Any]:
    """Revoke an API key by id."""
    r = client.delete(f"/settings/api-keys/{key_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_logs(level: str = "", page: int = 1, per_page: int = 50) -> dict[str, Any]:
    """Read logs with optional level filter and pagination."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if level:
        params["level"] = level
    r = client.get("/logs", params=params)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def clear_logs() -> dict[str, Any]:
    """Delete all logs."""
    r = client.post("/logs/clear")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def test_global_webhook() -> dict[str, Any]:
    """Send a test notification using global webhook settings."""
    r = client.post("/settings/test-webhook")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_backup() -> dict[str, Any]:
    """Export a backup without credentials."""
    r = client.get("/backup")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def create_secure_backup(passphrase: str) -> dict[str, Any]:
    """Export a backup with credentials encrypted by passphrase."""
    r = client.post("/backup/secure", json={"passphrase": passphrase})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def restore_backup(backup: dict[str, Any], passphrase: str = "") -> dict[str, Any]:
    """Restore from a backup payload. WARNING: this replaces current data."""
    r = client.post("/restore", json={"backup": backup, "passphrase": passphrase})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def reset_all_data() -> dict[str, Any]:
    """WARNING: delete all app data (services/providers/settings/logs)."""
    r = client.post("/reset")
    r.raise_for_status()
    return r.json()


@mcp.tool()
def stream_logs_snapshot(max_events: int = 10, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Read a bounded snapshot from the SSE logs stream endpoint.

    This does not keep a persistent stream open; it reads up to max_events and returns.
    """
    max_events = max(1, min(max_events, 200))
    timeout_seconds = max(1.0, min(timeout_seconds, 30.0))

    events: list[dict[str, Any]] = []
    with httpx.Client(base_url=client.VAUXTRA_URL, timeout=timeout_seconds) as c:
        with c.stream("GET", "/api/logs/stream", headers=client.auth_headers()) as r:
            r.raise_for_status()
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

    return {
        "count": len(events),
        "events": events,
    }
