# Vauxtra — How-To Guide

## Table of Contents

1. [First Launch & Setup Wizard](#1-first-launch--setup-wizard)
2. [Authentication](#2-authentication)
3. [SECRET_KEY — Important Warning](#3-secret_key--important-warning)
4. [Docker: Single or Multiple Hosts](#4-docker-single-or-multiple-hosts)
5. [Provider Setup](#5-provider-setup)
6. [Service Workflow](#6-service-workflow)
7. [Notifications](#7-notifications)
8. [Theme: Light / Dark / Auto](#8-theme-light--dark--auto)
9. [WAN Auto-Target Policy](#9-wan-auto-target-policy)
10. [MCP Integration](#10-mcp-integration)
11. [API Reference](#11-api-reference)
12. [Troubleshooting](#12-troubleshooting)

---

## 1) First Launch & Setup Wizard

On first launch, Vauxtra displays a guided setup wizard to help you configure:

1. **Password** — Protect access to your panel (optional — you can skip for open access)
2. **Providers** — Connect reverse proxies (NPM, Traefik) and DNS providers (Cloudflare, Pi-hole, AdGuard)
3. **Notifications** — Add webhooks for alerts (Discord, Slack, Telegram, etc.)
4. **Docker endpoints** — Connect Docker hosts for container discovery

The wizard includes step-by-step guided instructions for each provider type, with links to where you can find API tokens and credentials.

You can skip any step and configure it later from the Settings page.

---

## 2) Authentication

Vauxtra uses a password to protect access to the panel.

### Setting a password

- **Setup wizard**: On first launch, the wizard prompts you to choose a password.
- **Environment variable**: Set `APP_PASSWORD` in your `.env` file (takes priority over UI-configured password).
- **No password**: Leave both empty for open access (anyone on your network can access the panel).

### Password storage

Passwords set via the Setup wizard are stored as PBKDF2-HMAC-SHA256 hashes (600k iterations) in the database.  
Passwords set via `APP_PASSWORD` env var are compared in plaintext (not hashed).

### Forgot your password?

- **If set via `.env`**: Edit the file, change or remove `APP_PASSWORD`, restart the container.
- **If set via Setup wizard**: Connect to the database and delete the `app_password_hash` row:
  ```bash
  sqlite3 data/vauxtra.db "DELETE FROM settings WHERE key='app_password_hash';"
  ```
  Then restart the container.

---

## 3) SECRET_KEY — Important Warning

⚠️ **Do NOT change SECRET_KEY after adding providers.**

The `SECRET_KEY` is used for:

1. **Session cookies** — signing authenticated sessions
2. **Credential encryption** — all provider credentials (API tokens, passwords) are encrypted with a key derived from SECRET_KEY

If you change SECRET_KEY after adding providers, **all stored credentials become unreadable**. You would need to re-enter all provider credentials.

### How it works

- If `SECRET_KEY` is not set in `.env`, Vauxtra auto-generates one on first launch and stores it in `data/.secret_key`.
- The auto-generated key persists across container restarts (as long as you mount the `data/` volume).
- For production, you may set your own key via `SECRET_KEY=<your-64-char-hex-string>` in `.env`.

### Best practice

Let Vauxtra auto-generate the key (default behavior). Just make sure to **back up your `data/` folder**, which includes the `.secret_key` file and the database.

---

## 4) Docker: Single or Multiple Hosts

Vauxtra supports multiple Docker endpoints:

- **Local socket**: `unix:///var/run/docker.sock` (default)
- **TCP**: `tcp://192.168.1.100:2375`
- **SSH**: `ssh://user@hostname`

### Adding endpoints

1. Go to **Providers → Add Connection** and select **Docker Host** under "Container Discovery"
2. Enter a name and the Docker host URL (e.g. `unix:///var/run/docker.sock`)
3. Click "Add Docker Endpoint"

You can also add endpoints during the initial Setup wizard.

### Container discovery

1. Select an endpoint from the dropdown
2. Click "Discover containers"
3. Review discovered containers with confidence scores
4. Import selected containers as services

Vauxtra reads Traefik labels and suggests hostnames, ports, and routing rules automatically.

---

## 5) Provider Setup

Use **Providers → Add Connection** to add a new integration. Providers are organized by category:
- **External DNS** — Cloudflare DNS
- **Zero Trust** — Cloudflare Tunnel
- **Local DNS** — Pi-hole, AdGuard Home
- **Reverse Proxy** — Nginx Proxy Manager, Traefik

Choose **Guided setup** for step-by-step instructions, or **Expert mode** if you already have all credentials ready.

### Nginx Proxy Manager (NPM)

1. In NPM, go to **Users** and create a dedicated API user (or use admin)
2. In Vauxtra: Add provider → NPM → enter URL (`http://npm:81`) and credentials
3. Test connection

### Traefik (read-only)

Traefik is read-only in Vauxtra — it reads existing routes but does not modify them.

1. Expose the Traefik API (e.g., `--api.insecure=true` or dashboard router on port 8080)
2. In Vauxtra: Add provider → Traefik → enter API URL
3. Use **Sync → Import** to import existing routes

### Cloudflare DNS

1. Create an API token: My Profile → API Tokens → Create Token
2. Use "Edit zone DNS" template, or Custom Token with Zone → DNS → Edit
3. In Vauxtra: Add provider → Cloudflare → enter token and account email

### Cloudflare Tunnel

1. Create a tunnel: Zero Trust → Networks → Tunnels → Create
2. Copy the Tunnel ID (UUID)
3. Create API token with these permissions:
   - **Account → Cloudflare Tunnel → Edit** (required for route management)
   - **Zone → DNS → Edit** (required for DNS records)
4. In Vauxtra: Add provider → Cloudflare Tunnel → enter tunnel ID, account ID, and token

**Note on validation warnings:**
- `tunnel_config_write: Write probe skipped (safe mode)` — normal, write is only tested when actually pushing
- `zone_lookup: No hostname hint provided` — normal, DNS zones are checked when you create a service with a specific domain

### Pi-hole

1. Find your API token: Settings → API / Web interface → Show API token
2. In Vauxtra: Add provider → Pi-hole → enter URL and token

### AdGuard Home

1. Use your admin panel credentials (same as web login)
2. In Vauxtra: Add provider → AdGuard → enter URL and credentials

---

## 6) Service Workflow

### Recommended workflow

1. **Save** the service
2. **Push**: `POST /api/services/{id}/push` — apply to providers
3. **DNS check**: `POST /api/services/{id}/check-dns-propagation` — verify DNS records

### Drift detection

Drift occurs when provider state differs from Vauxtra's expected state (e.g., someone modified NPM directly).

- **Check drift**: `GET /api/services/{id}/drift`
- **Reconcile**: `POST /api/services/{id}/reconcile` — re-push to fix drift

### UI workflow

- **Expose modal**: configure service routing and push to providers
- **Services page**: check drift + reconcile per service

---

## 7) Notifications

Vauxtra uses [Apprise](https://github.com/caronc/apprise) format for webhooks.

### Supported services

- **Discord**: `discord://webhook_id/webhook_token`
- **Slack**: `slack://token_a/token_b/token_c`
- **Telegram**: `tgram://bot_token/chat_id`
- **Pushover**: `pover://user_key/api_token`
- **Email**: `mailto://user:pass@smtp.example.com`
- [Full list](https://github.com/caronc/apprise/wiki)

### Events

Notifications are sent for:

- Service health changes (down/recovered)
- Drift detected
- Auto-reconcile results

---

## 8) Theme: Light / Dark / Auto

Auto mode follows your system preference (`prefers-color-scheme`).

- **Light**: forces light theme
- **Dark**: forces dark theme
- **Auto**: follows system setting

Toggle in the sidebar or Settings page.

---

## 9) WAN Auto-Target Policy

Settings → General lets you configure how Vauxtra determines your public IP:

- **WAN resolver sources**: Services to query (ipify, ifconfig.me, etc.)
- **Timeout**: How long to wait for each source
- **Priority**: Which IP to prefer (`server_public_ip`, `proxy_provider_host`, `current`)

This is used for:

- Public target suggestion in the Expose modal
- DNS A-record resolution in auto mode
- Scheduler's auto DNS updates

---

## 10) MCP Integration

Vauxtra includes an MCP (Model Context Protocol) server for AI assistants.

### Setup

1. Create an API key: **Settings → API Keys → New Key**
2. Note the key (shown once)

### Claude Desktop config

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vauxtra": {
      "command": "python",
      "args": ["-m", "vauxtra_mcp.server"],
      "cwd": "/path/to/vauxtra",
      "env": {
        "VAUXTRA_URL": "http://localhost:8888",
        "VAUXTRA_API_KEY": "vx_your_key_here"
      }
    }
  }
}
```

### Available MCP tools

**Services**
| Tool | Description |
|---|---|
| `list_services` | List all services with health status |
| `get_service` | Get full details of a service |
| `create_service` | Create a new service |
| `update_service` | Update an existing service |
| `delete_service` | Delete a service |
| `toggle_service` | Enable/disable a service |
| `sync_services_from_providers` | Discover services from all providers |
| `import_services_from_sync` | Import discovered services |
| `get_service_topology` | Get service-to-provider mapping |

**Operations**
| Tool | Description |
|---|---|
| `run_preflight` | Run preflight checks before creating |
| `dry_run_push` | Preview push changes |
| `push_service` | Push to providers |
| `check_drift` | Detect drift |
| `reconcile_service` | Fix drift automatically |
| `check_dns_propagation` | DNS propagation check (3 resolvers) |

**Providers**
| Tool | Description |
|---|---|
| `list_providers` | List all providers |
| `get_provider_types` | Get supported provider types |
| `test_provider` | Test provider connection |
| `get_provider_health` | Get provider health |
| `get_tunnel_health` | Aggregate tunnel health |
| `get_provider_services` | Get services from a provider |
| `get_provider_dns_records` | Get DNS records from a provider |
| `get_provider_tunnel_routes` | Get tunnel routes |

**Docker**
| Tool | Description |
|---|---|
| `list_docker_endpoints` | List Docker endpoints |
| `discover_docker_containers` | Discover containers |
| `import_docker_containers` | Import containers as services |

**Monitoring**
| Tool | Description |
|---|---|
| `get_health` | System health |
| `get_logs` | Retrieve logs |
| `get_stats` | Global counters |
| `get_certificates` | List SSL certificates |
| `get_certificate_expiry` | Certificate expiry info |
| `check_all_services` | Trigger health check for all |

---

## 11) API Reference

When `DEBUG=true`, interactive docs are available at `/api/docs`.

All endpoints accept `Authorization: Bearer <api_key>` or session cookies.

### Authentication

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/auth/me` | Current auth status |
| `POST` | `/api/auth/login` | Login with password |
| `POST` | `/api/auth/logout` | End session |
| `POST` | `/api/auth/setup-password` | Set initial password (first launch) |
| `POST` | `/api/auth/setup-complete` | Mark setup wizard as completed |
| `POST` | `/api/auth/change-password` | Change password |

### Services

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/services` | List all services |
| `POST` | `/api/services` | Create a service |
| `GET` | `/api/services/history` | Recent service activity |
| `GET` | `/api/services/public-target/suggest` | Suggest public target IP |
| `POST` | `/api/services/preflight` | Preflight validation |
| `POST` | `/api/services/sync` | Discover services from all providers |
| `POST` | `/api/services/import` | Import services from sync |
| `POST` | `/api/services/check-all` | Trigger health check for all |
| `PUT` | `/api/services/{sid}` | Update a service |
| `DELETE` | `/api/services/{sid}` | Delete a service |
| `POST` | `/api/services/{sid}/push` | Push to providers |
| `POST` | `/api/services/{sid}/push/dry-run` | Dry-run push (preview) |
| `GET` | `/api/services/{sid}/drift` | Check for drift |
| `POST` | `/api/services/{sid}/reconcile` | Fix drift |
| `GET` | `/api/services/{sid}/check` | Single health check |

### Providers

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/providers` | List all providers |
| `POST` | `/api/providers` | Add a provider |
| `GET` | `/api/providers/types` | Supported provider types |
| `GET` | `/api/providers/health` | All providers health (batch) |
| `GET` | `/api/providers/tunnels/health` | Tunnel providers health |
| `POST` | `/api/providers/validate-draft` | Validate before saving |
| `PUT` | `/api/providers/{pid}` | Update a provider |
| `DELETE` | `/api/providers/{pid}` | Delete a provider |
| `GET` | `/api/providers/{pid}/health` | Single provider health |
| `POST` | `/api/providers/{pid}/test` | Test connection + diagnostics |
| `POST` | `/api/providers/{pid}/validate` | Validate permissions |

### Docker

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/docker/endpoints` | List Docker endpoints |
| `POST` | `/api/docker/endpoints` | Add a Docker endpoint |
| `POST` | `/api/docker/endpoints/{id}/test` | Test Docker endpoint |
| `POST` | `/api/docker/endpoints/{id}/default` | Set as default endpoint |
| `DELETE` | `/api/docker/endpoints/{id}` | Delete Docker endpoint |
| `GET` | `/api/docker/containers` | Discover containers |
| `POST` | `/api/docker/import` | Import containers as services |

### Certificates

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/certificates` | List certificates (from NPM) |
| `GET` | `/api/certificates/expiry` | Certificates with expiry info |

### Settings & Admin

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/settings` | Get settings |
| `POST` | `/api/settings` | Update settings |
| `POST` | `/api/settings/test-webhook` | Test webhook notification |
| `GET` | `/api/logs` | Get logs (supports `?level=` filter) |
| `GET` | `/api/logs/stream` | SSE log stream |
| `POST` | `/api/logs/clear` | Clear logs |
| `GET` | `/api/stats` | Global counters |
| `GET` | `/api/health` | System health check |
| `POST` | `/api/reset` | Factory reset (⚠️ destructive) |

### Backup & Restore

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/backup` | Export backup (credentials cleared) |
| `POST` | `/api/backup/secure` | Export with encrypted credentials |
| `POST` | `/api/restore` | Restore from backup |

### Tags & Environments

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/tags` | List tags |
| `POST` | `/api/tags` | Create a tag |
| `PUT` | `/api/tags/{tid}` | Update a tag |
| `DELETE` | `/api/tags/{tid}` | Delete a tag |
| `GET` | `/api/environments` | List environments |
| `POST` | `/api/environments` | Create an environment |
| `PUT` | `/api/environments/{eid}` | Update an environment |
| `DELETE` | `/api/environments/{eid}` | Delete an environment |

### Domains

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/domains` | List registered domains |
| `POST` | `/api/domains` | Add a domain |
| `DELETE` | `/api/domains/{name:path}` | Delete a domain |

### Webhooks & Alerts

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/webhooks` | List webhooks |
| `POST` | `/api/webhooks` | Create a webhook |
| `PUT` | `/api/webhooks/{wid}` | Update a webhook |
| `DELETE` | `/api/webhooks/{wid}` | Delete a webhook |
| `POST` | `/api/webhooks/test-url` | Test a webhook URL |
| `POST` | `/api/webhooks/{wid}/test` | Test existing webhook |
| `GET` | `/api/services/{sid}/alerts` | Get service alert config |
| `POST` | `/api/services/{sid}/alerts` | Set service alert config |

### API Keys

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/settings/api-keys` | List API keys |
| `POST` | `/api/settings/api-keys` | Create an API key |
| `DELETE` | `/api/settings/api-keys/{key_id}` | Revoke an API key |

---

## 12) Troubleshooting

### "Invalid password" but password is correct

If you set `APP_PASSWORD` in `.env` after setting a password via the wizard, the env var takes priority. Check your `.env` file.

### Provider credentials not working after restore

You likely restored a backup with a different `SECRET_KEY`. Credentials are encrypted with the key — you need to re-enter them or restore the original `.secret_key` file.

### Cloudflare Tunnel routes not updating

1. Check the API token has `Account → Cloudflare Tunnel → Edit` permission
2. Verify the Account ID is correct (32-char hex, not zone ID)
3. Use the "Validate" button to diagnose

### NPM connection fails

1. Verify NPM is reachable from the Vauxtra container
2. Check the port (usually 81, not 80)
3. Try with admin credentials first, then create a dedicated user

### Docker discovery returns empty

1. Check the Docker socket is mounted (`/var/run/docker.sock`)
2. For remote hosts, verify TCP/SSH connectivity
3. Ensure containers are running (not exited)
