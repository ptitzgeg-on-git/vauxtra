# Vauxtra Architecture & Design

## 📐 System Overview

**Vauxtra** is a unified control plane for managing DNS and reverse proxy configurations across multiple providers in a single infrastructure. It abstracts provider-specific APIs and provides a unified REST API + Web UI for managing:

- **DNS Rewrites** (A records, CNAME, etc.) via AdGuard, Pi-hole, Technitium
- **Reverse Proxies** (HTTP/HTTPS routing) via NPM, Traefik, Cloudflare Tunnel
- **Service Routing** (subdomain → target backend mapping)
- **SSL/TLS Certificates** (automatic issuance, renewal)
- **Webhooks & Sync** (push configuration changes to providers)

---

## 🏗️ Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│                   Web UI (React 19)                     │
│          (Setup Wizard, Services, Providers)            │
├─────────────────────────────────────────────────────────┤
│          FastAPI REST API (Python 3.13)                 │
├──────────────────┬──────────────────┬──────────────────┤
│  Auth & Sessions │  Logging & Sync  │   WebHooks       │
├──────────────────┴──────────────────┴──────────────────┤
│         SQLite Database (app/models.py)                 │
│   (Providers, Services, DNS Records, Certificates)     │
├─────────────────────────────────────────────────────────┤
│           Provider Abstraction Layer                    │
│  ┌─────────────┬─────────────┬──────────────┬──────┐   │
│  │ DNSProvider │ ProxyProvider│ CloudflareAPI│Factory│   │
│  └──────┬──────┴──────┬───────┴──────────────┴──┬───┘   │
├─────────────────────────────────────────────────┼───────┤
│  AdGuard│Pi-hole│Technitium │ NPM  │ Traefik  │ CF    │
│  (HTTP) │ (API) │   (API)   │ (API)│ (HTTP)   │(API)  │
└─────────┴────────┴───────────┴─────┴──────────┴──────┘
```

### **Layer 1: Frontend (React 19 + TypeScript)**
- **Setup Wizard** — Provider onboarding, credentials collection
- **Services Page** — Create/manage/delete service routes
- **Providers Page** — Configure DNS/Proxy provider connections
- **Settings** — Global config, webhooks, API keys, tags, environments
- **Backup/Migration** — Export/import service configs

**Key Components:**
- `useProviderMutations()` — Centralized provider CRUD
- `useDockerDiscovery()` — Auto-discovery of Docker services
- Provider categorization (DNS vs Proxy/Tunnel)

### **Layer 2: FastAPI REST API (Python 3.13)**

**Routers** (in `app/api/`):
- `providers.py` — CRUD for providers, test connections
- `services.py` — CRUD for service routes, preflight validation
- `sync.py` — Sync configs to providers, conflict resolution
- `webhooks.py` — Inbound provider notifications
- `certificates.py` — SSL/TLS management
- `environments.py` — Environment variables (prod/staging/etc)
- `tags.py` — Metadata tagging for services
- `settings.py` — Global settings
- `docker.py` — Docker API integration (service discovery)
- `backup.py` — Export/import
- `health.py` — Status monitoring

**Middleware & Security:**
- `CORSMiddleware` — Origin validation (see `app/security.py`)
- `SessionMiddleware` — HTTP session management
- `security_headers()` — XSS/clickjacking/HSTS protection
- `request_cache_middleware` — Per-request cache (avoid N+1 queries)
- Rate limiting (`app/limiter.py`)

### **Layer 3: Provider Abstraction**

**Abstract Base Classes** (in `app/providers/base.py`):

```python
class DNSProvider(ABC):
    """Interface for DNS providers."""
    def test_connection() → bool
    def list_rewrites() → list[dict]  # [{'domain': 'app.local', 'ip': '192.168.1.10'}]
    def add_rewrite(domain: str, ip: str) → bool
    def delete_rewrite(domain: str, ip: str) → bool
    def update_rewrite(old_domain, old_ip, new_domain, new_ip) → bool

class ProxyProvider(ABC):
    """Interface for reverse proxies."""
    def test_connection() → bool
    def list_hosts() → list[dict]  # [{'id': ..., 'domain': ..., 'forward_host': ...}]
    def create_host(...) → dict | None
    def update_host(host_id, ...) → bool
    def delete_host(host_id) → bool
