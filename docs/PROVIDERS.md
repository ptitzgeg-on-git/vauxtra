# Providers — what is supported, and what is deliberately not

Vauxtra drives ten provider types. This document says what each one can actually do,
where the coverage is thin, which candidates were evaluated and rejected, and how to add
one without forking the project.

## What ships today

| Type | Software | Role | Writes? | Proven in `lab/` |
|---|---|---|---|---|
| `npm` | Nginx Proxy Manager | reverse proxy | yes | yes |
| `zoraxy` | Zoraxy | reverse proxy | yes | yes |
| `traefik` | Traefik | reverse proxy | **no — read only** | no |
| `cloudflare_tunnel` | Cloudflare Tunnel | reverse proxy (tunnel) | yes | **no** |
| `adguard` | AdGuard Home | DNS rewrites | yes | yes |
| `pihole` | Pi-hole | DNS rewrites | yes | yes |
| `technitium` | Technitium DNS | authoritative zone records | yes | yes |
| `powerdns` | PowerDNS Authoritative | authoritative zone records | yes | yes |
| `cloudflare` | Cloudflare | public DNS | yes | **no** |
| `desec` | deSEC | public DNS | yes | **no** |

Traefik has no write endpoints — its configuration comes from files, labels or a provider,
never from its API. `TraefikProvider.create_host` and `delete_host` raise `RuntimeError` on
purpose. Traefik is therefore an *import and monitor* provider: it can populate Vauxtra
from what already exists and report drift, but it cannot be driven. That is a property of
Traefik, not a gap in Vauxtra.

## Where the coverage is still thin

Three capabilities used to have exactly one implementation each, all three of them
Cloudflare. deSEC closes two:

| Capability | Implemented by | What it gates |
|---|---|---|
| `public_dns` | `cloudflare`, `desec` | offers the external-DNS branch of the expose form |
| `supports_auto_public_target` | `cloudflare`, `desec` | offers the "resolve my public IP" radio |
| `supports_tunnel` | `cloudflare_tunnel` | offers tunnel mode at all |

An operator who does not want a Cloudflare account now has a public path: a deSEC account
is free, takes no card, and lights up the same two branches of the interface. Tunnel mode
is still Cloudflare alone — the nearest alternative, Pangolin, is a peer rather than a
backend, for the reasons below.

Two properties of the design made that cheap, and still do.

**The pickers are already capability-driven.** `frontend/src/lib/providers.ts` is the one
place that answers "does this type do X?", and `GET /api/providers/types` always wins over
its fallback table. Nothing in the UI is hardcoded to Cloudflare. A new type that declares
`public_dns: true` is offered by every picker the moment it is registered.

**The auto-public-target machinery never touches the DNS provider.**
`app/public_target.py` resolves the target from external IP-echo services
(`api.ipify.org`, `ifconfig.me`, `icanhazip.com`, all configurable) or from the proxy
provider's own URL host, in a priority order the operator sets. `resolve_public_target()`
takes the proxy provider id and nothing else. So `supports_auto_public_target` is a UI gate
in front of logic that is already generic — it is a flag, not a feature.

### Why the two authoritative servers ship as local DNS

In the type table `public_dns` is a property of the *software*. For an authoritative server
it is really a property of the *deployment*. Technitium and PowerDNS on a LAN are local
DNS; the same servers with their zone delegated at a registrar are public DNS — and nothing
in either API says which one you are looking at.

Declaring them public would relabel the address field "public WAN IP" for every operator
running one on a LAN, which is the far more common case. So both ship as local, and the
honest fix — an opt-in `public_dns` override on the provider *row*, defaulting to the type
value and offered only for authoritative types — stays unbuilt on purpose. It would be the
first setting in Vauxtra where the operator asserts something about their own network that
the software cannot verify, so it needs a warning and a preflight that says what will be
written where, not a checkbox.

AdGuard and Pi-hole are the opposite case and should stay as they are: they are resolvers
that answer with a rewrite, not authoritative servers, so they can never be public whatever
the operator does.

## Candidates evaluated

