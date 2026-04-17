# Contributing to Vauxtra

Thank you for your interest in contributing! This guide covers local setup, code conventions, and how to extend the project.

## Local Development Setup

### Backend (FastAPI)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# MCP server (optional, for Claude Desktop / Cursor integration)
pip install -r vauxtra_mcp/requirements.txt

# Start the backend (auto-reloads on file changes)
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
```

The API is available at `http://localhost:8888`. Interactive docs at `http://localhost:8888/api/docs` (requires `DEBUG=true` in `.env`).

### Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev   # Dev server at http://localhost:5173 (proxies /api → :8888)
```

### Full stack via Docker

```bash
cp .env.example .env
docker compose up --build
```

Access: `http://localhost:8888`

---

## Running Tests

```bash
# Backend tests
python -m pytest tests/ -v

# Frontend type check
cd frontend && npm run build

# Frontend lint
cd frontend && npm run lint
```

---

## Commit Convention

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(providers): add HAProxy provider
fix(scheduler): prevent duplicate tunnel health jobs
refactor(services): extract preflight into dedicated module
docs: update MCP integration guide
chore: upgrade fastmcp to 2.1
```

Scopes: `providers`, `services`, `scheduler`, `auth`, `docker`, `mcp`, `frontend`, `ui`, `settings`, `docs`

---

## How to Add a DNS Provider

> **Plugin architecture:** all provider metadata (labels, colors, guided wizard steps) lives in `app/providers/factory.py`. The React frontend reads it from the API at runtime. You only need to touch frontend code if you want a custom SVG logo.

### 1. Implement the provider class

Create `app/providers/myprovider.py` and implement the `DNSProvider` abstract class from `app/providers/base.py`:

```python
from app.providers.base import DNSProvider

class MyProvider(DNSProvider):
    def __init__(self, url: str, username: str, password: str): ...
    def test_connection(self) -> bool: ...
    def list_rewrites(self) -> list[dict]: ...       # return [{"domain": ..., "answer": ...}]
    def add_rewrite(self, domain: str, ip: str) -> bool: ...
    def delete_rewrite(self, domain: str, ip: str) -> bool: ...
    # update_rewrite() is inherited from base (add-first, then delete)
```

### 2. Register in `app/providers/factory.py`

Add to **`_PROVIDER_REGISTRY`** (class mapping):

```python
from app.providers.myprovider import MyProvider

_PROVIDER_REGISTRY: dict[str, tuple[type, bool]] = {
    # ...
    "myprovider": (MyProvider, False),   # True if provider needs an `extra` JSON dict
}
```

Add to **`PROVIDER_TYPES`** (UI metadata served to the frontend):

```python
PROVIDER_TYPES = {
    # ...
    "myprovider": {
        "label": "My Provider",
        "category": "dns",                  # "dns" or "proxy"
        "available": True,
        "description": "Short description shown in provider cards",
        "category_label": "Local DNS",      # Group heading in the type selector
        "category_color": "bg-purple-500/10 text-purple-600 dark:text-purple-400",
        "provider_color": "bg-purple-500/10 text-purple-600 border-purple-500/30 dark:text-purple-400",
        "capabilities": {
            "proxy": False, "dns": True,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "placeholder_url": "http://192.168.1.10:1234",
        "user_label": "Username",
        "pass_label": "Password",
        "user_placeholder": "admin",
        # Guided wizard steps (optional but recommended)
        "guided_steps": [
            {
                "title": "Enter your My Provider URL",
                "body": "Instructions shown during the guided setup.",
                "fields": [
                    {"key": "url", "label": "URL", "placeholder": "http://...",
                     "hint": "Help text", "input_type": "url"},
                ],
            },
            {
                "title": "Credentials",
                "body": "Enter your admin credentials.",
                "fields": [
                    {"key": "username", "label": "Username", "placeholder": "admin",
                     "input_type": "text"},
                    {"key": "password", "label": "Password", "placeholder": "(password)",
                     "input_type": "password"},
                ],
            },
        ],
    },
}
```

### 3. (Optional) Add a logo

Add an SVG to `frontend/public/logos/myprovider.svg` and register it in `frontend/src/components/ui/ProviderLogos.tsx`. If no logo is provided, the frontend uses a generic Lucide icon.

### 4. (Optional) Add a fallback icon

If the provider should work offline without an API call, add a fallback entry in `frontend/src/components/features/providers/providerConstants.ts`:

```typescript
import { MyIcon } from 'lucide-react';

export const fallbackIconByType = {
  // ...
  myprovider: MyIcon,
};
```

### That's it

The provider will automatically appear in:
- The **Setup wizard** (first-run)
- The **Add Integration** modal (Providers page)
- Provider cards, sync, drift detection, etc.

The guided wizard steps are served by the API and rendered dynamically. No frontend rebuild is needed for the provider to be functional.

---

## How to Add a Proxy Provider

Same pattern, implement `ProxyProvider` from `app/providers/base.py`:

```python
class MyProxyProvider(ProxyProvider):
    def __init__(self, url: str, username: str, password: str): ...
    def test_connection(self) -> bool: ...
    def list_hosts(self) -> list[dict]: ...
    def create_host(self, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> dict | None: ...
    def update_host(self, host_id: int, domain: str, ip: str, port: int,
                    scheme: str = "http", websocket: bool = False,
                    cert_id: int | None = None) -> bool: ...
    def delete_host(self, host_id: int) -> bool: ...
```

Then register in `_PROVIDER_REGISTRY` and `PROVIDER_TYPES` exactly as described above (use `"category": "proxy"`).

---

## How to Add an MCP Tool

1. Add the tool function in the relevant file under `vauxtra_mcp/tools/`:

```python
from vauxtra_mcp.client import get, post

@mcp.tool()
def my_tool(param: str) -> dict:
    """Short description shown to the AI assistant."""
    return get(f"/my-endpoint?param={param}")
```

2. Import and register the tool in `vauxtra_mcp/server.py`.

---

## Code Style

- **Python**: PEP 8, type hints on all public functions. Docstrings only where logic is non-obvious — no tautological comments.
- **TypeScript**: strict mode, no `any`, interfaces in `frontend/src/types/api.ts`.
- **No secrets**: never commit `.env`, private keys, or runtime data. Use `data/` and `.env` which are gitignored.

---

## Pull Request Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Frontend builds without errors (`npm run build`)
- [ ] No new `any` types introduced
- [ ] All UI text in English — no hardcoded strings in other languages
- [ ] No secrets committed (`.env` is gitignored)
- [ ] Commit messages follow Conventional Commits
- [ ] Bug reports include reproduction steps; feature proposals start as an issue