```

**Concrete Implementations** (in `app/providers/`):
- `adguard.py` — AdGuard Home HTTP API
- `pihole.py` — Pi-hole REST API
- `technitium.py` — Technitium DNS Server API
- `npm.py` — Nginx Proxy Manager API
- `traefik.py` — Traefik API (docker-compose integration)
- `cloudflare.py` — Cloudflare Zones API
- `cloudflare_tunnel.py` — Cloudflare Tunnel (Zero Trust)

**Factory Pattern:**
```python
# app/providers/factory.py
provider = create_provider(provider_row)  # Returns DNSProvider | ProxyProvider instance
```

### **Layer 4: Database (SQLite)**

**Tables** (in `app/models.py`):
- `providers` — Provider connections (url, credentials, type, enabled)
- `services` — Service routes (subdomain, domain, target_ip, target_port, etc)
- `dns_records` — Cached DNS records (domain, ip)
- `certificates` — SSL/TLS certs (domain, provider, renew_at)
- `environments` — Named environment sets (prod/staging/dev)
- `tags` — Service metadata tags
- `settings` — Global settings (check_interval, app_password, etc)
- `logs` — Operation audit trail (action, service_id, status, timestamp)
- `webhooks` — Provider webhooks (url, events, active)
- `api_keys` — API key credentials (key, scopes, last_used)

**Key Indexes:**
- `providers.type` — Fast provider lookups
- `services.domain` — Service FQDN lookups
- `services.tunnel_provider_id` — FK index
- `logs.service_id, logs.timestamp` — Audit trail queries

---

## 🔄 Request Flow: Service Creation

```
1. User submits service creation form
   ├─ Frontend validates input (subdomain, domain, target, providers)
   ├─ Calls POST /api/services
   │
2. Backend preflight validation
   ├─ Check target reachability (TCP connect)
   ├─ Check DNS provider availability
   ├─ Check reverse proxy availability
   ├─ Verify no FQDN conflicts
   ├─ If proxy mode=tunnel: check tunnel provider
   │
3. Create service in database
   ├─ Insert into services table
   ├─ Generate service ID
   │
4. Sync to providers
   ├─ If mode=dns: add DNS rewrite to DNS provider
   ├─ If mode=proxy_dns: add DNS rewrite + proxy host
   ├─ If mode=proxy_tunnel: add host to tunnel provider
   │
5. Webhook notifications
   ├─ Call registered webhooks (event=service_created)
   │
6. Return service details to frontend
   ├─ Display in Services list
   ├─ Show sync status (success/partial/failed)
