# Vauxtra MCP Server

Exposes Vauxtra's full DNS & proxy management API as [MCP](https://modelcontextprotocol.io/) tools, enabling AI assistants (Claude Desktop, Cursor, etc.) to manage your homelab network directly.

---

## Prerequisites

1. A running Vauxtra instance (`http://localhost:8888` or remote)
2. An API key — create one in **Vauxtra → Settings → API Keys** (scope: `all` for full access)
3. Python 3.12+ with dependencies installed:

```bash
pip install -r requirements.txt
pip install -r vauxtra_mcp/requirements.txt
```

---

## Connecting to Claude Desktop

Edit `~/.config/claude/claude_desktop_config.json` (Linux/Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "vauxtra": {
      "command": "python",
      "args": ["-m", "vauxtra_mcp.server"],
      "cwd": "/path/to/vauxtra",
      "env": {
        "VAUXTRA_URL": "http://localhost:8888",
        "VAUXTRA_API_KEY": "vx_yourkeyhere"
      }
    }
  }
}
```

Restart Claude Desktop. You should see "vauxtra" in the MCP tools panel.

---

## Connecting to Cursor

In Cursor settings → MCP → Add server:

```json
{
  "vauxtra": {
    "command": "python",
    "args": ["-m", "vauxtra_mcp.server"],
    "cwd": "/path/to/vauxtra",
    "env": {
      "VAUXTRA_URL": "http://localhost:8888",
      "VAUXTRA_API_KEY": "vx_yourkeyhere"
    }
  }
}
```

---

## HTTP/SSE transport (remote access)

For remote instances or browser-based clients, run the server in HTTP mode:

```bash
VAUXTRA_URL=http://vauxtra:8888 VAUXTRA_API_KEY=vx_... python -m vauxtra_mcp.server --http
# Listens on http://0.0.0.0:9000
```

Then point your MCP client at `http://your-server:9000`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VAUXTRA_URL` | `http://localhost:8888` | Base URL of your Vauxtra instance |
| `VAUXTRA_API_KEY` | *(required)* | Bearer API key created in Vauxtra settings |

---

## Available tools

### Monitoring (`tools/monitoring.py`)

| Tool | Description |
|---|---|
| `get_health` | Health check — DB status, latency, disk usage, version |
| `get_logs` | Fetch application logs, filterable by level |
| `get_certificates` | List all TLS certificates from proxy providers |
| `get_certificate_expiry` | Expiry report sorted by urgency |
| `check_all_services` | Trigger a health check pass across all services |
| `get_stats` | Service/provider/log counts |

### Operations (`tools/operations.py`)

| Tool | Description |
|---|---|
| `run_preflight` | Validate a service's routing config before pushing |
| `dry_run_push` | Preview what would be pushed to providers (no changes) |
| `push_service` | Push a service's routes to all its configured providers |
| `check_drift` | Compare expected state with live provider state |
| `reconcile_service` | Detect drift and automatically fix it |

### Providers (`tools/providers.py`)

| Tool | Description |
|---|---|
| `list_providers` | List all configured integrations with status |
| `get_provider_types` | Available provider types and their capabilities |
| `create_provider` | Add a new provider integration |
| `update_provider` | Update provider URL/credentials |
| `delete_provider` | Remove a provider |
| `test_provider` | Test connectivity to a provider |
| `get_provider_health` | Health status of a single provider |
| `get_all_providers_health` | Health status of all providers |
| `get_tunnel_health` | Status of Cloudflare Tunnel connections |

### Services (`tools/services.py`)

| Tool | Description |
|---|---|
| `list_services` | List all services with health and routing info |
| `get_service` | Full details of a single service |
| `create_service` | Create a new service record |
| `update_service` | Update a service |
| `delete_service` | Delete a service and remove its provider routes |
| `toggle_service` | Enable/disable a service |
| `sync_services_from_providers` | Pull existing hosts/rewrites from providers |
| `import_services_from_sync` | Import synced data as Vauxtra service records |
| `discover_docker_containers` | Discover Docker containers via the Docker API |
| `list_docker_endpoints` | List configured Docker endpoints |
| `import_docker_containers` | Import Docker containers as services |

---

## Example prompts

Once connected to Claude Desktop or Cursor:

```
"Show me all services that are currently down."
"Check drift on service 12 and reconcile if needed."
"Add a new AdGuard Home provider at http://192.168.1.5:3000."
"What certificates are expiring in the next 30 days?"
"Discover containers from Docker and suggest which ones to import."
```

---

## Security notes

- The API key has the same access level as a logged-in admin. Treat it like a password.
- Never commit the key to git — pass it via environment variable only.
- The MCP server runs locally (stdio) by default, so the key never leaves your machine.
- For HTTP mode, secure the endpoint (reverse proxy + TLS + IP allowlist).
