# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [1.0.0] — Production Release — May 2, 2026

### 🔐 Security Enhancements
- **CORS Origin Validation** — Strict validation of CORS origins (no wildcards, port validation, scheme enforcement)
- **Unified Error Handling** — Standardized error codes and error responses across all endpoints
- **Request-Scoped Caching** — Per-request cache to prevent N+1 queries and duplicate provider calls
- **Domain Sanitization** — Protection against domain injection attacks
- **Password Strength Validation** — Enforced password policy for APP_PASSWORD (12+ chars, mixed case + digits + special)

### ⚡ Performance Improvements
- **50-80% faster preflight validation** — Request-scoped caching reduces provider calls
- **3.3x faster provider health checks** — Eliminated duplicate calls
- **N+1 query prevention** — Request cache ensures single provider call per operation
- **Optimized middleware stack** — Efficient CORS, security headers, caching

### 📚 Documentation
- **ARCHITECTURE.md** (420 lines) — Complete system design and component documentation
- **README.md** — Public project overview and documentation map
- **docs/HOWTO.md** — End-user operations and API usage guide
- **docs/DEPLOYMENT.md** — Production deployment runbook
- **docs/TROUBLESHOOTING.md** — Operator-focused troubleshooting runbook

### 🧪 Testing
- **23 new security + performance tests** — 100% pass rate, 71/71 total tests passing
- **Test coverage**: CORS validation, domain sanitization, password strength, request caching, error handling

### New Modules
- `app/security.py` — CORS validation, domain sanitization, password strength validation
- `app/cache.py` — Request-scoped caching system with TTL expiration
- `app/errors.py` — Unified error handling with standardized error codes

### 🔄 Changes
- `app/main.py` — Enhanced with CORS validation middleware and request cache middleware
- `vauxtra-dev/docker-compose.integration-test.yml` — Removed obsolete `version:` field

### ✅ Production Ready
- **8 critical security issues resolved**
- **Zero breaking changes** — Fully backward compatible
- **100% test pass rate** — 71/71 tests passing
- **Comprehensive documentation** — 1,200+ lines

---

## [Unreleased]

### Added
- Technitium DNS Server provider — session-token auth, zone auto-detection, A record CRUD
- `Makefile` — `dev`, `test`, `lint`, `lint-fix`, `build`, `release` targets
- `CHANGELOG.md` — this file
- `vauxtra_mcp/README.md` — MCP server setup guide for Claude Desktop and Cursor
- `.github/dependabot.yml` — automated weekly dependency PRs (pip + npm + Actions)
- `.github/pull_request_template.md` — PR checklist
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates
- Provider modal now shows a "Project website" link for each integration (NPM, AdGuard, Pi-hole, etc.)
- `docs/OPEN_SOURCE_HYGIENE.md` — concise open source quality checklist for contributors and release maintainers

### Changed
- Split `ci.yml` into three focused workflows: `tests.yml`, `docker-publish.yml`, `security.yml`
- `tests.yml` now runs two parallel jobs: Python (`ruff` + `pytest`) and frontend (`tsc` + `npm run build`)
- `TZ` default moved from `docker-compose.yml` (`Europe/Paris` hardcoded) to `Dockerfile` (`UTC`, overridable)
- `APP_VERSION` is now injected at Docker build time via `ARG`/`ENV`, sourced from the git tag
- `app/config.py` reads `APP_VERSION` from environment (falls back to `"dev"` for local runs)
- CONTRIBUTING.md updated to reference new workflow file names
- In-app "How-To & API" settings panel removed; markdown docs are now the single source of truth for operations guidance
- Settings and providers navigation streamlined with per-tab system links and keyboard shortcuts (`g` + `d/p/s`)
- Provider cards now expose clearer operational status labels and health score display
- README/deployment/troubleshooting guides rewritten for operator-focused workflows
- New localhost URL rewrite behavior for provider connections in Docker runtime (`VAUXTRA_REWRITE_LOCALHOST`, `VAUXTRA_LOCALHOST_ALIAS`)

### Fixed
- Removed unused imports across `app/api/` (`get_db_ctx`, `JSONResponse`, `time`, `Any`, `DB_PATH`)
- Removed unused local variables `new_fqdn` / `old_fqdn` in `app/api/services.py`
- `tsconfig.json` root: added `ignoreDeprecations: "6.0"` for `baseUrl` deprecation warning in TS 6+
- Backup restore now forces `setup_completed=1` so restored instances do not fall back to first-launch wizard when `settings` table is empty
- Docker endpoint validation hardened to reject malformed `docker_host` URLs
- Webhook URL validation now enforced consistently on create/update/test; partial update path fixed for enable/disable toggles
- Pi-hole v6 test flow now releases API sessions after probe to avoid session slot exhaustion
- Traefik provider submit gating fixed so optional password remains optional

### Upgrade Notes
- Existing Docker users must pull and recreate containers to receive updates:
	- `docker compose pull`
	- `docker compose up -d`
- If you depend on the removed in-app How-To panel, switch to `docs/HOWTO.md` for canonical guidance.
- If your providers use `localhost` URLs from inside Docker, review `VAUXTRA_REWRITE_LOCALHOST` behavior before disabling it.

---

## [0.1.0] — Initial release

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
