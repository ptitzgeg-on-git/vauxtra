# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

Security audit of v1.1.0 and the fixes it produced. Everything below is on
`fix/audit-securite-v1.1.0`; the test suite went from 284 to 728.

### Security
- **Path traversal in the SPA catch-all route.** `/{full_path:path}` does not strip `..`
  segments and uvicorn does not normalize the path, so the raw URL segment was joined to
  the build directory unchecked: `GET /%2e%2e/%2e%2e/data/vauxtra.db` served the SQLite
  database, and `data/.secret_key` after it — decryptable provider passwords and a
  forgeable admin session. Paths are now confined with `realpath`.
- **An abandoned setup wizard left ten routes anonymous for good.** `is_setup_incomplete()`
  read only the `setup_completed` flag, written at the wizard's very last click;
  `POST /api/restore` was among the routes it left open. The condition now turns False as
  soon as a password or a provider exists, and the flag is set when either is created.
- **`POST /api/restore` destroyed the instance before validating the passphrase.**
  `executescript()` issues an implicit COMMIT, so the `BEGIN EXCLUSIVE` closed over an
  empty transaction, the thirteen DELETEs committed in autocommit, and the rollbacks
  undid nothing. A dry decryption pass now runs before anything is destroyed.
- **The admin password hash was treated as business data.** The `settings` table was
  readable with a `read` scope, exported in backups, wiped by `/api/reset` and
  `/api/restore` (which dropped the instance back to anonymous admin), and re-injectable
  from an imported file. Read allow-list, protected keys on wipe, import filter, and the
  cleartext export moved to the `admin` scope.
- **Ten routes accepted any authenticated key, scope or not.** `require_auth(request)`
  without `scope=` never consults the scope hierarchy. The preflight probe was the worst
  of them: host and port come from the request body, so a read-only key was a port scanner
  aimed at whatever the container can reach. Also check-all, provider test / validate /
  validate-draft, test-webhook (now `write`), change-password and setup-complete (now
  `admin`), push/dry-run and services/sync (now explicitly `read`). A static test rejects
  any POST/PUT/DELETE/PATCH route that calls `require_auth*` without a scope.
- **The MCP bridge's `--http` mode listened on 0.0.0.0 with no authentication of its own**,
  while holding an API key that reaches every route. It now binds `127.0.0.1` by default;
  `VAUXTRA_MCP_HOST` publishes it, with a warning on stderr.
- **The setup wizard mirrored the provider password into `sessionStorage`** so the form
  would survive a reload — including the proxy admin password or Cloudflare API token.
  Everything else in the form still comes back; that field is typed again.
- **Changing the admin password logged everyone out and changed nothing.** With
  `APP_PASSWORD` set, `check_password` returns on the variable and never reads the stored
  hash — but `POST /api/auth/change-password` wrote one anyway and then bumped the session
  epoch. Every session died, the operator's included, the new password was refused, and the
  way back in was the password they had just tried to retire. The route answers 409 before
  verifying or writing anything, `GET /api/auth/me` carries `password_source`, and the
  Security screen explains instead of offering a form that cannot work.

### Fixed
- **Four Settings components were never committed.** The `.gitignore` rule `data/` was
  unanchored, so it matched `frontend/src/components/features/settings/data/` as well as
  the SQLite directory it was written for, and `git add -A` skipped them without a word.
  The build passed on any machine that had them on disk. The rules are anchored to the
  repository root, and the hygiene gate now refuses any source file `.gitignore` hides.
- **CI could not collect the test suite.** `tests/test_mcp_*.py` import fastmcp, which
  lives only in `vauxtra_mcp/requirements.txt`; the workflow installed
  `requirements.txt pytest ruff`. It installs `requirements-dev.txt`, which pulls both.
- **Deleting a service left its routes serving.** `delete_service` walked only
  `proxy_provider_id` and `dns_provider_id`; the extra targets in `service_push_targets`
  were never told, so the hostname stayed resolvable and the proxy kept forwarding with
  nothing left in Vauxtra to show for it. Withdrawal now covers every provider that may
  hold a route, disabled ones included — turning a provider off in Vauxtra does not take
  its published route off the internet. `bulk_action` had the same gap.
- **Deleting a service purged its neighbours' logs.** v1.1.0 narrowed the log cleanup to
  `LIKE '%service <id>%'`, which still matches "service 12", "service 100" and every id
  that merely starts with this one: deleting service 1 wiped the monitoring history of
  nine other services. Now a bounded GLOB. `bulk_action` never purged logs at all.
