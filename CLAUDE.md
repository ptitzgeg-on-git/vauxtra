# Vauxtra — AI Directives

> Guidelines for AI assistants (Claude Code, Cursor, Copilot, etc.) working in this codebase.
> Read this file before making any changes.

---

## Project Overview

**Vauxtra** is a self-hosted DNS & reverse proxy management panel for homelab.
It orchestrates NPM, Traefik, Cloudflare, Pi-hole, AdGuard, and Technitium from a single React UI backed by FastAPI.

**Primary repo:** `vauxtra_github/` (this directory)

---

## Stack — Current (Do Not Change)

### Backend
| Layer | Technology |
|---|---|
| Framework | FastAPI + Uvicorn, Python 3.12+ |
| Database | SQLite (WAL mode) |
| DB abstraction | `app/db.py` → `get_connection()` |
| Auth | Session cookies + Bearer API keys (`app/api/api_keys.py`) |
| Rate limiting | `slowapi` (`app/limiter.py`) |
| Background jobs | APScheduler (`app/scheduler.py`) |
| MCP server | FastMCP (`vauxtra_mcp/`) |
| Secrets | Fernet encryption for all provider credentials |
| Notifications | Apprise webhooks |

### Frontend
| Layer | Technology |
|---|---|
| UI framework | React 19 + TypeScript |
| Build tool | Vite |
| Styling | Tailwind CSS 3 |
| Data fetching | React Query v5 (`@tanstack/react-query`) |
| Routing | React Router v7 |
| Icons | lucide-react |
| Toasts | react-hot-toast |
| HTTP client | Axios (`frontend/src/api/client.ts`) |

**No new dependencies without explicit user approval.**

---

## Language & Localisation

- **English only** — all UI strings written directly in JSX/TSX
- **No i18n system** — no translation keys, no `t()` function, no JSON locale files

---

## Frontend Conventions

### File structure
```
frontend/src/
  pages/              # One file per route
  components/
    layout/           # Sidebar, Layout
    features/
      expose/         # ExposeModal + ServiceForm + ServicePreview + types.ts
      providers/      # DockerImport + providerConstants.ts
      ProviderModal.tsx
    ui/               # Shared primitives (ConfirmDialog, ErrorBoundary, ProviderLogos)
  hooks/              # Custom React hooks
  types/api.ts        # Strict TypeScript interfaces — keep in sync with FastAPI
  api/client.ts       # Axios wrapper with generics
```

### Component patterns
- Cards: `bg-card border border-border rounded-xl shadow-sm`
- Status dots: `w-2 h-2 rounded-full bg-primary | bg-destructive | bg-muted-foreground/40`
- Muted labels: `text-sm font-medium text-muted-foreground`
- Primary actions: `bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-60`
- Danger: `text-destructive`, `border-destructive/30`, `bg-destructive/5`

### CSS tokens

Raw variables: `--vx-*` prefix in `src/index.css`.
Tailwind utilities: `bg-card`, `text-foreground`, `border-border`, `text-muted-foreground`, `bg-primary`, `text-destructive`.
Dark mode: `.dark` class on `<html>` (managed in `theme.tsx`).

### TypeScript
- Strict mode enabled (`tsconfig.app.json`)
- All API types in `frontend/src/types/api.ts`
- No `any` — use proper types or `unknown`
- React Query v5: `useQuery`, `useMutation` — no direct `fetch`

---

## Backend Conventions

### API routes
- All routes under `/api/` prefix
- Auth via `require_auth` dependency or Bearer token
- Rate limiting via `@limiter.limit(...)` on sensitive endpoints
- Router order: specific paths before parameterised ones (`/topology` before `/{sid}`)

### Database
- Use `app/db.py → get_connection()` — never call `sqlite3.connect()` directly
- Close connections in `finally` blocks
- Column access via dict-style (`sqlite3.Row` row factory)

### Error handling
- Never use bare `except Exception: pass` — always log with `add_log("error", ...)`
- `add_log(level, message)` from `app/models.py`
- Include `traceback.format_exc()` for unexpected exceptions

### Provider system
- Base classes: `DNSProvider` / `ProxyProvider` (`app/providers/base.py`)
- Registry + metadata: `app/providers/factory.py` → `_PROVIDER_REGISTRY` + `PROVIDER_TYPES`
- To add a provider: see `CONTRIBUTING.md` — "How to Add a DNS Provider"
- Frontend reads provider types from `GET /api/providers/types` at runtime

---

## What NOT to Do

- ❌ Do NOT add framer-motion (removed — was unused, 160 KB)
- ❌ Do NOT use Bootstrap, Tabler.io CSS, or any CSS framework other than Tailwind
- ❌ Do NOT create i18n/translation files or use `t()` patterns
- ❌ Do NOT use `data-theme` — use `.dark` class on `<html>`
- ❌ Do NOT call `sqlite3.connect()` directly — use `app/db.py`
- ❌ Do NOT use bare `except: pass`
- ❌ Do NOT add `console.log` in production code (guard with `import.meta.env.DEV`)
- ❌ Do NOT add speculative abstractions or unused utilities
- ❌ Do NOT install new Python or npm packages without user approval

---

## Running Locally

```bash
# Backend
pip install -r requirements.txt
uvicorn app.main:app --port 8888 --reload

# Frontend (dev)
cd frontend && npm install && npm run dev   # Vite on :5173, proxies /api → :8888

# Frontend (build check)
cd frontend && npm run build && npx tsc --noEmit

# Full stack (Docker)
cp .env.example .env
docker compose up --build -d
# App: http://localhost:8888
```

