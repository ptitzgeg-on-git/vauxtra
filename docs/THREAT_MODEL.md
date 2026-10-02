# Threat model

Vauxtra holds working credentials for your DNS servers, reverse proxies and Cloudflare
account. Whoever controls Vauxtra controls what those credentials allow. This page says
what is stored, who the defences are aimed at, and where they stop, so you can decide
where to run it.

To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## What Vauxtra stores

Everything lives in the `data/` volume unless noted.

| What | Where | How it is protected |
|---|---|---|
| Provider passwords and API tokens | `providers.password` in `vauxtra.db` | Fernet (AES-128-CBC with HMAC-SHA256), key derived from `SECRET_KEY` |
| Notification (Apprise) URLs, which embed their tokens | `webhooks.url`, and `webhook_delivery_log.url` while a delivery is retried | Fernet, like provider secrets. Masked in every API response and log line |
| Admin password | `settings.app_password_hash`, or `APP_PASSWORD` | PBKDF2-HMAC-SHA256, 600,000 iterations |
| API keys | `api_keys.key_hash` | SHA-256 of a random 256-bit token, shown once at creation |
| `SECRET_KEY` | The `SECRET_KEY` variable, otherwise generated into `data/.secret_key` (mode 600) | Signs the session cookie and derives the encryption key |

Provider URLs, usernames, Cloudflare account and tunnel IDs, service hostnames and internal
addresses are stored in clear and returned to any authenticated caller. They describe your
network; they do not open it.

## Who the defences are aimed at

### Someone on the network who can reach the panel

- The default Compose file publishes port 8888 on `127.0.0.1` only.
- With a password set, every route requires a session or an API key, except sign-in,
  sign-out, `/api/health`, `/metrics` and `/api/auth/me`. `/api/health` returns the version
  and disk usage; `/metrics` returns counters per status and per provider type, not names.
- Sign-in is limited to 5 attempts a minute and 20 an hour per client address. Behind a
  reverse proxy, set `FORWARDED_ALLOW_IPS`, otherwise every visitor shares one counter and
  one attacker can lock you out.
- The session cookie is `HttpOnly` and `SameSite=Strict`, and `Secure` when `HTTPS_ONLY=true`.
  There is no CSRF token: cross-site requests are stopped by `SameSite=Strict` and by the
  empty CORS list. Do not add origins to `CORS_ORIGINS` that you do not control.
- A session lasts 7 days. Signing out clears the cookie in that browser only. Changing the
  admin password ends every session at once.

### A leaked API key

Keys do not expire. Give each tool its own key with the lowest scope it needs, and revoke it
in **Settings > API Keys** when the tool goes away.

| Scope | What a stolen key can do |
|---|---|
| `read` | List services, providers, tags and certificates: hostnames, internal addresses, provider URLs and usernames. No secrets, no logs. |
| `write` | Create, change and delete DNS records and proxy hosts through the stored credentials. Make the server connect to hosts it names (provider tests, preflight, Docker `tcp://` endpoints, webhook tests), which is enough to probe the network Vauxtra sits on. It cannot read a stored secret, move a provider's stored secret to another host, set generic HTTP webhooks or the WAN address resolvers, manage keys or read the logs. |
| `admin` | Everything, including an encrypted backup of every stored secret under a passphrase it chooses. |

### Someone with a copy of the data directory

When `SECRET_KEY` is not set, its generated value is written to `data/.secret_key`, next to
the database. A copy of `data/` is then a copy of every provider credential. If backups of
that directory leave the host, set `SECRET_KEY` in the environment instead, so the key is
not in the backup.

Changing `SECRET_KEY` makes the stored credentials unreadable. Re-enter them afterwards.
Vauxtra logs a warning at startup when `SECRET_KEY` is shorter than 32 characters.

### Other containers on the same host

The Docker socket is optional and only used to discover containers. Vauxtra only lists and
inspects them, but whoever controls Vauxtra controls the socket, and the Docker API is root
on the host. `:ro` on the mount does not change that. Drop the mount if you do not use
discovery, or point Vauxtra at a socket proxy that only allows listing containers.

## Open mode

The setup wizard lets you skip the password. Every request is then treated as admin, with no
credential, and a warning is logged at every start. While no password exists, the first
visitor can also set one. Use open mode only while the port is bound to `127.0.0.1`.

If an instance had a password and its hash disappears from the database (a partial restore,
a hand edit), Vauxtra refuses every request instead of falling back to open mode, including
the setup route. See [HOWTO](HOWTO.md) for the recovery steps.

## Outbound connections

Vauxtra connects to whatever its configuration names: provider URLs, Docker `tcp://` and
`ssh://` endpoints, notification URLs, the WAN address resolvers, and each service's target
for health checks. These are LAN addresses by nature, so private ranges are not filtered.
Choosing them takes a `write` key, and `admin` for the WAN resolvers and the generic HTTP
notification schemes (`json://`, `form://`, `xml://`).

