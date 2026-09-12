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
10. [DNS-Only vs Reverse Proxy](#10-dns-only-vs-reverse-proxy)
11. [MCP Integration](#11-mcp-integration)
12. [Service Templates](#12-service-templates)
13. [Prometheus Metrics](#13-prometheus-metrics)
14. [API Reference](#14-api-reference)
15. [Troubleshooting](#15-troubleshooting)

---

## Notes

- The in-app "How-To & API" panel has been removed to avoid duplicated/dated guidance.
- This file is now the single source of truth for end-user operations.

---

## 1) First Launch & Setup Wizard

On first launch, Vauxtra displays a guided setup wizard to help you configure:

1. **Password** — Protect access to your panel (optional — you can skip for open access)
2. **Providers** — Connect reverse proxies (NPM, Traefik, Zoraxy) and DNS providers (Cloudflare, Pi-hole, AdGuard)
3. **Notifications** — Add webhooks for alerts (Discord, Slack, Telegram, etc.)
4. **Docker endpoints** — Connect Docker hosts for container discovery

The wizard includes step-by-step guided instructions for each provider type, with links to where you can find API tokens and credentials.

You can skip any step and configure it later from the Settings page.

---

## 2) Authentication

Vauxtra uses a password to protect access to the panel.

### Setting a password

- **Setup wizard**: On first launch, the wizard prompts you to choose a password.
- **Environment variable**: Set `APP_PASSWORD` in your `.env` file to a **PBKDF2 hash** (takes priority over the UI-configured password).
- **No password**: Leave both empty for open access. Every request then carries the admin
  scope with no credential at all — anyone who can reach the port can add providers, read
  the decrypted credentials of the ones already there, and delete your services. Vauxtra
  logs a warning at each boot and shows a permanent banner in the interface while this is
  the case; setting a password later is one form in **Settings → API keys**.

Generate the hash with the same function the wizard uses:

```bash
docker compose exec vauxtra \
  python -c "from app.auth import hash_password; print(hash_password('your-password'))"
# pbkdf2:sha256:600000$<salt>$<hash>   ← this is what goes in APP_PASSWORD
```

### Password storage

Passwords set via the Setup wizard are stored as PBKDF2-HMAC-SHA256 hashes (600k iterations) in the database.

`APP_PASSWORD` is expected to hold a hash in that same format. A value that is **not** a
hash is refused: the login returns 401 and the setup wizard stays closed, because a
non-empty `APP_PASSWORD` counts as "a password is configured". The server logs an explicit
error when this happens — check the container logs if a password you are sure about is
rejected.

To keep a plaintext value anyway — a lab instance, a migration you have not finished — set
`ALLOW_PLAINTEXT_APP_PASSWORD=true`. It is off by default, and it means the password sits
in clear text in your `.env`, your shell history and `docker inspect`.

### Forgot your password?

- **If set via `.env`**: Edit the file, change or remove `APP_PASSWORD`, restart the container.
- **If set via Setup wizard**: connect to the database and delete **both** rows:
  ```bash
  sqlite3 data/vauxtra.db \
    "DELETE FROM settings WHERE key IN ('app_password_hash','auth_mode');"
  ```
  Then restart the container. The instance is open again and the interface offers the
  password form, with the banner up until you use it.

  Deleting `app_password_hash` alone is **not** enough, and that is deliberate: the
  `auth_mode` row is what tells Vauxtra this instance was protected. With the hash gone and
  the marker still there, every request is refused with an explicit message rather than
  granted the admin scope. That way a database restored from the wrong file, or a partial
  recovery, cannot quietly turn a protected instance into an open one.

### "The hash is no longer in the database"

If every request returns 401 with that message, the two rows disagree: `auth_mode` says a
password was set, `app_password_hash` is missing. Either restore the database, or choose
one of the two ways out:

```bash
# Keep the protection: set a hash in the environment and restart
docker compose exec vauxtra \
  python -c "from app.auth import hash_password; print(hash_password('new-password'))"
# put the value in APP_PASSWORD

# Or go back to open access on purpose (see the warning above)
sqlite3 data/vauxtra.db "DELETE FROM settings WHERE key='auth_mode';"
```

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
- **Reverse Proxy** — Nginx Proxy Manager, Traefik, Zoraxy

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

### Zoraxy

Vauxtra drives Zoraxy through its management API (port 8000 by default): it logs in with
the admin username/password, keeps the session cookie and sends the CSRF token Zoraxy
requires on every write.

1. Note the management URL (`http://zoraxy:8000`) and the admin credentials
2. In Vauxtra: Add provider → Zoraxy → enter URL, username and password
3. Test connection, then **Sync → Import** to pick up existing host rules

**No dedicated account:** Zoraxy has exactly one admin account and no API keys, so Vauxtra
holds the same credentials as your browser session. Keep port 8000 on the LAN or behind the
VPN; never expose it to the internet.

**`-noauth` instances:** leave username and password empty. Vauxtra skips the login and only
checks that the instance answers as authenticated.

What Vauxtra does on a Zoraxy rule:
- Creates, updates, enables/disables and deletes the rule (hostname → `ip:port`)
- Sets the upstream scheme; for HTTPS upstreams the certificate validation is skipped
- Turns WebSocket support on or off
- Lists Zoraxy certificates and records the one whose CN matches the hostname (or its
  parent wildcard) as the rule's preferred certificate
- Imports existing rules and reports drift when the live rule no longer matches the service

Limitations:
- Only **host** rules are managed — no virtual directories, no TCP/UDP stream proxies
- One upstream per rule; extra load-balanced upstreams are left untouched
- Zoraxy terminates TLS globally, so there is no per-host "force SSL" switch
- The preferred certificate is recorded, but Zoraxy still picks certificates by SNI unless
  SNI matching is disabled on the rule
- A rule renamed in Zoraxy is seen as drift, because the hostname is the rule's identifier
- Creating a service for a hostname that already has a rule in Zoraxy is refused rather
  than overwriting the rule (Zoraxy's own `add` would replace it silently): import the
  rule first, then edit the service

### Cloudflare DNS

1. Create an API token: My Profile → API Tokens → Create Token
2. Use "Edit zone DNS" template, or Custom Token with Zone → DNS → Edit
3. In Vauxtra: Add provider → Cloudflare → enter token
4. Optional: set Zone ID when you want to force a single zone

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
2. In Vauxtra: Add provider → Pi-hole → enter base URL and token/password
3. Use base URL (e.g. `http://pihole` or `http://localhost:18081`), not `/admin`

### AdGuard Home

1. Use your admin panel credentials (same as web login)
2. In Vauxtra: Add provider → AdGuard → enter URL and credentials

---

## 6) Service Workflow

### Recommended workflow

1. **Save** the service
2. **Push**: `POST /api/services/{id}/push` — apply to providers
3. **Verify**: check service health on the Services page

### One hostname, one service

Two services publishing the same hostname push over each other: whichever syncs last owns
the proxy host and the DNS record, and the drift check then reports the other as permanently
wrong. Creating or renaming onto a taken hostname answers **409** and names the service that
already holds it. A unique index backs this up in the database for anything that bypasses the
API (a restore, a hand edit). An installation that already contains duplicates keeps starting
normally and says which rows to merge — being unable to boot is not a way to fix data.

### Reading the answer to a push

```json
{ "ok": false, "errors": ["Proxy (NPM): the provider refused the update of host 12. ..."] }
```

`ok` is `false` as soon as one target refused, and `errors` names which. Providers report a
refusal by *returning* a failure, not by raising one — an expired NPM token, a Cloudflare
token missing `Zone:DNS:Edit`, a Pi-hole answering 401 — and they give no reason, so the
message says where to look instead of inventing one. The matching `[Push] … refused …` line
lands in the journal; a `[Push] … synced` line now only ever means the provider accepted.

One case is deliberate: if the stale DNS record could not be removed, the new one is **not**
created. AdGuard and Pi-hole will happily hold two rewrites for the same name, and the host
would then resolve to whichever the resolver picked.

### Reading the answer to an import

`POST /api/services/import` takes the rows a scan found and answers with four outcomes:

```json
{
  "imported": 3,
  "linked": 1,
  "skipped": ["Import passed over nas.example.lan: Vauxtra already tracks this name"],
  "errors": ["Import skipped proxy host 91 on NPM: the provider listed no domain name for it"]
}
```

Four, because two could not tell you anything. `imported` is a new service. `linked` is an
existing service that gained the DNS half it was missing: a real write, and one the older
two-field answer counted nowhere, so re-scanning after adding a rewrite reported zero.
`skipped` is a row passed over **on purpose** — it is already tracked, or it is the second
name on a proxy host that carries several, and a service holds a single name. It is not a
failure and the panel does not paint it as one. `errors` is a row that is *wrong*: no domain
name, no address, no dot to split a subdomain off, or an import that raised. Each one names
the row, so there is something to go and fix.

Apart from one fold, every row you submit lands in exactly one of the four. The fold: a DNS
record whose name matches a proxy host **in the same payload** is the other half of that
host, not a second service, so the two rows produce one service counted once under
`imported`. `skipped` can also carry extra lines for names *inside* a row, so it is the one
bucket that can outrun the row count.

When two DNS providers answer for the same name, the first record is kept and the refusal
names both providers and says which answer was imported — the other one is what you remove.
Only refusals go to the journal one line at a time; rows passed over are summed into a
single line, because fifty of them would bury everything else in *Recent activity*.

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

### The URL is the credential

Look at those formats: the token *is* the URL. Anyone holding
`discord://webhook_id/webhook_token` can post to that channel — there is no separate
password to clear, and no way to show the URL without handing over the ability to use it.

So a notification URL only ever travels inward. It is never returned by any route, in any
scope, to anyone:

- `GET /api/webhooks` and `GET /api/services/{sid}/alerts` return `url_masked` /
  `webhook_url_masked` (`discord://***`) and no `url` field at all. The create and update
  responses answer the same way.
- The legacy global `webhook_url` no longer exists as a setting. It could be written, it
  was masked on the way out, and it delivered nothing — alerting reads the `webhooks`
  table. Any value already stored is moved into that table on the next start, as a target
  named *Global notifications (migrated)*; writing the key now returns 400 and points at
  `POST /api/webhooks`.
- The `[Webhook]` log lines are masked too. Before this, one failed delivery wrote the token
  into the `logs` table, which every `read` key can read; the migration deletes those rows
  once, on the next start.
- `GET /api/backup` — the export whose own flag says `secrets_included: false` — leaves the
  URL out entirely. `POST /api/backup/secure` encrypts it with your passphrase, alongside the
  provider passwords.

The consequence to know about: **to change a URL you retype it**. The UI shows you which
service a webhook points at, never the token. And restoring a *plain* backup brings your
notification targets back **disabled**, with `webhooks_needing_url` in the response saying
how many — their names, scopes and rules survive, only the one field a secret-free file
cannot carry is missing. Restoring a secure backup restores them working.

`POST /api/restore` names two other things it could not take. `settings_not_restored` lists
every setting in the file this version does not accept — a key an older Vauxtra wrote, or a
newer one — and *not restored* is literal: the restore empties the settings table before
refilling it, so such a key ends up absent rather than keeping the value this instance had.
`domains_without_name` counts domain rows in the file with no name, which cannot be
recreated; services that referenced one come back pointing at a domain the list no longer
offers. Both also write a single journal line each. The keys a restore drops **on purpose**
— the admin password hash, the setup marker, the schema version, the auth mode, the session
epoch, and the one-shot webhook log purge marker — are never reported, because a warning
that fires on every restore is one you learn to skip.

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

## 10) DNS-Only vs Reverse Proxy

Vauxtra supports both patterns:

1. **DNS + Reverse Proxy** (common for web apps)
2. **DNS-only** (no reverse proxy), useful for LAN-only routes

### Provider capability model (scalable)

Vauxtra UI/logic uses provider capabilities instead of hardcoded provider names.

- `dns=true`: provider can receive DNS records
- `public_dns=true`: provider publishes records on the public internet (WAN scope)
- `supports_auto_public_target=true`: provider can use WAN auto-target mode

This keeps behavior stable when new providers are added later.

### DNS-only with local DNS providers (Pi-hole / AdGuard / local authoritative DNS)

- Scope: local network clients
- Recommended target: LAN IP/FQDN of the service endpoint clients should reach
- In DNS-only mode, no separate DNS target field is shown in the UI
- Vauxtra uses the internal target host/IP as the DNS record target for local DNS providers

### DNS + Reverse Proxy with local DNS providers

- DNS must point to the reverse proxy endpoint, not directly to the backend service
- Use the **Reverse proxy LAN IP** field (LAN IP/FQDN of NPM/Traefik/Zoraxy)
- When available, Vauxtra pre-fills this field from the selected proxy provider URL host

### DNS with external providers (Cloudflare, etc.)

- Scope: public internet
- Recommended target: WAN IP/FQDN
- Auto target detection uses WAN policy (section 9)
- Ensure router forwarding is aligned for exposed protocols (typically 80/443)

### Why this distinction matters

Using a WAN target for local DNS, or a LAN target for external DNS, creates confusing incidents that look like provider failures while DNS data itself is valid. The capability model reduces this class of configuration drift.

---

## 11) MCP Integration

Vauxtra includes an MCP (Model Context Protocol) server for MCP-compatible clients.

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

**Operations**
| Tool | Description |
|---|---|
| `run_preflight` | Run preflight checks before creating |
| `dry_run_push` | Preview push changes |
| `push_service` | Push to providers |
| `check_drift` | Detect drift |
| `reconcile_service` | Fix drift automatically |

**Providers**
| Tool | Description |
|---|---|
| `list_providers` | List all providers |
| `get_provider_types` | Get supported provider types |
| `test_provider` | Test provider connection |
| `get_provider_health` | Get provider health |
| `get_all_providers_health` | Get all providers health (batch) |
| `get_tunnel_health` | Aggregate tunnel health |
| `create_provider` | Create a new provider |
| `update_provider` | Update an existing provider |
| `delete_provider` | Delete a provider (409 while services use it; `force=True` unlinks them) |

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

## 12) Service Templates

Service Templates are pre-configured blueprints that pre-fill the service creation form. Instead of filling in provider assignments, scheme, port, domain, and tags every time, you save those defaults once and apply them in one click.

### What a template stores

| Field | Description |
|---|---|
| `name` | Template display name (unique) |
| `description` | Optional notes |
| `forward_scheme` | `http` or `https` |
| `target_port` | Default backend port (1–65535) |
| `websocket` | Enable WebSocket support |
| `expose_mode` | `proxy_dns` or `tunnel` |
| `proxy_provider_id` | Pre-selected reverse proxy |
| `dns_provider_id` | Pre-selected DNS provider |
| `tunnel_provider_id` | Pre-selected Cloudflare Tunnel |
| `public_target_mode` | `manual` or `auto` |
| `domain` | Default base domain |
| `dns_ip` | Default DNS record target |
| `tag_ids` | Default tags to attach |
| `icon_url` | Service icon URL |

### What a template refuses

A template is a service payload saved for later, so it is checked with the rules of the
service form — once, when you type it, instead of every time you apply it.

| Field | Accepted values |
|---|---|
| `forward_scheme` | `http` or `https` |
| `expose_mode` | `proxy_dns` or `tunnel` |
| `public_target_mode` | `manual` or `auto` |
| `target_port` | 1–65535 |
| `domain` | empty, or a valid domain name |
| `dns_ip` | empty, or an IP address or hostname |

Anything else answers `422`. `domain` and `dns_ip` may be left empty on purpose — that is
what makes a template a template — but a value that is there has to be a usable one.

Every `proxy_provider_id`, `dns_provider_id`, `tunnel_provider_id` and `tag_ids` entry has
to name a row that exists. If one does not, the call is refused with `400`, the id is named,
and nothing is written:

```json
{ "detail": "Nothing was created -- unknown tag 12, provider 4" }
```

A provider or a tag deleted *after* the template was saved is not an error and is never
reported as one: the provider field empties itself, which the database does on its own, and
the tag stops being listed. The stored template is not rewritten, so putting the tag back
restores it.

### Using templates from the UI

1. Go to **Settings → Templates → New Template**
2. Fill in the defaults you want
3. Save the template
4. When creating a service, click **Apply Template** and choose a template — the form pre-fills with the stored defaults
5. Adjust any fields as needed and save

### Using templates via API

```bash
# List templates
GET /api/templates

# Create a template
POST /api/templates
{
  "name": "Internal HTTPS App",
  "forward_scheme": "https",
  "target_port": 443,
  "websocket": false,
  "expose_mode": "proxy_dns",
  "domain": "home.local",
  "dns_ip": "192.168.1.10"
}

# Apply a template (get pre-filled service defaults)
GET /api/templates/{id}/apply
# Returns: forward_scheme, target_port, websocket, expose_mode,
#          proxy_provider_id, dns_provider_id, tunnel_provider_id,
#          public_target_mode, domain, dns_ip, tag_ids, icon_url,
#          _template_id, _template_name
```

Apply returns the template fields merged as service-creation defaults. You can POST those directly to `/api/services` (add `name`, `subdomain`, and `internal_target` to complete the service).

### Using templates via MCP

```
list_templates          → list all templates
get_template(id)        → get a single template
create_template(...)    → create a template
delete_template(id)     → delete a template
apply_template(id, ...) → apply defaults + create the service
```

`apply_template` is the power tool: it fetches defaults from the template, merges any overrides you supply, and creates the service in one call.

---

## 13) Prometheus Metrics

Vauxtra exposes a Prometheus-compatible metrics endpoint with no authentication required — designed to be scraped directly by a Prometheus server.

### Endpoint

```
GET /metrics
```

No `Authorization` header required. Response is `text/plain` in Prometheus text exposition format.

### Available metrics

| Metric | Labels | Description |
|---|---|---|
| `vauxtra_services_total` | `status` (`ok`, `error`, `unknown`) | Services by health status |
| `vauxtra_providers_total` | `type`, `state` (`enabled`, `disabled`) | Providers by type and enabled state |
| `vauxtra_logs_24h` | `level` (`info`, `warn`, `error`, `debug`) | Log entries in the last 24 hours |
| `vauxtra_uptime_events_24h` | `event` (`up`, `down`) | Service uptime events in the last 24 hours |
| `vauxtra_webhooks_total` | `state` (`enabled`, `disabled`) | Configured webhooks |
| `vauxtra_webhook_deliveries_total` | `status` (`pending`, `delivered`, `failed`) | Webhook delivery log entries |
| `vauxtra_templates_total` | *(none)* | Number of service templates |
| `vauxtra_schema_version` | *(none)* | Current database schema version |

### Example output

```
# HELP vauxtra_services_total Services by status
# TYPE vauxtra_services_total gauge
vauxtra_services_total{status="ok"} 5
vauxtra_services_total{status="error"} 1
vauxtra_services_total{status="unknown"} 2
# HELP vauxtra_providers_total Providers by type and state
# TYPE vauxtra_providers_total gauge
vauxtra_providers_total{type="npm",state="enabled"} 1
vauxtra_providers_total{type="cloudflare",state="enabled"} 1
vauxtra_schema_version 10
```

### Prometheus scrape config

```yaml
# prometheus.yml
scrape_configs:
  - job_name: vauxtra
    static_configs:
      - targets: ['vauxtra:8888']
    metrics_path: /metrics
    scrape_interval: 60s
```

### Alerting examples

```yaml
# alert.rules.yml
groups:
  - name: vauxtra
    rules:
      - alert: VauxtraServiceDown
        expr: vauxtra_services_total{status="error"} > 0
        for: 5m
        annotations:
          summary: "{{ $value }} service(s) in error state"
      - alert: VauxtraCertExpiringSoon
        expr: vauxtra_logs_24h{level="error"} > 0
        for: 1m
        annotations:
          summary: "Check Vauxtra logs — certificate may be expiring"
```

---

## 14) API Reference

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
| `POST` | `/api/services` | Create a service — 400 naming any unknown tag/environment/provider id, 409 if the hostname is taken |
| `GET` | `/api/services/history` | Recent service activity |
| `GET` | `/api/services/public-target/suggest` | Suggest public target IP |
| `POST` | `/api/services/preflight` | Preflight validation. A check marked `blocking` is a promise that the save route refuses the same body. Add `service_id` to preflight an edit: the public target is then resolved from that service's stored row, exactly as the `PUT` resolves it. |
| `POST` | `/api/services/sync` | Discover services from all providers |
| `POST` | `/api/services/import` | Import services from sync |
| `POST` | `/api/services/check-all` | Trigger health check for all. Returns `results`: `{id, status, latency_ms}` per probed service. |
| `PUT` | `/api/services/{sid}` | Update a service — same 400 / 409 as the creation, missing provider target and unresolvable public DNS target included |
| `DELETE` | `/api/services/{sid}` | Delete a service |
| `POST` | `/api/services/{sid}/push` | Push to providers |
| `POST` | `/api/services/{sid}/push/dry-run` | Dry-run push (preview) |
| `GET` | `/api/services/{sid}/drift` | Check for drift |
| `POST` | `/api/services/{sid}/reconcile` | Fix drift |
| `POST` | `/api/services/{sid}/check` | Single health check. The `GET` of the same path is a deprecated alias kept for one version; both need the `write` scope, because the check writes `status`, `last_checked` and an uptime event. |

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
| `POST` | `/api/settings` | Update settings — send only the keys you change; 400 (and nothing written) on an invalid value |
| `POST` | `/api/settings/test-webhook` | Send a test notification to every enabled webhook; answers `{ok, results[]}` with one entry per target |
| `GET` | `/api/logs` | Get logs (supports `?level=` filter) |
| `GET` | `/api/logs/stream` | SSE log stream |
| `POST` | `/api/logs/clear` | Clear logs |
| `GET` | `/api/stats` | Global counters |
| `GET` | `/api/health` | System health check |
| `POST` | `/api/reset` | Factory reset (⚠️ destructive) |

### Backup & Restore

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/backup` | Export backup (credentials cleared — including notification URLs) |
| `POST` | `/api/backup/secure` | Export with encrypted credentials (passwords **and** notification URLs) |
| `POST` | `/api/restore` | Restore from backup |

### Service Templates

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/templates` | List all templates |
| `POST` | `/api/templates` | Create a template |
| `GET` | `/api/templates/{id}` | Get a template |
| `PUT` | `/api/templates/{id}` | Update a template |
| `DELETE` | `/api/templates/{id}` | Delete a template |
| `GET` | `/api/templates/{id}/apply` | Get pre-filled service defaults from template |

### Metrics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/metrics` | Prometheus metrics (no auth required) |

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
| `GET` | `/api/webhooks` | List webhooks (URLs masked — see [The URL is the credential](#the-url-is-the-credential)) |
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

### Scopes

A key carries one or more of `read`, `write`, `admin`. They are hierarchical: `admin`
satisfies `write`, `write` satisfies `read`. A UI session is always `admin`; a key is only
what it was created with. A request that falls short is refused with
`403 Insufficient scope: '<scope>' required`.

| Scope | Covers |
|---|---|
| `read` | Every `GET` except the deprecated `GET /api/services/{sid}/check`, which writes and therefore needs `write` like its `POST`. Plus the read-only diagnostics: `/api/services/{sid}/push/dry-run`, `/api/services/sync`. |
| `write` | Everything that changes state — create/update/delete of services, providers, tags, environments, domains, templates, webhooks — plus anything the server acts on from the outside: `/api/services/preflight`, `/api/services/check-all`, `/api/services/{sid}/check`, `/api/providers/{pid}/test`, `/api/providers/{pid}/validate`, `/api/providers/validate-draft`, `/api/settings/test-webhook`, `/api/webhooks/test-url`, `/api/docker/endpoints/{id}/test`. |
| `admin` | Credentials and the whole instance: `/api/auth/change-password`, `/api/auth/setup-complete`, `/api/settings/api-keys*`, `/api/backup*`, `/api/restore`, `/api/reset`. |

Two things a `write` key may **not** do, because they choose a URL rather than a value,
and the server is what goes and fetches it:

- `public_target_sources` through `POST /api/settings` — the resolvers Vauxtra polls to
  discover its own WAN address. A key that could set them could aim the scheduler at any
  host reachable from the container, including one on the deployment's own network. The
  other two WAN-policy keys stay at `write`: `public_target_timeout` is a number, and
  `public_target_priority` is one of three fixed words.
- Apprise URLs on the six generic schemes — `json://`, `jsons://`, `form://`, `forms://`,
  `xml://`, `xmls://` — through `POST /api/webhooks`, `PUT /api/webhooks/{id}` and
  `POST /api/webhooks/test-url`. Every other scheme names a service (`discord://`,
  `tgram://`, `ntfy://`); these six name a host and a body, which is a notification for an
  operator and an arbitrary outbound POST for anyone else. `test-url` sends one straight
  away, without storing anything.

Both are refused with the same `403 Insufficient scope: 'admin' required`. Neither is
blocked outright — posting JSON to your own service is a fair reason to run a tool like
this — and a UI session, always `admin`, never meets either rule. A partial update that
does not carry a `url` field is not affected: toggling `enabled` on a webhook an admin
created stays a `write`.

A `read` key is deliberately refused on the test and preflight routes. They take a target
host and port from the request and make the server connect to it, or deliver a real
notification — side effects, not reads, even though nothing in Vauxtra's own database
changes.

The setup routes are the one exception to the table: while the installation wizard has not
been completed they answer without any authentication at all, because there is nobody to
authenticate yet. As soon as `setup-complete` has been stored they fall back to the scope
listed above.

---

## 15) Troubleshooting

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