| Candidate | Would fill | API | Verdict |
|---|---|---|---|
| deSEC | public DNS | REST, `Authorization: Token`, RRset per (subname, type) | **shipped** |
| PowerDNS Authoritative | local + public DNS | `PATCH /api/v1/servers/…/zones/{zone}`, `X-API-Key` | **shipped** |
| Caddy | reverse proxy | admin API on `:2019` | hold — see below |
| Pangolin | tunnel proxy | Integration API, Bearer key | no — it is a peer, not a backend |
| HAProxy | reverse proxy | Data Plane API (separate binary) | no unless asked |
| BIND9 | DNS | none — RFC 2136 + TSIG | no |
| Unbound, dnsmasq, plain nginx, Apache | either | none | impossible |
| Registrar APIs (OVH, Gandi, Porkbun, Hetzner, Namecheap, Route 53…) | public DNS | one auth scheme each | plugin territory |

**deSEC** was the strongest public-DNS candidate and is now the second `public_dns`
implementation: free, nonprofit, DNSSEC by default, and a genuinely clean REST model — one
RRset per `(subname, type)` under `https://desec.io/api/v1/domains/{name}/rrsets/`, token
in an `Authorization` header. It is the provider that actually removes the Cloudflare
dependency for the people who have it. Its rate limiting is aggressive, which forced the
design rule both new providers share: a read that was *refused* is not a read that came
back *empty*, and neither `add_rewrite` nor `delete_rewrite` will write over a record set
it could not read first.

**PowerDNS** is the only candidate that is both a real DNS server and testable in the lab,
and it now runs there — `vxlab-powerdns` on 3084, through the same publish / drift /
reconcile / delete lifecycle as the rest. Records go through a single `PATCH` on the zone
carrying an `rrsets` array with a `changetype` of `REPLACE` or `DELETE`, authenticated by
`X-API-Key`. `REPLACE` drops every existing value for the `(name, type)` pair before
writing, so every write is a read-modify-write; a blind PATCH would silently delete the
sibling A record of a name that has two addresses.

**Caddy** is technically drivable — `POST /load`, and `GET`/`POST`/`PATCH`/`DELETE` on
`/config/[path]` with `@id` shortcuts — but there is a footgun that would land squarely on
Vauxtra. `caddy run --config /etc/caddy/Caddyfile`, which is what the official Docker image
runs, **discards admin-API changes on restart**; only `caddy run --resume` keeps them. An
operator who manages a Caddyfile would watch Vauxtra's routes vanish at the next
`docker restart` and reasonably conclude Vauxtra had lost them. On top of that the admin
API binds to the container's own loopback unless the Caddyfile says `admin :2019`. Nothing
in the API reports how Caddy was started, so Vauxtra cannot detect the bad configuration
and warn. Build it only alongside documentation that leads with `--resume`.

**Pangolin** already occupies the layer Vauxtra occupies: resources, targets, its own
dashboard, its own tunnels, its own identity and access control. Driving it from Vauxtra
means two panels holding opinions about the same state. Its Integration API is real —
Bearer key, org- and root-scoped — and must be enabled explicitly on self-hosted
deployments. Revisit if users ask; do not lead with it.

**BIND9** has no HTTP API. Supporting it means dynamic updates over RFC 2136 with a TSIG
key: a new dependency and a transport unlike every other provider in the codebase, for a
server the self-hosting community is leaving rather than adopting.

**Unbound, dnsmasq, plain nginx and Apache** are configured by files. Vauxtra does not
write configuration files into other people's containers, and should not start.

## What the lab cannot prove

`lab/` runs the six self-hostable providers against real containers. Cloudflare,
Cloudflare Tunnel and deSEC are absent because nothing can fake them — testing them means a
real account and a real zone.

So the three capability flags with the thinnest coverage are still held only by providers
the lab cannot reach. **The capabilities with the least redundancy are also the ones with
no automated integration test.** PowerDNS narrows the gap where it can: it is the first
provider in the lab whose write path is a record *set* rather than a record, so the merge
logic that keeps a sibling record alive is now exercised against a real server instead of a
mock. deSEC shares that logic and that test shape — against a fake, for want of an
account.

## Adding a provider without forking

`VAUXTRA_PROVIDER_PLUGINS` takes a comma-separated list of importable Python modules, each
exposing `register()`, `PROVIDER_PLUGIN` or `PROVIDER_PLUGINS`. See `.env.example` for the
contract and an example. A module that fails to load is logged as a warning and skipped —
the application still starts, with that provider type simply absent.

These modules are **imported**, so their code runs with the application's privileges. Load
only modules you wrote or audited.

This is the right home for the long tail of registrar APIs: one operator's Porkbun account
does not need to be in this repository to be usable from this panel.
