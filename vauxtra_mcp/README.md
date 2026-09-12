# Vauxtra MCP Server

Exposes Vauxtra's full DNS & proxy management API as [MCP](https://modelcontextprotocol.io/) tools, enabling MCP-compatible clients (Claude Desktop, Cursor, etc.) to manage your homelab network directly.

---

## Prerequisites

1. A running Vauxtra instance (`http://localhost:8888` or remote)
2. An API key — create one in **Vauxtra → Settings → API Keys**. Scopes are `read`, `write` and `admin`; `write` covers every tool except the admin ones (`change_password`, backup/restore, factory reset, API key management).
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
# Listens on http://127.0.0.1:9000
```

Then point your MCP client at `http://127.0.0.1:9000`.

The HTTP transport has **no authentication of its own** while holding an API key that can
reach every Vauxtra route, so it binds to loopback only. To expose it, set
`VAUXTRA_MCP_HOST` explicitly — and put an authenticating reverse proxy in front of it, or
tunnel to the loopback port instead. The server prints a warning whenever it binds
anything other than `127.0.0.1`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VAUXTRA_URL` | `http://localhost:8888` | Base URL of your Vauxtra instance |
| `VAUXTRA_API_KEY` | *(required)* | Bearer API key created in Vauxtra settings |
| `VAUXTRA_TIMEOUT` | `120` | Seconds to wait for a Vauxtra call. A push walks every provider in series. |
| `VAUXTRA_MCP_HOST` | `127.0.0.1` | Interface `--http` binds to. Anything else is published without authentication. |
| `VAUXTRA_MCP_PORT` | `9000` | Port `--http` binds to |

---

## Available tools

84 tools across six modules. `scripts/check_api_mcp_parity.py` fails the build if a tool
listed here does not exist, or if a tool exists and is not listed here.

### Services (`tools/services.py`)

| Tool | Description |
|---|---|
| `list_services` | List all services with health and routing info |
| `get_service` | Full details of one service: provider assignments and push targets |
| `create_service` | Create a service (DNS + proxy route) |
| `update_service` | Update specific fields of a service |
| `delete_service` | Delete a service and remove its routes from every provider |
| `toggle_service` | Enable or disable a service without touching its provider routes |
| `check_service_health` | Run a live health/TCP and DNS check for one service |
| `get_services_history` | Last 24 h of uptime history for every service |
| `bulk_service_action` | Enable, disable or delete several services at once (`action` is one of those three words) |
| `suggest_public_targets` | Suggest WAN/public DNS targets from current connectivity |
| `sync_services_from_providers` | Discover services already configured in the providers |
| `import_services_from_sync` | Import what `sync_services_from_providers` found |
| `discover_docker_containers` | Discover running containers on a Docker endpoint |
| `import_docker_containers` | Import Docker containers as services |
| `list_docker_endpoints` | List the configured Docker endpoints |
| `add_docker_endpoint` | Create a Docker endpoint for discovery |
| `set_default_docker_endpoint` | Mark one endpoint as the default for discovery/import |
| `test_docker_endpoint` | Test one endpoint and report how many containers it sees |
| `delete_docker_endpoint` | Delete a Docker endpoint (at least one must remain) |

### Operations (`tools/operations.py`)

| Tool | Description |
|---|---|
| `run_preflight` | Validate a service's routing config before creating or updating it |
| `dry_run_push` | Preview what a push would change, without changing anything |
| `push_service` | Push a service to every configured provider (proxy + DNS) |
| `check_drift` | Compare the expected state against what the providers really hold |
| `reconcile_service` | Detect drift, push corrections, then verify the drift is gone |

### Providers (`tools/providers.py`)

