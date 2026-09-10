# Integration lab

Five real provider containers and a harness that drives a real Vauxtra against them over
its own HTTP API. Not a unit test with a mocked provider: the actual images an operator
runs, answering on their actual APIs.

The unit suite proves Vauxtra sends the right request. This proves the provider accepts
it — and, more usefully, catches the things a mock cannot have an opinion about: an API
that caps concurrent sessions, a proxy that refuses a second host for a domain it already
serves, a DNS server that will not write a record outside a zone it hosts.

## Run it

```sh
cd lab && ./up.sh          # seed, start, wait, bootstrap
cd .. && python lab/harness.py
```

`up.sh --fresh` destroys every container and volume first. Nothing in here is meant to
survive; that is the point.

Requirements: Docker, and the repository's own Python dependencies (`requirements.txt`).
The harness installs nothing.

## What runs

| Container | Image | Port (127.0.0.1) | Role |
|---|---|---|---|
| `vxlab-adguard` | `adguard/adguardhome:v0.107.68` | 3080 | DNS |
| `vxlab-npm` | `jc21/nginx-proxy-manager:2.13.1` | 3081 | Proxy |
| `vxlab-pihole` | `pihole/pihole:2025.08.0` | 3082 | DNS |
| `vxlab-zoraxy` | `zoraxydocker/zoraxy:v3.3.4` | 3083 | Proxy |
| `vxlab-technitium` | `technitium/dns-server:13.6.0` | 5380 | DNS |
| `vxlab-upstream` | `traefik/whoami:v1.11` | 3090 | Something to point at |

Every port is bound to loopback. Each of these ships an admin panel, and Pi-hole and
Technitium also answer DNS: none of it belongs on the LAN. The password is
`vauxtra-lab-pw` throughout — a fixture, not a secret.

Cloudflare and Cloudflare Tunnel are absent because they cannot be faked: testing them
means pointing at a real account and a real zone.

## What the harness checks

Seventy-four probes across six phases, for every provider and for three proxy+DNS pairs
(`npm+adguard`, `zoraxy+technitium`, `npm+pihole`):

0. **Clean slate** — remove anything a previous run left behind. A leftover is not
   harmless: NPM refuses a second host for a domain it already serves, so one aborted run
   makes the next one fail on `create` and blame Vauxtra.
1. **Register** each container as a provider through `POST /api/providers`.
2. **Test the connection** through the same endpoint the UI's button calls.
3. **Direct record CRUD** — create, read back off the live provider, delete, confirm gone.
4. **Publish a service** — then read the route off the proxy and the rewrite off the DNS
   server to confirm both halves actually landed.
5. **Drift** — break the provider *behind Vauxtra's back*, which is how it happens in the
   field: delete the rewrite, force the origin port to 9999. Check that the drift endpoint
   names `missing_dns_rewrite` and `proxy_origin_mismatch`, that reconcile repairs it, and
   that drift comes back clean.
6. **Delete** the service and confirm the route and the rewrite are gone from the live
   providers, not just from Vauxtra's database.

Drift is never created by editing Vauxtra's own database. That version of the test passes
whether or not the code works.

## Seeding

Three of the five cannot be seeded from `compose.yaml` alone, and each refuses in its own
way. `bootstrap.sh` handles them and documents why:

- **Zoraxy** starts with no account at all. `/api/auth/login` answers 403 "CSRF token not
  found" until the token is read out of `/login.html`; only then does `/api/auth/register`
  accept the first user.
- **Nginx Proxy Manager 2.13** no longer ships `admin@example.com` / `changeme`. While
  `GET /api/` reports `"setup": false`, `POST /api/users` is open and creates the first
  administrator.
- **Technitium** accepts a record only inside a zone it hosts, and Vauxtra derives the
  zone from the last two labels — so `vxlab.test` has to exist first.

AdGuard's four-step wizard has no API at all, so `up.sh` copies a pre-hashed config into
place before the container starts. Pi-hole takes its password from the environment.

## `repro_pihole_seats.py`

A standing witness for the bug this lab was built to find. Pi-hole v6 caps concurrent API
sessions at `webserver.api.max_sessions` (16) and holds each for
`webserver.session.timeout` (1800s). Vauxtra builds a fresh provider per request, so an
operation that logs in without logging out burns a seat for half an hour — and
`list_rewrites` is what the drift check calls on every pass.

```sh
docker compose rm -sf pihole && docker compose up -d pihole
python lab/repro_pihole_seats.py
```

It reports two things per call, because they fail independently: whether Pi-hole granted
a session, and whether the seat came back. Before the fix the sixteenth call was refused;
now twenty calls run with nothing left open.