```

**Error Handling:**
- DNS provider offline? → Return 503, don't create service
- Proxy provider offline? → Create service, mark proxy_sync=false
- Conflict detected? → Return 409 Conflict
- Target unreachable? → Warning (not blocking)

---

## 🔐 Security Architecture

### **Authentication & Authorization**
- **Session-based** for web UI (HTTP cookies)
- **API keys** for programmatic access (header: Authorization: Bearer <key>)
- **Scope-based access** (read, write, admin)
- **Password protection** for app/admin panel

### **Secret Management**
- Provider credentials encrypted at rest (SQLite with `app/db.py` serialization)
- Avoid plaintext passwords in environment
- Rotate API keys regularly

### **CORS Validation**
- Strict origin validation (see `app/security.validate_cors_origins()`)
- No wildcard origins allowed
- Port-specific constraints

### **Protection Against Attacks**
- **CSRF**: SessionMiddleware with HttpOnly cookies
- **XSS**: Content-Security-Policy headers, HTML escaping
- **Clickjacking**: X-Frame-Options: DENY
- **SQL Injection**: Parameterized queries (sqlite3)
- **Domain Injection**: Sanitization in `app/security.sanitize_domain()`

---

## ⚡ Performance Optimization

### **Request-Scoped Caching**
- Per-request cache in `app/cache.py`
- Caches provider connections, health checks
- Prevents N+1 queries within single request
- Auto-cleared after response sent

### **Database Optimization**
- Indexes on foreign keys (`tunnel_provider_id`, `dns_provider_id`)
- Indexes on frequently queried columns (`services.domain`, `providers.type`)
- Connection pooling (sqlite3 pooling in `app/db.py`)

### **Provider Operation Optimization**
- `test_connection()` cached during preflight
- `list_hosts()` / `list_rewrites()` batched in single request
- Async I/O for concurrent provider calls (FastAPI async)

### **Frontend Optimization**
- React Query caching (5-minute stale-while-revalidate)
- Lazy loading of provider configs
- Virtual scrolling for large service lists

---

## 🔄 Provider Sync Strategy

### **Sync Triggers**
1. **Service Creation** — Add DNS record + proxy host
2. **Service Update** — Update DNS record + proxy host
3. **Service Deletion** — Remove DNS record + proxy host
4. **Manual Sync** — Force resync via API/UI

### **Conflict Resolution**
- **DNS conflicts**: Return error (can't have duplicate A records)
- **Proxy conflicts**: Return error (can't have duplicate domain routes)
- **Partial failures**: Create service, mark sync=partial, log errors

### **Webhook Notifications**
```python
# After successful sync:
notify_webhooks(
    event="service_created",
    service_id=123,
    payload={...}
)
```

---

## 🐳 Docker Integration

### **Service Discovery**
- Scans Docker daemon via `docker.py`
- Extracts service metadata (name, image, ports, labels)
- Suggests service routes to user in setup wizard

### **Test Environment** (vauxtra-dev/)
- `docker-compose.integration-test.yml` — 5 provider services + Vauxtra
- `integration_test.py` — Validates provider CRUD operations
- `docker-compose-test.sh` — Bootstrap & health checks

---

## 📋 Error Handling Strategy

### **Unified Error Codes** (app/errors.py)
```
UNAUTHORIZED, FORBIDDEN, INVALID_INPUT, NOT_FOUND, ALREADY_EXISTS, CONFLICT,
PROVIDER_UNAVAILABLE, PROVIDER_AUTH_FAILED, PROVIDER_OPERATION_FAILED,
INTERNAL_ERROR, SERVICE_UNAVAILABLE
```

### **Provider Error Wrapper**
```python
safe_provider_call(
    provider_type="adguard",
    operation="add_rewrite",
    fn=provider.add_rewrite,
    domain="app.local",
    ip="192.168.1.10"
)
```

---

## 📊 Deployment Architecture

### **Production**
```
Vauxtra Container (Docker)
├─ FastAPI app (uvicorn, 4 workers)
├─ SQLite database (/app/data/vauxtra.db)
├─ Static frontend (/app/frontend/dist)
└─ Docker socket mount (for discovery)

DNS Provider (external)
├─ AdGuard, Pi-hole, or Technitium

Reverse Proxy (external)
├─ NPM, Traefik, or Cloudflare

Load Balancer (optional)
├─ Routes traffic to Vauxtra UI + API
```

### **Development** (vauxtra-dev/)
- Multi-container docker-compose
- All 5 providers running locally
- Integration tests validate real provider connections

---

## 🧪 Testing Strategy

### **Unit Tests** (tests/test_*.py)
- Provider implementations (Technitium mocked HTTP)
- API endpoints (database isolated with tempfiles)
- Validators (domain/IP/port validation)

### **Integration Tests** (vauxtra-dev/integration_test.py)
- Real provider connections (requires docker-compose up)
- CRUD operations: add/list/delete DNS records
- Proxy host listing and creation

### **E2E Coverage (Planned Scope)**
- Full service creation workflow
- Import/export functionality
- Webhook notifications
- Multi-provider sync

---

## 🚀 Future Enhancements

1. **Background Task Queue** — Async sync operations (Celery/RQ)
2. **Request ID Tracking** — Correlation IDs for debugging
3. **API Versioning** — v1/v2 endpoints for breaking changes
4. **Advanced Caching** — Redis support for multi-instance deployments
5. **Metrics & Observability** — Prometheus metrics, distributed tracing
6. **Provider Marketplace** — Plugin architecture for custom providers
7. **Advanced Sync** — Bidirectional sync, conflict detection/merge

---

## 🔗 Key Files Reference

| Module | Purpose |
|--------|---------|
| `app/main.py` | FastAPI app initialization, middleware, routers |
| `app/models.py` | SQLite schema, ORM helpers, database utilities |
| `app/providers/base.py` | Abstract provider interfaces |
| `app/providers/factory.py` | Provider factory pattern |
| `app/security.py` | CORS validation, domain sanitization |
| `app/cache.py` | Request-scoped cache (N+1 prevention) |
| `app/errors.py` | Unified error handling & codes |
| `app/api/services.py` | Service CRUD & preflight validation |
| `app/api/sync.py` | Provider sync orchestration |
| `frontend/src/pages/Services.tsx` | Main service management UI |
| `frontend/src/components/setup/` | Setup wizard components |

---

**Last Updated**: May 2, 2026  
**Status**: Production-Ready (with ongoing hardening)