## Tests & Linting

```bash
# Python tests
python -m pytest tests/ -v

# Python lint (ruff)
ruff check app/ vauxtra_mcp/

# Frontend typecheck
cd frontend && npx tsc --noEmit

# Frontend lint
cd frontend && npm run lint
```

---

## Git Workflow for AI Agents

> This section tells an AI assistant exactly how to branch, commit, PR, and release.

### Branch naming

| Type | Pattern | Example |
|---|---|---|
| Feature | `feat/<short-description>` | `feat/technitium-provider` |
| Bug fix | `fix/<short-description>` | `fix/scheduler-duplicate-jobs` |
| Docs | `docs/<short-description>` | `docs/mcp-setup` |
| Chore | `chore/<short-description>` | `chore/upgrade-fastmcp` |
| Refactor | `refactor/<short-description>` | `refactor/extract-preflight` |

Always branch off `main`. Never commit directly to `main`.

### Commit messages — Conventional Commits

```
feat(providers): add Technitium DNS provider
fix(scheduler): prevent duplicate tunnel health jobs
refactor(services): extract preflight into dedicated module
docs: update MCP integration guide
chore: upgrade fastmcp to 2.1
```

Scopes: `providers`, `services`, `scheduler`, `auth`, `docker`, `mcp`, `frontend`, `ui`, `settings`, `docs`, `ci`

### Typical task flow

```bash
git checkout main && git pull origin main
git checkout -b feat/my-feature

# ... make changes ...

python -m pytest tests/ -v                    # must pass
cd frontend && npx tsc --noEmit && cd ..      # must pass
ruff check app/ vauxtra_mcp/                  # must pass

git add <specific files>                      # never git add -A blindly
git commit -m "feat(scope): short description"
git push -u origin feat/my-feature
gh pr create --title "feat(scope): ..." --body "..."
```

### PR rules

- Target branch: `main`
- Title: Conventional Commit format
- Body: what changed + why + test steps
- All CI checks must pass before merge
- **Never force-push to `main`**
- **Never merge your own PR without user approval**

### Things an AI must NOT do without explicit user confirmation

| Action | Why |
|---|---|
| `git push --force` to any branch | Destructive — can overwrite upstream work |
| Push directly to `main` | Bypasses CI and PR review |
| Create or publish a release | Triggers Docker build + public image push |
| Delete branches | May destroy in-progress work |
| Modify `.env` or secrets | Could break the running instance |
| Run `docker compose down -v` | Destroys data volumes |

---

## Release Process

Releases are fully automated once a tag is pushed. The Docker image is built and pushed to GHCR, and a GitHub Release is created automatically.

### How to cut a release

```bash
# 1. Make sure main is clean and tests pass
git checkout main && git pull origin main
python -m pytest tests/ -v

# 2. Create and push a version tag (semver)
git tag v0.2.0
git push origin v0.2.0

# 3. Watch the CI
#    .github/workflows/docker-publish.yml runs automatically:
#    - Builds linux/amd64 + linux/arm64 Docker images
#    - Pushes ghcr.io/<owner>/vauxtra:0.2.0 + :latest
#    - Creates a GitHub Release with auto-generated notes
```

### Version is injected at build time

`APP_VERSION` is passed as a Docker build-arg from `steps.meta.outputs.version`.
At runtime `app/config.py` reads it: `APP_VERSION = os.environ.get("APP_VERSION", "dev")`.
The version is exposed at `GET /api/health` → `{ "version": "0.2.0" }`.

### Semver conventions for this project

| Tag | When |
|---|---|
| `v0.x.0` | New feature or breaking change while < v1 |
| `v0.0.x` | Bug fix or patch |
| `v1.0.0` | First stable public release |

---

## CI Overview

| Workflow | Trigger | What it does |
|---|---|---|
| `tests.yml` | Every push + PR | Python compile, pytest, ruff, frontend tsc + build |
| `docker-publish.yml` | Push to `main` + version tags | Multi-arch Docker build + GHCR push + GitHub Release |
| `security.yml` | Every Monday 03:00 UTC + manual | Trivy vulnerability scan → GitHub Security tab |

---

## Key Non-Obvious Endpoints

| Endpoint | Notes |
|---|---|
| `GET /api/services/topology` | Must be registered BEFORE `GET /api/services/{sid}` |
| `GET /api/services/templates` | 7 service presets from `app/templates.py` |
| `POST /api/services/{sid}/check-dns-propagation` | Queries 8.8.8.8, 1.1.1.1, 9.9.9.9 |
| `GET /api/certificates/expiry` | Sorted urgent-first, includes `expiring_soon_count` |
| `GET /api/providers/{pid}/services/analyze` | Enriched Traefik analysis |
| `GET /api/docker/containers` | Discovery with `suggestion` (confidence/source) |

---

## Architecture Decisions

| Decision | Choice | Why |
|---|---|---|
| Backend language | Python/FastAPI | HTTP orchestrator, not a systems daemon |
| Database | SQLite (WAL) | Homelab = single file, no external DB |
| Frontend | React 19 SPA | Migrated from Jinja2/Vanilla JS |
| Localisation | English only | No i18n library, global GitHub audience |
| MCP | FastMCP + API Keys | Native Claude Desktop / Cursor integration |
| Remote Docker | Docker API (tcp/ssh) | No agent needed on remote hosts |
| Traefik | Read-only + enriched analysis | Vauxtra reads Traefik config, does not write it |
