# Vauxtra

> **The missing link in your network stack.**  
> Self-hosted DNS & reverse proxy management panel — built for homelab.  
> Orchestrate Nginx Proxy Manager, Traefik, Zoraxy, Cloudflare, Pi-hole, AdGuard Home, and more from one unified interface.

<div align="center">

[![Tests](https://github.com/ptitzgeg-on-git/vauxtra/actions/workflows/tests.yml/badge.svg)](https://github.com/ptitzgeg-on-git/vauxtra/actions/workflows/tests.yml)
[![Latest Release](https://img.shields.io/github/v/release/ptitzgeg-on-git/vauxtra?label=release)](https://github.com/ptitzgeg-on-git/vauxtra/releases)
[![Docker Image](https://ghcr-badge.egpl.dev/ptitzgeg-on-git/vauxtra/latest_tag?trim=major&label=ghcr)](https://github.com/ptitzgeg-on-git/vauxtra/pkgs/container/vauxtra)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61dafb)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135-green)](https://fastapi.tiangolo.com/)
[![MCP Ready](https://img.shields.io/badge/MCP-Ready-purple)](https://modelcontextprotocol.io/)

</div>

---

## Features

| Category | What you get |
|---|---|
| **Multi-provider routing** | Manage proxy hosts (NPM, Traefik, Zoraxy) and DNS records (Cloudflare, deSEC, Pi-hole, AdGuard, Technitium, PowerDNS) from a single service record |
| **Exposure modes** | Choose DNS-only, DNS + Reverse Proxy, or Tunnel with capability-aware guidance |
| **Cloudflare Tunnel** | Expose services without port-forwarding via Cloudflare Tunnel integration |
| **Docker discovery** | Auto-detect running containers with Traefik label parsing and confidence scoring |
| **Preflight & dry-run** | Validate routing config before pushing; preview changes without committing |
| **Drift detection** | Detect when live provider state diverges from expected and reconcile automatically |
| **Auto-reconcile scheduler** | Periodic background reconciliation with webhook notifications |
| **Service Templates** | Pre-configured blueprints that pre-fill the service form — one click to deploy a standard HTTPS app, internal tool, or tunnel service |
| **Certificate monitoring** | Track NPM and Zoraxy certificates; alerts at < 30 days (warn) and < 7 days (error) |
| **Webhook retry** | Failed Apprise notifications are retried with exponential backoff (1 min → 24 h); delivery log visible in metrics |
| **Prometheus metrics** | `/metrics` endpoint in Prometheus text format — scrape service health, provider counts, logs, webhooks, templates |
| **API Keys** | Bearer token auth for CI/CD pipelines and MCP server access |
| **MCP Server** | Expose core operations as tools for MCP-compatible clients and automation |
| **Webhook alerts** | Apprise-compatible webhooks for service down/recovery, provider state changes, and reconcile events |
| **Environments & Tags** | Organise services with colour-coded labels |
| **Multilingual UI** | English, French, German, Spanish, Portuguese, Dutch, Japanese, Chinese — 8 locales |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Vauxtra                             │
│                                                         │
│   React 19 + TypeScript + Vite (SPA)                    │
│          │                                              │
│   FastAPI + SQLite (WAL)                                │
│          │                                              │
│   ┌──────┴────────────────────────┐                     │
│   │         Providers             │                     │
│   │  NPM  Zoraxy  Traefik         │                     │
│   │  Cloudflare DNS  CF Tunnel    │                     │
│   │  Pi-hole  AdGuard  Technitium │                     │
│   │  PowerDNS  deSEC              │                     │
│   └───────────────────────────────┘                     │
└─────────────────────────────────────────────────────────┘
          │
  MCP Server (FastMCP) — client and automation integration
```

Vauxtra is an **orchestrator**: it does not run a reverse proxy or DNS server itself — it configures the ones you already have running.

---

## Quick Start (Docker)

Pull the pre-built image from GitHub Container Registry:

```bash
docker run -d \
  --name vauxtra \
  -p 127.0.0.1:8888:8888 \
  -v vauxtra_data:/app/data \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/ptitzgeg-on-git/vauxtra:latest
```

> The port is published on the loopback: the panel holds provider credentials and has
> none of its own until you complete the setup wizard. Once you have, `-p 8888:8888` reaches
> it from the LAN — behind a reverse proxy, preferably.

> The Docker socket is only needed for container discovery, and mounting it is a real
> grant: `:ro` applies to the socket file, not to the Docker API, so whoever reaches it can
> start a privileged container and is root on the host. Drop the `-v /var/run/docker.sock`
> line if you do not use the Docker features — the rest of Vauxtra is unaffected and the
> Docker screens answer "daemon unavailable" — or front it with a read-only socket proxy.

Or use Docker Compose:

```yaml
# docker-compose.yml
services:
  vauxtra:
    image: ghcr.io/ptitzgeg-on-git/vauxtra:latest
    ports:
      # Loopback by default; VAUXTRA_BIND=0.0.0.0 publishes on every interface.
      - "${VAUXTRA_BIND:-127.0.0.1}:8888:8888"
    volumes:
      - ./data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - TZ=${TZ:-UTC}
    restart: unless-stopped
```

```bash
docker compose up -d
```

Then open http://localhost:8888 in your browser.

The `data/` directory stores the SQLite database and the auto-generated secret key.

> **Build from source?** Clone the repo and run `docker compose up --build -d`.

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in your values.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | **Do not change after setup.** Used to sign session cookies and encrypt provider credentials. Auto-generated to `data/.secret_key` if left empty. |
| `APP_PASSWORD` | *(none)* | Password to protect the web interface. Leave empty to configure via Setup wizard. |
| `TZ` | `UTC` | Timezone for scheduler and log timestamps. |
| `HTTPS_ONLY` | `false` | Set to `true` whenever the interface is reached over `https://`, **including behind a reverse proxy that terminates TLS** — which is the usual deployment. Marks the session cookie `Secure` and sends HSTS. |
| `DEBUG` | `false` | Enable `/api/docs` and `/openapi.json`, add the Vite dev server to `CORS_ORIGINS`, and log verbosely. |
| `VAUXTRA_URL` | `http://localhost:8888` | Base URL of this instance (used by the MCP server). |
| `VAUXTRA_API_KEY` | *(none)* | API key for MCP server auth. Create one in **Settings → API Keys**. |
| `DOCKER_HOST` | *(env default)* | Docker socket path. Override if using a non-standard location. |
| `VAUXTRA_REWRITE_LOCALHOST` | `true` | Rewrite provider URLs using localhost/127.0.0.1 to a host alias when running inside Docker. |
| `VAUXTRA_LOCALHOST_ALIAS` | `host.docker.internal` | Hostname used when localhost rewrite is active. |
| `CORS_ORIGINS` | *(empty)* | Cross-origin callers allowed to reach the API, comma-separated. Empty is right for a normal install: the interface is served by this same application, so it is already same-origin. Fill it in only for a frontend hosted elsewhere — every origin listed may send the session cookie. |
| `FORWARDED_ALLOW_IPS` | *(empty)* | Address of the reverse proxy in front of this instance. Unset, every rate limit keys on the socket peer — behind a proxy that is one address for every visitor, so five failed logins from anywhere lock you out. Do not set it without a proxy actually in front. |

> **⚠️ Important**: Do not change `SECRET_KEY` after adding providers. All stored credentials are encrypted with this key.

> **Forgot your password?** If set via `.env`, edit the file. If set via the Setup wizard, use Settings → Change Password while logged in. Locked out entirely, clear **both** rows and restart:
>
> ```bash
> sqlite3 data/vauxtra.db "DELETE FROM settings WHERE key IN ('app_password_hash','auth_mode');"
> ```
>
> `auth_mode` is what tells a deliberately passwordless install apart from one whose hash went missing. Deleting the hash on its own leaves the second state, and Vauxtra then refuses every request rather than falling back to anonymous admin — which is the point of it, and why it has to go too.

---

## Documentation Map

- End-user quick start and feature overview: [README.md](README.md)
- End-user operations and API usage: [docs/HOWTO.md](docs/HOWTO.md)
- Production deployment checklist and recipes: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- Troubleshooting and known failure patterns: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Supported providers, evaluated candidates, writing your own: [docs/PROVIDERS.md](docs/PROVIDERS.md)
- Integration lab against real provider containers: [lab/README.md](lab/README.md)
- Security policy and reporting: [SECURITY.md](SECURITY.md)
- MCP server setup for compatible clients: [vauxtra_mcp/README.md](vauxtra_mcp/README.md)

---

## Provider Setup

### Nginx Proxy Manager

1. In NPM, go to **Users** and create a dedicated API user (or use admin credentials).
2. In Vauxtra, add a provider: type = `npm`, URL = `http://your-npm:81`.
3. Test connection.

### Traefik

Traefik is **read-only** in Vauxtra (it configures itself via Docker labels or config files).

1. Expose the Traefik dashboard API at e.g. `http://traefik:8080`.
2. In Vauxtra, add a provider: type = `traefik`, URL = `http://traefik:8080`.
3. Use **Sync → Import** to import existing routes.

### Zoraxy

Zoraxy has a single admin account and no API keys: Vauxtra logs in with the same
credentials you use in the browser. Keep the management port reachable from the LAN or
VPN only.

1. Note the management URL, e.g. `http://zoraxy:8000`, and the admin username/password
   (leave both empty for an instance started with `-noauth`).
2. In Vauxtra, add a provider: type = `zoraxy`, URL = `http://zoraxy:8000`.
3. Test connection, then **Sync → Import** to pick up existing host rules.

Vauxtra manages **host** rules only (no virtual directories, no TCP/UDP stream proxies) and
one upstream per rule; extra load-balanced upstreams are left untouched. Zoraxy terminates
TLS globally, so there is no per-host "force SSL" switch.

### Cloudflare DNS

1. Create a Cloudflare API token with **Zone → DNS → Edit** permission for your zones.
2. In Vauxtra, add a provider: type = `cloudflare`, API token = `<your-token>`.

### Cloudflare Tunnel

1. Create a tunnel in the Cloudflare dashboard and copy the Tunnel ID (UUID).
2. Create an API token with:
  - `Account -> Cloudflare Tunnel -> Edit`
  - `Zone -> DNS -> Edit`
3. Copy your Cloudflare Account ID.
4. In Vauxtra, add a provider: type = `cloudflare_tunnel`, with Account ID, API token, and Tunnel ID.
5. When creating a service, set expose mode to **Tunnel**.

### Pi-hole / AdGuard Home

1. Retrieve the API password from your Pi-hole or AdGuard Home admin panel.
2. In Vauxtra, add a provider of the appropriate type with URL and credentials.
3. For Pi-hole, use the base URL (e.g. `http://pihole` or `http://localhost:18081`), not `/admin`.

### PowerDNS Authoritative

1. Enable the API in `pdns.conf`: `api=yes`, `api-key=<key>`, `webserver=yes`, and a
   `webserver-allow-from` that includes Vauxtra. The API listens on `:8081` by default.
2. In Vauxtra, add a provider: type = `powerdns`, URL = `http://your-pdns:8081`,
   server id = `localhost` (the stock value), API key = `<key>`.
3. The record has to fall inside a zone the server hosts — create the zone in PowerDNS
   first. Vauxtra picks the longest zone that contains the name.

### deSEC

1. Create a free account at [desec.io](https://desec.io/) and generate a token in
   **Token management**. A token limited to your domain is enough.
2. In Vauxtra, add a provider: type = `desec`, token = `<token>`. Leave the URL empty
   unless you run your own deSEC instance.
3. The domain field is optional: leave it blank to let Vauxtra pick the matching domain
   out of your account, or set it to pin every record to one domain.

---

## MCP Integration

Vauxtra ships an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server package that exposes core operations as tools. Connect it to any MCP-compatible client.

### Setup

1. Create an API key in Vauxtra: **Settings → API Keys → New Key**.
2. Note the generated key (shown once at creation).

> The MCP server runs on the host (next to your MCP client), not inside the Vauxtra Docker image. Clone this repo and `pip install -r vauxtra_mcp/requirements.txt` on the machine that will launch the client. The server reaches Vauxtra over HTTP using `VAUXTRA_URL` + `VAUXTRA_API_KEY`.

### Example MCP client config

Add an equivalent server block to your MCP client configuration:

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

See [docs/HOWTO.md](docs/HOWTO.md#11-mcp-integration) for the full list. Summary:

**Services** — `list_services`, `get_service`, `create_service`, `update_service`, `delete_service`, `toggle_service`, `sync_services_from_providers`, `import_services_from_sync`

**Templates** — `list_templates`, `get_template`, `create_template`, `delete_template`, `apply_template`

**Operations** — `run_preflight`, `dry_run_push`, `push_service`, `check_drift`, `reconcile_service`

**Providers** — `list_providers`, `get_provider_types`, `create_provider`, `update_provider`, `delete_provider`, `test_provider`, `get_provider_health`, `get_all_providers_health`, `get_tunnel_health`

**Docker** — `list_docker_endpoints`, `discover_docker_containers`, `import_docker_containers`

**Monitoring** — `get_health`, `get_logs`, `get_stats`, `get_certificates`, `get_certificate_expiry`, `check_all_services`

---

## API Reference

When `DEBUG=true` is set, the full interactive API documentation is available at:

```
http://localhost:8888/api/docs
```

All endpoints accept `Authorization: Bearer <api_key>` in addition to session cookies.

---

## Development Setup

### Backend

```bash
# Requires Python 3.13+
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run with auto-reload
uvicorn app.main:app --reload --port 8888
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # Dev server on :5173 with API proxy to :8888
npm run build      # Production build to frontend/dist/
npm run lint       # ESLint
```

### Running both together

The frontend dev server (`npm run dev`) proxies `/api/*` to the FastAPI backend at `:8888`. Run both in separate terminals.

### Docker build

```bash
docker compose up --build
```

The Dockerfile uses a multi-stage build: Node 26 for the frontend, Python 3.14-slim for the final image.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Local dev setup
- Conventional Commits guide
- How to add a DNS or proxy provider
- How to add an MCP tool
- PR checklist

### Translating Vauxtra

The UI is fully internationalized. To add or improve a translation:

1. Duplicate [`frontend/src/locales/en.json`](frontend/src/locales/en.json)
2. Name it with the [ISO 639-1 code](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes) (e.g. `ko.json`)
3. Add the language to `SUPPORTED_LANGUAGES` in [`frontend/src/i18n/index.tsx`](frontend/src/i18n/index.tsx)
4. Open a PR — no backend change needed

---

## Upgrading

Vauxtra follows a **rolling release** model on the `latest` Docker tag:

```bash
docker compose pull
docker compose up -d
```

The database is automatically migrated on startup. No manual steps required.

---

## License

MIT — see [LICENSE](LICENSE).
