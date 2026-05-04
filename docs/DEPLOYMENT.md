# Vauxtra Deployment Guide

This guide is for production-style deployments of Vauxtra.

## 1. Deployment Modes

- Docker image (recommended): `ghcr.io/ptitzgeg-on-git/vauxtra:latest`
- Docker Compose from repo: `docker compose up -d --build`
- Source run (dev/staging only): backend + frontend dev server

## 2. Minimum Requirements

- Docker 24+ and Compose plugin
- Persistent storage for `/app/data`
- Network access from Vauxtra to your providers (NPM, DNS, Cloudflare, Docker endpoints)

## 3. Quick Production Compose

```yaml
services:
  vauxtra:
    image: ghcr.io/ptitzgeg-on-git/vauxtra:latest
    container_name: vauxtra
    ports:
      - "8888:8888"
    environment:
      TZ: Europe/Paris
      HTTPS_ONLY: "false"
      DEBUG: "false"
      # Optional but recommended in production:
      # SECRET_KEY: "set-a-long-random-value-and-keep-it-stable"
      # APP_PASSWORD: "set-a-strong-password"
      # CORS_ORIGINS: "https://vauxtra.example.com"
    volumes:
      - ./data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped
```

Start:

```bash
docker compose up -d
```

Open: `http://<host>:8888`

## 4. Critical Security Rules

1. Keep `SECRET_KEY` stable for the lifetime of the instance.
2. Never rotate `SECRET_KEY` casually: provider credentials are encrypted with it.
3. Use `APP_PASSWORD` or complete password setup in wizard before exposing publicly.
4. Set `DEBUG=false` in production to disable `/api/docs`.
5. Restrict inbound access with reverse proxy/firewall if Internet-exposed.

## 5. Reverse Proxy (Recommended)

Deploy behind your existing reverse proxy (NPM/Traefik/Caddy/Nginx) with TLS termination.

Recommended upstream:

- Upstream target: `http://vauxtra:8888`
- Preserve `Host` header
- Configure HTTPS certificate at proxy layer

When TLS is terminated at proxy, keep `HTTPS_ONLY=false` in Vauxtra unless you serve HTTPS directly to the app container.

## 6. Update Procedure

For GHCR image deployments:

```bash
docker compose pull
docker compose up -d
```

For source-build deployments:

```bash
git pull
docker compose up -d --build
```

Post-update checks:

1. Open dashboard and confirm health cards load.
2. Validate at least one provider test from Integrations.
3. Run one endpoint drift check and verify success.

## 6.1 Upgrade Notes (Existing Users)

1. Users already running older images are not auto-upgraded.
2. Apply updates explicitly:

```bash
docker compose pull
docker compose up -d
```

3. If using pinned tags or digests, bump them manually.
4. In-app "How-To & API" tab was removed; use `docs/HOWTO.md` as source of truth.
5. If provider URLs use `localhost` and Vauxtra runs in Docker, review localhost rewrite behavior:
  - `VAUXTRA_REWRITE_LOCALHOST` (default `true`)
  - `VAUXTRA_LOCALHOST_ALIAS` (default `host.docker.internal`)

## 7. Backup and Recovery

Use Settings -> Backup & Restore for logical backups.

Also snapshot the `data/` directory periodically:

- `data/vauxtra.db`
- `data/.secret_key`

Recovery rule:

- Restoring DB without matching `.secret_key` will break encrypted provider credentials.

## 8. Environment Variables

| Variable | Default | Production note |
|---|---|---|
| `SECRET_KEY` | auto-generated | Set explicitly for predictable recovery and keep stable |
| `APP_PASSWORD` | empty | Set for non-wizard bootstrap or leave empty for setup flow |
| `TZ` | `Europe/Paris` | Set to your timezone |
| `HTTPS_ONLY` | `false` | Use `true` only when app itself is served over HTTPS |
| `DEBUG` | `false` | Keep `false` in production |
| `CORS_ORIGINS` | local defaults | Restrict to actual frontend origins |

## 9. Deployment Readiness Checklist

- [ ] `DEBUG=false`
- [ ] Stable `SECRET_KEY` configured and backed up
- [ ] `APP_PASSWORD` set or setup wizard completed securely
- [ ] `/app/data` persisted on durable storage
- [ ] Access restricted by firewall or reverse proxy auth/TLS
- [ ] Backup and restore test completed once
- [ ] Provider connectivity validated from Integrations page

## 10. Related Docs

- User operations: [docs/HOWTO.md](docs/HOWTO.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Security policy: [SECURITY.md](SECURITY.md)
