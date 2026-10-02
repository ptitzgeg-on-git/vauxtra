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

## 5. Behind a reverse proxy

Deploy behind your existing reverse proxy with TLS termination. The examples below put the
proxy and Vauxtra on one Docker network, so the proxy reaches `http://vauxtra:8888` by name
and the port does not need to be published at all. Create the network once:

```bash
docker network create --subnet 10.0.0.0/24 proxy
```

Pick any free subnet; a fixed one lets you name it in `FORWARDED_ALLOW_IPS`. Then, whatever
the proxy:

- Set `HTTPS_ONLY=true`. It is about the URL the browser used, not about the hop between proxy
  and container: it marks the session cookie `Secure` and sends HSTS. Leave it `false` only
  while reaching the instance over plain `http://`.
- Set `FORWARDED_ALLOW_IPS` to the proxy's address, or to the network's subnet
  (`10.0.0.0/24` above). Without it Vauxtra reads the socket peer as the client address, which
  behind a proxy is one address for every visitor: the login limit of 5/minute is shared by
  everyone and one attacker locks the operator out. Do not set it without a proxy in front: it
  lets any caller choose the address every rate limit and log line is charged to.

Vauxtra's side of the compose file is the same for all three:

```yaml
services:
  vauxtra:
    image: ghcr.io/ptitzgeg-on-git/vauxtra:1.7
    # no `ports:`: only the proxy reaches it, over the shared network
    networks: [proxy]
    volumes:
      - ./data:/app/data
    environment:
      HTTPS_ONLY: "true"
      FORWARDED_ALLOW_IPS: "10.0.0.0/24"
    restart: unless-stopped

networks:
  proxy:
    external: true
```

### Nginx Proxy Manager

Attach the NPM container to the `proxy` network too, then in NPM:

1. **Hosts > Proxy Hosts > Add Proxy Host**.
2. **Details**: domain `vauxtra.example.com`, scheme `http`, forward hostname `vauxtra`,
   forward port `8888`. Turn on **Block Common Exploits**.
3. **SSL**: request or pick a certificate, turn on **Force SSL** and **HTTP/2 Support**.
4. Save.

NPM sends `X-Forwarded-For` by default; nothing to add in the **Advanced** tab.

### Traefik

Add these labels to the `vauxtra` service above, with Traefik on the same network and an
entrypoint called `websecure` and a certificate resolver called `letsencrypt` (adjust to your
names):

```yaml
    labels:
      - traefik.enable=true
      - traefik.docker.network=proxy
      - traefik.http.routers.vauxtra.rule=Host(`vauxtra.example.com`)
      - traefik.http.routers.vauxtra.entrypoints=websecure
      - traefik.http.routers.vauxtra.tls.certresolver=letsencrypt
      - traefik.http.services.vauxtra.loadbalancer.server.port=8888
```

### Caddy

With Caddy on the same network:

```caddyfile
vauxtra.example.com {
    reverse_proxy vauxtra:8888
}
```

Caddy gets the certificate and sends `X-Forwarded-For` on its own.

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
4. If provider URLs use `localhost` and Vauxtra runs in Docker, review localhost rewrite behavior:
  - `VAUXTRA_REWRITE_LOCALHOST` (default `true`)
  - `VAUXTRA_LOCALHOST_ALIAS` (default `host.docker.internal`)

### Pinning a version

Images are tagged per release: `1.7.0`, `1.7`, `1`, plus `latest` (follows `main`, which only
receives releases) and `dev` (the integration branch, not for production). Pin a minor line
to get fixes without new features:

```yaml
    image: ghcr.io/ptitzgeg-on-git/vauxtra:1.7
```

**Downgrades are not supported.** The schema only moves forward: an older image does not
know what a newer one changed in `vauxtra.db`. To go back, stop the container, restore the
`data/` snapshot taken before the upgrade, and start the older tag.

## 7. Backup and Recovery

Two kinds of backup, and you want both:

- **Logical backup**: **Settings > Data > Export**. *Export (no credentials)* leaves provider
  passwords and notification URLs out. *Export with credentials* encrypts them with a
  passphrase you choose. Restore either with **Settings > Data > Import**.
- **Snapshot of `data/`**: `vauxtra.db` and `.secret_key` (unless `SECRET_KEY` is set in the
  environment). Keep the two together: a database restored without its key cannot decrypt
  the credentials it holds.

The database runs in WAL mode, so a plain copy of `vauxtra.db` while the container runs can
miss recent writes. Either stop the container first:

```bash
docker compose stop vauxtra
tar czf vauxtra-data-$(date +%F).tgz data/
docker compose start vauxtra
```

or copy `vauxtra.db-wal` and `vauxtra.db-shm` with it, or take a consistent copy from the live
database with SQLite's backup API (the image has Python, not the `sqlite3` tool):

```bash
docker compose exec vauxtra python -c \
  "import sqlite3; sqlite3.connect('data/vauxtra.db').backup(sqlite3.connect('data/vauxtra-backup.db'))"
```

### Restoring a data/ snapshot

1. Stop the container: `docker compose stop vauxtra`.
2. Move the current `data/` aside, then put the snapshot in its place. Remove any
   `vauxtra.db-wal` and `vauxtra.db-shm` that did not come from the same snapshot.
3. Check the key matches **before starting**: the snapshot's `.secret_key`, or the
   `SECRET_KEY` that was set when the snapshot was taken. With the wrong key every test
   fails with `Cannot decrypt a stored secret`; stop, put the right key back, and start again.
   Versions up to 1.7.0 also re-encrypted the passwords on such a start, which the right key
   could not undo: on those, only the snapshot or re-entering the passwords recovers them.
4. Start the container and run **Test** on one integration.

Restore a snapshot only into the same or a newer version of Vauxtra (see
[Pinning a version](#pinning-a-version)).

### Rotating SECRET_KEY

The key signs session cookies and encrypts provider credentials and notification URLs. To
change it without re-entering every credential, go through a secure backup, which carries
the credentials encrypted with a passphrase rather than with the key:

1. **Settings > Data > Export > Export with credentials**, with a passphrase. Keep the file
   and the passphrase.
2. Stop the container and take a `data/` snapshot as a fallback.
3. Set the new key in `.env`, at least 32 characters:
   `SECRET_KEY=` followed by the output of `openssl rand -hex 32`. A `SECRET_KEY` in the
   environment takes precedence over `data/.secret_key`; delete that file so only one key
   exists.
4. Start the container and sign in again. Every session ended, because the cookies were
   signed with the old key. API keys and the admin password do not depend on the key and
   still work. The provider passwords in the database are unusable at this point, which is
   expected: the next step replaces them.
5. **Settings > Data > Import** the file from step 1, with its passphrase. The restore
   re-encrypts the credentials with the new key. It replaces the data with the file's
   content, so do not change anything between steps 1 and 5.
6. Run **Test** on each integration.

To keep the key the instance generated but manage it in the environment instead (for a
secrets manager, say), no rotation is needed: copy the content of `data/.secret_key` into
`SECRET_KEY`, restart, then delete the file. The value does not change, so nothing has to be
re-encrypted.

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
