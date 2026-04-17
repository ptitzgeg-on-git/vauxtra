# Vauxtra

> **The missing link in your network stack.**  
> Self-hosted DNS & reverse proxy management panel for homelab.  
> Orchestrates Nginx Proxy Manager, Traefik, Cloudflare, Pi-hole, AdGuard Home, and more — from one unified interface.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61dafb)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135-green)](https://fastapi.tiangolo.com/)
[![MCP Ready](https://img.shields.io/badge/MCP-Ready-purple)](https://modelcontextprotocol.io/)

---

## Features

- **Multi-provider routing** — manage proxy hosts (NPM, Traefik) and DNS rewrites (Cloudflare, Pi-hole, AdGuard) from a single service record
- **Cloudflare Tunnel** — expose services without port-forwarding via Cloudflare Tunnel integration
- **Docker discovery** — auto-detect running containers with Traefik label parsing and confidence scoring
- **Preflight & dry-run** — validate routing config before pushing; preview changes without committing
- **Drift detection & reconcile** — detect when live provider state diverges from expected and fix it automatically
- **Auto-reconcile scheduler** — periodic background reconciliation with webhook notifications
- **DNS propagation checker** — verify A-record propagation across Google, Cloudflare, and Quad9 resolvers after a push
- **Certificate expiry monitoring** — track NPM certificates, flag those expiring within 30 days
- **Service templates** — quick-create for common patterns (HTTP webapp, HTTPS, WebSocket, Cloudflare Tunnel, etc.)
- **API Keys** — bearer token authentication for CI/CD pipelines and MCP server access
- **MCP Server** — expose all operations as tools for AI assistants (Claude Desktop, Cursor)
- **Webhook alerts** — Apprise-compatible webhook for service down/recovery and reconcile events
- **Environments & Tags** — organise services with colour-coded labels

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
│   │  NPM  Traefik  Cloudflare DNS │                     │
│   │  Pi-hole  AdGuard  CF Tunnel  │                     │
│   └───────────────────────────────┘                     │
└─────────────────────────────────────────────────────────┘
          │
   MCP Server (FastMCP) — AI assistant integration
```

Vauxtra is an **orchestrator**: it does not run a reverse proxy or DNS server itself — it configures the ones you already have running.

---

## Quick Start (Docker)

Pull the pre-built image from GitHub Container Registry:

```bash
docker run -d \
  --name vauxtra \
  -p 8888:8888 \
  -v vauxtra_data:/app/data \
  ghcr.io/ptitzgeg-on-git/vauxtra:latest
```

Or use Docker Compose:

```yaml
# docker-compose.yml
services:
  vauxtra:
    image: ghcr.io/ptitzgeg-on-git/vauxtra:latest
    ports:
      - "8888:8888"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

```bash
docker compose up -d
open http://localhost:8888
```

The `data/` directory stores the SQLite database and the auto-generated secret key.

> **Build from source?** Clone the repo and run `docker compose up --build -d`.

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in your values.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | **Do not change after setup.** Used to sign session cookies and encrypt provider credentials. Auto-generated to `data/.secret_key` if left empty. |
| `APP_PASSWORD` | *(none)* | Password to protect the web interface. Leave empty to configure via Setup wizard. |
| `TZ` | `Europe/Paris` | Timezone for scheduler and log timestamps. |
| `HTTPS_ONLY` | `false` | Set to `true` when serving directly over HTTPS (not behind a reverse proxy). |
| `DEBUG` | `false` | Enable `/api/docs` (Swagger UI) and verbose logging. |

| `VAUXTRA_URL` | `http://localhost:8888` | Base URL of this instance (used by the MCP server). |
| `VAUXTRA_API_KEY` | *(none)* | API key for MCP server auth. Create one in **Settings → API Keys**. |
| `DOCKER_HOST` | *(env default)* | Docker socket path. Override if using a non-standard location. |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated list of allowed CORS origins. Override when serving behind a custom domain. |

> **⚠️ Important**: Do not change `SECRET_KEY` after adding providers. All stored credentials are encrypted with this key.

> **Forgot your password?** If set via `.env`, edit the file. If set via Setup wizard, use Settings → Change Password while logged in, or delete the hash from the database: `sqlite3 data/vauxtra.db "DELETE FROM settings WHERE key='app_password_hash';"` and restart.

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

### Cloudflare DNS

1. Create a Cloudflare API token with **Zone → DNS → Edit** permission for your zones.
2. In Vauxtra, add a provider: type = `cloudflare`, API token = `<your-token>`.

### Cloudflare Tunnel

1. Create a tunnel in the Cloudflare dashboard and copy the tunnel token.
2. In Vauxtra, add a provider: type = `cloudflare_tunnel`, token = `<tunnel-token>`.
3. When creating a service, set expose mode to **Tunnel**.

### Pi-hole / AdGuard Home

1. Retrieve the API password from your Pi-hole or AdGuard Home admin panel.
2. In Vauxtra, add a provider of the appropriate type with URL and credentials.

---

## MCP Integration

Vauxtra ships an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes all operations as AI tools. Connect it to Claude Desktop, Cursor, or any MCP-compatible client.

### Setup

1. Create an API key in Vauxtra: **Settings → API Keys → New Key**.
2. Note the generated key (shown once at creation).

> The MCP server runs on the host (next to Claude Desktop / Cursor), not inside the Vauxtra Docker image. Clone this repo and `pip install -r vauxtra_mcp/requirements.txt` on the machine that will launch the MCP client. The server reaches Vauxtra over HTTP using `VAUXTRA_URL` + `VAUXTRA_API_KEY`.

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

See [docs/HOWTO.md](docs/HOWTO.md#10-mcp-integration) for the full list. Summary:

**Services** — `list_services`, `get_service`, `create_service`, `update_service`, `delete_service`, `toggle_service`, `sync_services_from_providers`, `import_services_from_sync`, `get_service_topology`

**Operations** — `run_preflight`, `dry_run_push`, `push_service`, `check_drift`, `reconcile_service`, `check_dns_propagation`

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

The Dockerfile uses a multi-stage build: Node 22 for the frontend, Python 3.13-slim for the final image.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Local dev setup
- Conventional Commits guide
- How to add a DNS or proxy provider
- How to add an MCP tool
- PR checklist

---

## License

MIT — see [LICENSE](LICENSE).