- **Disabling a service did not cut its public exposure.** In tunnel mode the publish
  branch of `update_service` never read `body.enabled` and republished the ingress rule it
  was meant to remove; `bulk_action` skipped tunnel-mode rows outright. The operator had
  only stopped monitoring the host, not closed it. Editing an already-disabled service
  replayed `add_rewrite`, and a service created disabled was published immediately.
- **A failed Cloudflare Tunnel read looked exactly like an empty tunnel.**
  `_get_configuration()` turned any error into `{}`, and the PUT replaces the whole
  ingress: adding a service during a transient 502 silently deleted every other route in
  the tunnel. It now returns `None` on failure and the writers refuse to act on it.
  `_delete_dns_record` no longer claims success when the zone could not be resolved.
- **No read timeout on any provider call.** `session.timeout` does not exist in `requests`
  — the attribute was set and never read. A provider that accepts the connection then goes
  quiet blocked the scheduler's single thread, stopping all monitoring without a message.
- **The health-check cycle deadlocked itself.** `_run_dns_auto_updates()` ran inside the
  write transaction opened by `run_health_checks()` and opened a second connection; SQLite
  allows one writer, so it waited out its busy timeout, raised "database is locked", and
  every status of that round was lost before the commit.
- **A DOWN alert that failed to send was silenced for good.** `_alert_down_sent` was set
  before `notify()` was even called, and its return value was neither checked nor logged.
  All four notification paths now go through `_try_send_apprise()` — which existed but was
  called nowhere — and a failed send releases the lock instead of keeping it.
- **`_alert_down_since` persisted `time.monotonic()` values**, whose origin changes with
  every process: after a restart, every computed duration was nonsense. Retries were
  stamped in ISO while the queue compares with `next_retry_at <= datetime('now')`, a
  string comparison where "T" sorts above " ": a retry due today only came due tomorrow.
- **NPM forced HTTPS on a certificate that did not cover the host.**
  `find_best_certificate` fell back to the first certificate it found, and `create_host`
  sets `ssl_forced` as soon as an id exists — a wall of errors on every visit. Matching
  now follows the TLS rule (a wildcard covers exactly one label).
- **A toggle from the MCP bridge stripped a service's tags and environments.** The GET
  serializes relations as `tags`/`environments` while the PUT expects
  `tag_ids`/`environment_ids`: pydantic ignored the unknown keys, applied empty defaults,
  and `set_tags` starts with a DELETE.
- **The docs told operators to set `APP_PASSWORD` in cleartext; the code refused it in
  silence.** `check_password` only compares cleartext when `ALLOW_PLAINTEXT_APP_PASSWORD`
  is true (default false), and that variable appeared nowhere outside `app/auth.py`. The
  operator got a 401 on the password they had just configured, a closed setup wizard, and
  not one line in the logs.
- **A half-failed delete said nothing in the UI.** The mutation dropped the response, so
  the row vanished from the table while the hostname went on answering from the internet.
  Provider failures are now reported, and `onError` shows the API's own message.
