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
      # The loopback, so the proxy of section 5 is the only way in. VAUXTRA_BIND=0.0.0.0
      # publishes on every interface of the host instead.
      - "${VAUXTRA_BIND:-127.0.0.1}:8888:8888"
    environment:
      TZ: UTC
      HTTPS_ONLY: "true"          # the browser reaches you over https://, proxy or not
      DEBUG: "false"
      # Set this to your reverse proxy's address, or every visitor shares one rate limit:
      # FORWARDED_ALLOW_IPS: "172.18.0.2"
      # Optional but recommended in production:
      # SECRET_KEY: "set-a-long-random-value-and-keep-it-stable"
      # APP_PASSWORD: "pbkdf2:sha256:600000$...$..."   # a hash, not the password itself
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

Open: `http://127.0.0.1:8888`, from the host itself. The port is published on the
loopback only; section 5 puts a reverse proxy in front of it to reach it from anywhere
else, and `VAUXTRA_BIND=0.0.0.0` publishes it on every interface if you would rather not.

## 4. Critical Security Rules

1. Keep `SECRET_KEY` stable for the lifetime of the instance.
2. Never rotate `SECRET_KEY` casually: provider credentials are encrypted with it.
3. Use `APP_PASSWORD` or complete password setup in wizard before exposing publicly.
   `APP_PASSWORD` must hold a PBKDF2 hash — generate it with
   `python -c "from app.auth import hash_password; print(hash_password('...'))"`.
   A plaintext value is refused unless `ALLOW_PLAINTEXT_APP_PASSWORD=true`.
4. Set `DEBUG=false` in production to disable `/api/docs`.
5. Restrict inbound access with a reverse proxy or a firewall. The compose file
   publishes the port on the loopback (`VAUXTRA_BIND`, default `127.0.0.1`) so that this
   rule is the default rather than a step to remember; widen it only once something in
   front of it authenticates.
6. Treat the Docker socket mount as root on the host, because it is. The `:ro` in
   `/var/run/docker.sock:/var/run/docker.sock:ro` applies to the socket *file*; the Docker
   API behind it is unchanged, and it can create a container that bind-mounts `/`. Vauxtra
   only lists and inspects containers, but the grant is not bounded by what Vauxtra does
   with it — it is bounded by what anyone who reaches this container can do with it.
   Two ways to spend less: drop the mount entirely if you do not use the Docker features
   (the Docker screens then answer 502 and nothing else changes), or run a read-only socket
   proxy — for example `tecnativa/docker-socket-proxy` with `CONTAINERS=1` and everything
   else left off — and set the endpoint's Docker host to `tcp://docker-proxy:2375`.

## 5. Reverse Proxy (Recommended)

Deploy behind your existing reverse proxy (NPM/Traefik/Caddy/Nginx) with TLS termination.

Recommended upstream:

- Upstream target: `http://vauxtra:8888`
- Preserve `Host` header
- Configure HTTPS certificate at proxy layer

When TLS is terminated at the proxy, set `HTTPS_ONLY=true` anyway: it is about the URL the browser used, not about the hop between proxy and container. It marks the session cookie `Secure` and sends HSTS, and both are exactly what a proxied HTTPS deployment wants. Leave it `false` only while reaching the instance over plain `http://`.

Set `FORWARDED_ALLOW_IPS` to the proxy's address at the same time. Without it Vauxtra reads the socket peer as the client address — behind a proxy, one address for every visitor — so the login limit of 5/minute is shared by the whole internet and one attacker locks the operator out. It is opt-in on purpose: honouring `X-Forwarded-For` from anyone lets any caller choose the address every rate limit and log line is charged to.

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
| `APP_PASSWORD` | empty | PBKDF2 hash for non-wizard bootstrap, or leave empty for the setup flow |
| _(no password at all)_ | — | Every request gets the admin scope. Logged as a warning at each boot, and shown as a banner in the interface. |
| `ALLOW_PLAINTEXT_APP_PASSWORD` | `false` | Accept a plaintext `APP_PASSWORD`. Leave off outside a lab. |
| `TZ` | `UTC` | Set to your timezone |
| `HTTPS_ONLY` | `false` | `true` whenever the browser reaches the interface over `https://`, including behind a TLS-terminating proxy |
| `DEBUG` | `false` | Keep `false` in production — it also exposes `/api/docs` and `/openapi.json` |
| `CORS_ORIGINS` | *(empty)* | Empty is correct for a normal install; the interface is same-origin. List an origin only for a frontend hosted elsewhere |
| `FORWARDED_ALLOW_IPS` | *(empty)* | Your reverse proxy's address. Required for per-visitor rate limiting behind a proxy |

## 9. Deployment Readiness Checklist

- [ ] `DEBUG=false`
- [ ] `HTTPS_ONLY=true` and `FORWARDED_ALLOW_IPS` set to the proxy address
- [ ] Stable `SECRET_KEY` configured and backed up
- [ ] `APP_PASSWORD` set to a PBKDF2 hash, or setup wizard completed securely
- [ ] `/app/data` persisted on durable storage
- [ ] Access restricted by firewall or reverse proxy auth/TLS
- [ ] Backup and restore test completed once
- [ ] Provider connectivity validated from Integrations page

## 10. Related Docs

- User operations: [docs/HOWTO.md](HOWTO.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Security policy: [SECURITY.md](../SECURITY.md)
