# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

---

## [1.0.1] — 2026-05-04

### Added
- Technitium DNS Server provider — session-token auth, zone auto-detection, A record CRUD
- `Makefile` — `dev`, `test`, `lint`, `lint-fix`, `build`, `release` targets
- `CHANGELOG.md` — this file
- `vauxtra_mcp/README.md` — MCP server setup guide for Claude Desktop and Cursor
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
- ARCHITECTURE.md — system design and component documentation
- docs/HOWTO.md, docs/DEPLOYMENT.md, docs/TROUBLESHOOTING.md

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
- MCP server exposing core operations as tools for Claude Desktop and Cursor
- React 19 + TypeScript SPA with Tailwind CSS
- SQLite (WAL mode) — zero external dependencies
- Multi-architecture Docker image (linux/amd64 + linux/arm64) via GHCR

---

[Unreleased]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v0.1.0