- **Five French strings carried a `?` where the accent belonged** ("?chec de v?rification
  des services"), and `Settings.tsx` asked for `settings.api_keys.create_failed`, which no
  locale defined — `t()` falls back to printing the key, so the error toast read
  `settings.api_keys.create_failed`.
- **The three big modals were plain divs stacked over the page**: no `role="dialog"`, no
  `aria-modal`, no Escape, and Tab walked out of the box into the form underneath.
- **The log filter queried a level the backend never writes.** The chips asked for
  `ok`, which no code path records, and offered no `warn`, which several do — so
  two filters returned an empty table and one class of log was unreachable. Levels
  are now normalised on write and the same set is used on both sides.
- **The DNS suffix field accepted names the server rejects.** A single label passed
  the browser regex and came back as a 400 from the API.
- **Monitoring poisoned the shared endpoints cache.** It wrote its normalised list to
  session storage on every render, including the empty list that exists before the
  query resolves — so a reload could paint an empty Endpoints page from the cache.
- **A certificate expiring near midnight UTC was counted a day off**, in one direction
  west of Greenwich and the other east: the fallback route serves a naive timestamp and
  the browser read it as local time.
- **Deleting an integration forced the delete without asking.** The call sent
  `force=true` up front, so the 409 listing the endpoints that depend on it was never
  shown. The confirmation now names them before anything is removed.
- Writing to an integration left its inspector panel stale — health, proxy hosts and DNS
  records were keyed per id and never invalidated.

### Changed
- `ServiceIn` now rejects unknown keys (`extra="forbid"`). **Breaking** for any client that
  echoed a GET body straight back into a PUT — it gets a 422 instead of a service stripped
  of its relations.
- The MCP bridge's default request timeout is 120 s instead of 30 s (`VAUXTRA_TIMEOUT`);
  30 s cut off a push that walks the providers in series.
- `DELETE /api/services/{id}` returns `{"ok": true, "errors": [...]}` even when a provider
  refused to withdraw the route: the service is gone from Vauxtra either way, and `false`
  would push a client into retrying a delete that can only answer 404.
- Docs follow the code: a Scopes section in HOWTO, `APP_PASSWORD` /
  `ALLOW_PLAINTEXT_APP_PASSWORD` documented in `.env.example` and DEPLOYMENT, and
  `vauxtra_mcp/README.md` no longer advertises an "all" scope that does not exist.
- Errors the API returns for a preflight refusal, a drift issue or a provider
  diagnostic now carry a `detail_key` and its parameters beside the English
  `detail` (60 codes), so the UI shows them in the user's language instead of an
  English sentence. Old clients keep reading `detail`.
- The interface is fully translated: 297 to 1783 keys per language, the same keys
  in the same order in all eight locales, and no English string left in the code.
- Six environment variables the code reads are documented in `.env.example`:
  `VAUXTRA_PROVIDER_PLUGINS` (an extension mechanism that imports the Python modules named
  in it), `VAUXTRA_REWRITE_LOCALHOST` and `VAUXTRA_LOCALHOST_ALIAS` (whether a provider URL
  on `localhost` is rewritten to `host.docker.internal`, and to what), and the bridge's
  `VAUXTRA_MCP_HOST` / `VAUXTRA_MCP_PORT` / `VAUXTRA_TIMEOUT`. A test holds the sync both
  ways: a variable read without documentation fails, and so does a documented one nothing
  reads.
- `DOCKER_HOST` said "override if using a non-standard location". It only seeds the first
  Docker endpoint on an empty database; afterwards the endpoint list owns the value and the
  variable does nothing. The text says so now.
- Grype is pinned to v0.118.0. At v0.96.0 it could no longer read the vulnerability
  database anchore publishes, so the weekly scan was red on `grype db update` for a reason
  unrelated to this repository.
- `.github/dependabot.yml` is back, grouped. It was deleted in the v1.1.0 release commit
  and nothing watched the dependencies for the three months that followed.

### Added
- **Zoraxy provider** (`zoraxy`, Reverse Proxy). Drives host rules through the management
  API on port 8000 (session login + CSRF token; empty credentials for a `-noauth`
  instance): create, update, enable/disable, delete, import, drift detection, HTTP or
  HTTPS upstream, WebSocket toggle, and the certificate whose CN matches the hostname or
  its parent wildcard recorded as the rule's preferred certificate. Host rules only, one
  managed upstream per rule; no per-host "force SSL" since Zoraxy terminates TLS globally.
- `useModalDialog` — Escape to close, a focus trap, and focus restored to whatever opened
  the dialog. Applied to the expose, provider and connection-editor modals.
- Static guards that need neither a browser nor Node: locale key parity, placeholder
  parity, no accent lost to a `?`, every `t('…')` key defined, and every full-screen
  overlay declaring itself a dialog. CI runs `i18n:quality` but never `i18n:check`, so
  key parity was ungated until now.
- **Rebuilt interface.** One design system behind every screen: 26 primitives
  (buttons, cards, modals, drawers, fields, tabs, empty states, skeletons,
  tooltips), light/dark tokens, a single focus ring, and animations that stand
  down under `prefers-reduced-motion`. The shell gained a collapsible sidebar
  whose badges count what needs attention (enabled endpoints, integrations,
  expiring certificates, endpoints in error), a Ctrl/Cmd+K command palette over
  pages, settings tabs, endpoints, integrations and languages, a shortcuts sheet
  (`?`) with `g`-chords, and a mobile header with a navigation drawer. The theme
  is applied before the first paint, so a dark instance no longer flashes white.
- **Templates page** (`/templates`) — the saved service templates now have a screen.
- Settings split into nine tabs across their own files (`Settings.tsx`: 1909 lines
  to 134), and the data tab into four sections (sync, Docker, export, restore).

---

## [1.1.0] — 2026-06-12

### Added
- **Service Templates** (`GET/POST/PUT/DELETE /api/templates`, `GET /api/templates/{id}/apply`) — pre-configured service blueprints that pre-fill the service form with provider assignments, scheme, port, domain, and tags. Applied via `/api/templates/{id}/apply` or MCP `apply_template`. DB schema v10.
- **Prometheus metrics** (`GET /metrics`) — no-auth endpoint exposing service counts by status, provider counts by type, log counts (24 h), uptime event counts, webhook stats, template count, and schema version in Prometheus text exposition format.
- **Webhook retry with exponential backoff** — failed Apprise deliveries are logged to `webhook_delivery_log` and retried by the scheduler with backoffs of 60 s, 5 min, 30 min, 2 h, 24 h. Delivered/failed entries are purged after 7 days (configurable via `webhook_retry_retention_days`).
- **Certificate expiry alerts** — scheduler now scans certificates on all enabled NPM providers each health-check cycle and logs `warn` (< 30 days) or `error` (< 7 days) alerts. Deduplication prevents spam: re-alerts only after 24 h or when severity level changes.
- **MCP template tools** (`list_templates`, `get_template`, `create_template`, `delete_template`, `apply_template`) — full template CRUD and one-shot service creation from a template via MCP.
- **284 unit tests** — 7 new provider test files (NPM, AdGuard, Pi-hole, Traefik, Cloudflare, Cloudflare Tunnel, Scheduler) + 2 integration test files (Templates API, Metrics endpoint). All 284 pass.

### Changed
- Expose Route UX now derives DNS behavior from provider capabilities (including `public_dns`) instead of hardcoded provider types.
- DNS-only local routes now derive DNS target directly from the internal target host/IP (without a separate DNS target field), reducing manual friction for LAN-only routes.
- Validation copy now distinguishes local DNS target vs external WAN target to avoid operator confusion.
- Service subdomain validation now accepts wildcard `*` for wildcard host routing (e.g. `*.example.com`).
- Route creation now requires at least one provider target (proxy or DNS), preventing no-op "manual" routes.
- NPM status sync moved from `GET /api/services` (called on every page load) to the health-check scheduler cycle — eliminates blocking HTTP calls on every service list fetch.
- Request-scoped cache now uses `contextvars.ContextVar` instead of a module-level global, making it safe under concurrent async requests.
- `.gitattributes` added — repository line endings normalized to LF; eliminates CRLF conversion warnings on Windows checkouts.

### Fixed
- Services / Expose modal no longer crashes (React error #31) when backend returns structured 422 validation errors; error details are now normalized to user-facing strings.
- Wildcard endpoint links (`*.domain`) are rendered as non-clickable labels to avoid invalid `%2A` navigation URLs.
- `POST /api/services/check-all` now skips tunnel-mode services (previously attempted TCP checks against Cloudflare Tunnel endpoints, always producing spurious `error` status).
- `DELETE /api/services/{id}` log cleanup no longer matches logs from unrelated services that share a hostname substring; scope narrowed to service-ID-specific log entries only.
- `POST /api/services/{id}/reconcile` now correctly fixes DNS drift — previously `push_service` compared stored DB value against itself when they were equal, causing `update_rewrite` to short-circuit as a no-op; push now reads the actual live value from the provider before deciding to delete+re-add.
- `POST /api/services` (proxy+DNS mode) no longer leaves an orphaned NPM proxy host when DNS target resolution subsequently fails — the host is now removed from NPM before the `400` is returned.
- NPM `toggle_host` was calling `PATCH /nginx/proxy-hosts/{id}` which does not exist in NPM; fixed to use the correct `POST /nginx/proxy-hosts/{id}/enable` and `.../disable` endpoints. Service enable/disable from the UI now correctly pauses/resumes the host in NPM.
- Dashboard "Recent activity" → "Logs" link was navigating to `/monitoring` (service health view) instead of `/settings?tab=logs` (full action log).
- Service creation form "Base Domain" was a `<select>` locked to pre-configured domains — if none were configured, the form could not be submitted. Changed to a free-text `<input>` with an optional `<datalist>` for saved domains.
- Internal navigation links in the service form (`/providers`, `/settings`) were plain `<a href>` causing full page reloads; replaced with React Router `<Link>`.
- Service enable/disable now removes DNS rewrites from all DNS providers on disable and re-adds them on enable — keeps DNS state in sync with service state instead of leaving stale records.
- Provider suspend is now generalized: providers that support toggle (NPM) have their proxy host suspended/resumed; providers that do not support toggle (Traefik, etc.) have their proxy host deleted from the provider on disable and re-deployed from Vauxtra config on re-enable. The same logic applies to bulk enable/disable.

---

## [1.0.1] — 2026-05-04

### Added
- Technitium DNS Server provider — session-token auth, zone auto-detection, A record CRUD
- `Makefile` — `dev`, `test`, `lint`, `lint-fix`, `build`, `release` targets
- `CHANGELOG.md` — this file
- `vauxtra_mcp/README.md` — MCP server setup guide for MCP-compatible clients
- `.github/dependabot.yml` — automated weekly dependency PRs (pip + npm + Actions)
- `.github/pull_request_template.md` — PR checklist
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates
- `.github/CODEOWNERS` — default code ownership for PR reviews
- Provider modal now shows a "Project website" link for each integration (NPM, AdGuard, Pi-hole, etc.)
- Grype + Syft SBOM scan added to security workflow; scans run on every PR to `main`
- cosign keyless image signing on every published Docker image (Sigstore)
- `ruff.toml` — explicit linter configuration

### Changed
- Split `ci.yml` into three focused workflows: `tests.yml`, `docker-publish.yml`, `security.yml`
- `tests.yml` now runs two parallel jobs: Python (`ruff` + `pytest`) and frontend (`tsc` + `npm run build`)
- `TZ` default changed from `Europe/Paris` to `UTC` across all config files and examples
- `APP_VERSION` is now injected at Docker build time via `ARG`/`ENV`, sourced from the git tag
- `app/config.py` reads `APP_VERSION` from environment (falls back to `"dev"` for local runs)
- `trivy-action` pinned to a specific version (was `@master`)
- In-app "How-To & API" settings panel removed; markdown docs are the single source of truth
- Settings and providers navigation streamlined with per-tab system links and keyboard shortcuts (`g` + `d/p/s`)
- Provider cards now expose clearer operational status labels and health score display
- README/deployment/troubleshooting guides rewritten for operator-focused workflows
- New localhost URL rewrite behavior for provider connections in Docker runtime (`VAUXTRA_REWRITE_LOCALHOST`, `VAUXTRA_LOCALHOST_ALIAS`)

### Fixed
- Removed unused imports across `app/api/` (`get_db_ctx`, `JSONResponse`, `time`, `Any`, `DB_PATH`)
- Removed unused local variables `new_fqdn` / `old_fqdn` in `app/api/services.py`
- `tsconfig.json` root: added `ignoreDeprecations: "6.0"` for `baseUrl` deprecation warning in TS 6+
- Backup restore now forces `setup_completed=1` so restored instances skip first-launch wizard when `settings` table is empty
- Docker endpoint validation hardened to reject malformed `docker_host` URLs
- Webhook URL validation now enforced consistently on create/update/test; partial update path fixed for enable/disable toggles
- Pi-hole v6 test flow now releases API sessions after probe to avoid session slot exhaustion
- Traefik provider submit gating fixed so optional password remains optional

### Upgrade Notes
- Pull and recreate containers to receive updates:
  ```bash
  docker compose pull && docker compose up -d
  ```
- If providers use `localhost` URLs from inside Docker, review `VAUXTRA_REWRITE_LOCALHOST` behavior.

---

## [1.0.0] — 2026-05-02

### Added
- `app/security.py` — CORS origin validation, domain sanitization, password strength enforcement
- `app/cache.py` — request-scoped caching with TTL expiration (eliminates N+1 provider calls)
- `app/errors.py` — unified error handling with standardized error codes across all endpoints
- Core operator documentation set: `docs/HOWTO.md`, `docs/DEPLOYMENT.md`, `docs/TROUBLESHOOTING.md`

### Changed
- CORS validation: strict origin checking, no wildcards, port and scheme enforcement
- Error responses: standardized codes and shapes across all API endpoints
- `app/main.py` — CORS validation middleware and request cache middleware added

### Fixed
- 8 security issues resolved (CORS, domain injection, password policy, session handling)

---

## [0.1.0] — 2026-04-01

### Added
- Multi-provider service management (NPM, Traefik, Cloudflare, Pi-hole, AdGuard Home, Cloudflare Tunnel)
- Docker container discovery with Traefik label parsing and confidence scoring
- Preflight validation, dry-run push, drift detection, and reconcile
- Auto-reconcile scheduler with webhook (Apprise) notifications
- Certificate expiry monitoring
- API key authentication (Bearer tokens) for CI/CD and MCP
- MCP server exposing core operations as tools for MCP-compatible clients
- React 19 + TypeScript SPA with Tailwind CSS
- SQLite (WAL mode) — zero external dependencies
- Multi-architecture Docker image (linux/amd64 + linux/arm64) via GHCR

---

[Unreleased]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v1.0.2
[1.0.1]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v1.0.1
[1.0.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases
[0.1.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v0.1.0