| Tool | Description |
|---|---|
| `list_providers` | List every configured integration with its status |
| `get_provider_types` | Supported provider types, their capabilities and required fields |
| `create_provider` | Add a provider integration |
| `update_provider` | Update a provider; only the fields you send are changed |
| `delete_provider` | Remove a provider (409 while services use it; `force=True` unlinks them) |
| `test_provider` | Test a provider's connection and validate its credentials |
| `test_provider_connection` | Same test, returning structured diagnostics |
| `validate_provider_draft` | Validate a provider's settings before creating it (no DB write) |
| `get_provider_health` | Health of one provider |
| `get_all_providers_health` | Health of every enabled provider, by id |
| `get_tunnel_health` | Aggregate health of all Cloudflare Tunnel providers |

Raw per-provider record editing (`/api/providers/{id}/dns-records`, `/proxy-hosts`) is
deliberately not exposed. A service is the bridge's unit of work: push a service and the
provider rows follow, so reaching underneath them is a way to manufacture drift.

### Templates (`tools/templates.py`)

| Tool | Description |
|---|---|
| `list_templates` | List the service templates |
| `get_template` | Full details of one template |
| `create_template` | Create a template |
| `update_template` | Replace a template's settings (full replacement, not a patch) |
| `delete_template` | Delete a template; services already created from it are untouched |
| `apply_template` | Create a service from a template; refuses when neither the call nor the template supplies a domain and a port |

### Monitoring (`tools/monitoring.py`)

| Tool | Description |
|---|---|
| `get_health` | DB status, latency, disk usage, version |
| `get_logs` | Recent operational logs, filterable by level |
| `get_certificates` | TLS certificates held by the proxy providers |
| `get_certificate_expiry` | The same certificates with days remaining, sorted by urgency |
| `check_all_services` | Trigger a health-check pass over every service |
| `get_stats` | Service, provider and log counts |

### Administration (`tools/admin.py`)

**Authentication**

| Tool | Description |
|---|---|
| `get_auth_status` | Whether auth is configured, and whether this client is authenticated |
| `auth_login` | Open a session with the admin password; the cookie is kept for later calls |
| `auth_logout` | Close the session, on the server and in this bridge |
| `setup_password` | Set the initial admin password when none is configured |
| `change_password` | Change the admin password |
| `mark_setup_complete` | Mark the setup wizard as finished |

An API key is the better credential here: it carries a scope, `auth_login` does not — a
session is always `admin`. Use `auth_login` only on an instance with no key yet.

**Settings and domains**

| Tool | Description |
|---|---|
| `get_settings` | Every global setting (secrets come back masked) |
| `save_settings` | Save settings; send only the keys you mean to change |
| `list_domains` | The configured root domains |
| `add_domain` | Add a root domain |
| `delete_domain` | Delete a root domain by exact name |

**Tags and environments**

| Tool | Description |
|---|---|
| `list_tags` / `create_tag` / `update_tag` / `delete_tag` | Tag CRUD |
| `list_environments` / `create_environment` / `update_environment` / `delete_environment` | Environment CRUD |

**Webhooks and alerts**

| Tool | Description |
|---|---|
| `list_webhooks` | List targets; URLs come back masked (`discord://***`) and cannot be written back |
| `create_webhook` | Create a notification target |
| `update_webhook` | Update a target; omitted fields keep their stored value |
| `delete_webhook` | Delete a target |
| `test_webhook_url` | Test an Apprise URL without creating anything |
| `test_webhook` | Send a real test notification to one existing target |
| `test_global_webhook` | Send one to every enabled target; answers `{ok, results[]}`, one entry per target |
| `get_service_alerts` | The per-service alert rules |
| `set_service_alerts` | Replace all per-service alert rules |

**API keys**

| Tool | Description |
|---|---|
| `list_api_keys` | List keys; the secret is never returned |
| `create_api_key` | Create a key and return its secret once |
| `revoke_api_key` | Revoke a key by id |

**Logs**

| Tool | Description |
|---|---|
| `clear_logs` | Delete every log entry |
| `stream_logs_snapshot` | Read a bounded slice of the SSE log stream; a quiet instance answers `timed_out: true` with whatever it collected |