A stored provider secret is only ever sent to the host, port and scheme it was entered for.
Moving a provider elsewhere takes the secret again, or an `admin` key.

## The MCP bridge

The bridge in `vauxtra_mcp/` runs next to your MCP client and holds an API key, so it can do
whatever that key's scope allows. Give it the lowest scope the agent needs.

- Over stdio, the default, nothing listens on the network.
- With `--http`, it binds to `127.0.0.1` and refuses a request whose `Host` or `Origin` is
  not that address, so a web page cannot reach it through DNS rebinding. It has no
  authentication of its own: binding it elsewhere needs an authenticating proxy in front.
- `auth_login` is refused when a key is configured, so a password session cannot widen a
  narrow key.
- `create_secure_backup` writes the encrypted backup to a file on the bridge's machine and
  returns only the path. The passphrase you give the agent is in the conversation; the
  backup is not.

## Credentials each provider needs

Give Vauxtra the narrowest credential the provider offers. Where the provider has no scoped
credential, treat Vauxtra as an administrator of that provider.

| Provider | Credential | What Vauxtra does with it | Narrowest option |
|---|---|---|---|
| Nginx Proxy Manager | User email and password | Creates, updates, enables, disables and deletes proxy hosts; reads certificates | A dedicated user with Proxy Hosts: Manage and Certificates: View, not the admin account |
| Zoraxy | Web console login | Manages host rules; reads certificates | Zoraxy has one admin account. Keep its console on your LAN or VPN |
| Traefik | None, or basic auth | Reads routers. Never writes | Expose the API on an internal entrypoint only |
| Cloudflare DNS | API token | Lists zones; creates, updates and deletes DNS records | Zone > DNS > Edit, limited to the zones Vauxtra manages |
| Cloudflare Tunnel | API token and account ID | Edits the tunnel's ingress rules and the matching DNS records | Account > Cloudflare Tunnel > Edit and Zone > DNS > Edit, limited to those zones |
| Pi-hole | API token (v5) or app password (v6) | Manages local DNS records | Pi-hole has no scoped credential. Use the API token or an app password rather than the web password |
| AdGuard Home | Web admin login | Manages DNS rewrites | AdGuard Home has no scoped credential |
| Technitium DNS | Web console login | Manages records in existing zones | A dedicated user, limited to those zones if your version supports per-zone permissions |
| PowerDNS | API key | Manages records in existing zones | The key covers every zone on the server. Narrow `webserver-allow-from` to Vauxtra's address |
| deSEC | API token | Manages records in the account's domains | A token without "Can manage tokens" |

## Verify the image

Every published image is signed with Sigstore keyless signing and carries a SLSA provenance
attestation, both produced by `.github/workflows/docker-publish.yml` after the tests and the
vulnerability scans pass. Verification needs [cosign](https://github.com/sigstore/cosign)
3.0 or later; cosign 2 does not find these signatures.

A release:

```bash
cosign verify ghcr.io/ptitzgeg-on-git/vauxtra:1.7.0 \
  --certificate-identity 'https://github.com/ptitzgeg-on-git/vauxtra/.github/workflows/docker-publish.yml@refs/tags/v1.7.0' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

`latest`, built from `main` or from a release tag:

```bash
cosign verify ghcr.io/ptitzgeg-on-git/vauxtra:latest \
  --certificate-identity-regexp '^https://github\.com/ptitzgeg-on-git/vauxtra/\.github/workflows/docker-publish\.yml@refs/(heads/main|tags/v[0-9.]+)$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The provenance attestation, same identity:

```bash
cosign verify-attestation ghcr.io/ptitzgeg-on-git/vauxtra:1.7.0 --type slsaprovenance1 \
  --certificate-identity 'https://github.com/ptitzgeg-on-git/vauxtra/.github/workflows/docker-publish.yml@refs/tags/v1.7.0' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

A signature proves the image was built by this repository's workflow from that tag. It does
not prove the code is free of bugs.

## Known limits

- Sessions cannot be revoked one by one. Changing the password revokes all of them.
- API keys have no expiry date.
- `/metrics` answers without a credential. It shows counts only, including the number of
  warnings logged in the last 24 hours, refused sign-ins among them. Restrict it at your
  reverse proxy if that matters to you.
- A `write` key can make the server open connections to any host it names.
- The drift, dry-run, scan and certificate routes are not rate-limited, and each one asks
  the providers. A `read` key used in a tight loop can keep them busy.
- Provider plugins loaded through `VAUXTRA_PROVIDER_PLUGINS` run with the application's
  privileges. Load only code you trust.