**Backup and reset**

| Tool | Description |
|---|---|
| `create_backup` | Export a backup without credentials |
| `create_secure_backup` | Export a backup with credentials encrypted by a passphrase |
| `restore_backup` | Restore from a backup payload — this replaces current data |
| `reset_all_data` | Delete all application data |

---

## What the tools refuse before calling

A tool's parameters are its whole contract. FastMCP builds the schema from the function
signature, and a normal install publishes no OpenAPI document to derive one from
(`DEBUG` is false, so `openapi_url` is None), so whatever the route enforces has to be
repeated here by hand. Where that had not been done the bridge sent bodies the API refused
-- and, in one case, a body it accepted that nobody had asked for.

| Parameter | Accepted values | Tools |
|---|---|---|
| `forward_scheme` | `http`, `https` | `create_service`, `update_service`, `create_template`, `update_template`, `run_preflight` |
| `expose_mode` | `proxy_dns`, `tunnel` | `create_service`, `create_template`, `update_template`, `run_preflight` |
| `public_target_mode` | `auto`, `manual` | the same four, plus `create_service` |
| `target_port` | 1 to 65535 | every tool that takes a port |
| `action` | `enable`, `disable`, `delete` | `bulk_service_action` |
| `type` | the ten provider types | `create_provider`, `validate_provider_draft` |
| `color` | the fourteen tag colours | `create_tag`, `update_tag` |
| `scopes` | `read`, `write`, `admin`, at least one | `create_api_key` |

A value outside one of these sets is refused by the schema, before any request is built.
Two of them are worth knowing about specifically:

- `bulk_service_action` used to take any string and let the API answer 400. `delete` is one
  of the three words, so the round trip now being saved is one that deletes services.
- a tag colour the API does not know is not refused by the API: it is quietly stored as
  blue, with a 200 and no mention of the substitution. The bridge is the only place that
  can tell you the colour you asked for does not exist.

`apply_template` is the one place where the tool is deliberately looser than the route.
`POST /api/services` requires a domain and a port, and a template may legitimately carry
neither -- that is what lets one template serve several domains. So both stay optional on
the tool and are taken from the template when the call omits them; when neither side has a
value, the call is refused and nothing is sent. It used to fall back to `or 80` and `or ""`.
The empty domain came back as a 422, but port 80 did not, because 80 is a valid port: a
call that named no port, against a template that sets none, created a service pointing at a
port nobody had chosen, and reported success.

One rule no parameter schema can carry: `expose_mode: tunnel` also needs a
`tunnel_provider_id`. That is a rule about a pair of fields, and a schema describes one
field at a time, so the route is still what decides.

`scripts/check_api_mcp_parity.py` compares every tool's parameters against the model its
route validates -- names, required or optional, types, value sets, bounds -- and fails the
build on any difference that is not in its commented exemption list.

---

## When a call fails

Errors carry what the API said, not just its status code:

```
ApiError: POST /api/settings -> 400: Nothing was saved -- check_interval: must be between 30 and 86400
```

`raise_for_status()` used to produce `Client error '400 Bad Request' for url ...`, which
threw the `detail` away — and since 1.1 the detail is the useful part: which setting was
refused and why, which provider still holds a service, that a hostname is already taken.

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

- A key grants exactly the scopes it was created with — a session login is always `admin`,
  a key is not. Give each integration the lowest scope that works, and treat the key
  itself like a password.
- Scopes are hierarchical: `admin` satisfies `write`, `write` satisfies `read`. A `read`
  key is refused with `403 Insufficient scope` on anything that changes state or sends
  something outward, including the test-send and preflight tools.
- Never commit the key to git — pass it via environment variable only.
- The MCP server runs locally (stdio) by default, so the key never leaves your machine.
- For HTTP mode, secure the endpoint (reverse proxy + TLS + IP allowlist).
