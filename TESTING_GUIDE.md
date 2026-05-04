# 🚀 Vauxtra UI Testing Guide

## ✅ System Status

All services running and ready:
- **Vauxtra Backend**: http://localhost:8888 (FastAPI)
- **Vauxtra Frontend**: http://localhost:5173 (React/Vite)
- **Lab Providers**: All 4 running and health-checked ✓
  - AdGuard: http://localhost:13000 (admin/adminadmin)
  - Pi-hole: http://localhost:18081 (admin/admin)
  - Technitium: http://localhost:15389 (admin/admin, zone: lab.test)
  - NPM: http://localhost:18082 (admin@example.com/admin)

## 📋 UI Testing Checklist

### Phase 1: Provider Configuration

**Objective**: Verify that Vauxtra can manage the 4 lab providers

1. **Navigate to Providers Page**
   - Open http://localhost:5173/providers
   - Should display 4 registered providers in a list
   - Each should show name, type, and URL

2. **Test Provider Connectivity**
   - Manually test each provider via dedicated page or health check
   - Look for "Connected" or status indicator
   - AdGuard, Pi-hole, Technitium, NPM should all show as operational

3. **Inspect Provider Details**
   - Click on each provider to view configuration
   - Verify URL, username are correctly displayed
   - Password should be masked/hidden for security

### Phase 2: DNS Records CRUD (AdGuard/Pi-hole/Technitium)

**Objective**: Test create, read, update, delete of DNS records through Vauxtra UI

#### AdGuard DNS Rewrite Test:
1. Open Providers → AdGuard (Lab)
2. Find "DNS Records" or "Rewrites" section
3. **CREATE**: Add new record
   - Domain: `test-vauxtra.lab.test`
   - Answer/IP: `10.20.30.40`
   - Click "Add"
4. **READ**: Verify record appears in list
5. **UPDATE**: Modify the record (change IP or domain)
6. **DELETE**: Remove the record
7. Verify all operations complete without errors

#### Pi-hole DNS Records Test:
Same as AdGuard but may use different UI labels. Pi-hole calls them "Local DNS Records".

#### Technitium DNS Records Test:
Same workflow for Technitium DNS provider.

### Phase 3: Proxy Hosts CRUD (NPM)

**Objective**: Test proxy host management through Vauxtra

1. Open Providers → NPM (Lab)
2. Find "Proxy Hosts" section
3. **CREATE**: Create new proxy host
   - Domain: `test-app.lab.test`
   - Target Host: `whoami` (the lab whoami container)
   - Target Port: `80`
   - Click "Create"
4. **READ**: Verify proxy host appears in list
5. **UPDATE**: Modify proxy host configuration
6. **DELETE**: Remove the proxy host
7. Verify all operations work smoothly

### Phase 4: Multilingual Support

**Objective**: Verify i18n functionality and auto-detection

#### 4.1 Language Auto-Detection:
1. Open browser console → check `localStorage.getItem('vauxtra_lang')`
2. If not set, browser language should be auto-detected on first load
3. Navigate to Settings → Language tab
4. Verify the flag for your system language is highlighted

#### 4.2 Language Selector Features:
1. Go to Settings → Language tab
2. **Verify UI Elements**:
   - Grid of language buttons with country flags
   - Each button shows flag emoji + language name
   - Currently selected language highlighted with primary color
3. **Search/Filter** (if implemented):
   - Check if there's a search box to filter languages
   - Try typing language name to filter options

#### 4.3 Supported Languages:
- 🇬🇧 English (en)
- 🇫🇷 Français (fr)
- 🇩🇪 Deutsch (de)
- 🇪🇸 Español (es)
- 🇧🇷 Português (pt)
- 🇳🇱 Nederlands (nl)
- 🇯🇵 日本語 (ja)
- 🇨🇳 中文 (zh)

#### 4.4 Language Switching Test:
1. Change language to French (Français)
   - UI should update immediately
   - All labels, buttons, dialogs in French
2. Perform a provider CRUD operation in French
   - Add a test DNS record while UI is in French
   - Verify success message is in French
3. Switch to another language (e.g., Spanish)
   - Verify all UI elements update
   - Toast notifications appear in new language
4. Switch back to English
   - Confirm UI returns to English
5. Refresh the page
   - Selected language should persist (from localStorage)

#### 4.5 Multilingual CRUD Test:
1. Switch to French
2. Go to Providers → AdGuard
3. Create a DNS record with a French name/label (if available)
4. Delete the record
5. Verify both operations show French success messages
6. Repeat with 2-3 different languages

### Phase 5: Edge Cases

1. **Network Error Handling**: Temporarily disconnect/block provider and verify error messages
2. **Concurrent Operations**: Try adding multiple records quickly (test race conditions)
3. **Large Domain Names**: Test DNS records with very long domain names
4. **Special Characters**: Test domain names with special characters (underscores, hyphens)
5. **Language Persistence**: Switch languages, navigate away, return to app → should remember language

## 📊 Success Criteria

✅ **Phase 1**: All 4 providers visible and configured
✅ **Phase 2**: CRUD operations work for DNS records (all 3 DNS providers)
✅ **Phase 3**: CRUD operations work for NPM proxy hosts
✅ **Phase 4**: Language selector visible, switching changes UI, languages persist
✅ **Phase 5**: App handles errors gracefully

## 🐛 Known Issues / Notes

1. **Provider Health Check**: API endpoint returns "down" status, but direct provider test passes
   - This appears to be an issue with the health check endpoint, not the providers themselves
   - Providers themselves work correctly when tested directly

2. **Initial Setup**: Setup wizard was bypassed using API script
   - Alternatively, can use full UI wizard if desired

## 📸 Screenshots / Observations

(To be filled during manual testing)

---

**Ready to test**: Navigate to http://localhost:5173 and follow the checklist above!

---

## E2E Run Report (2026-05-04, UI-only)

Scope executed end-to-end from a clean reset in Dockerized Vauxtra on `http://127.0.0.1:8888`, using browser clicks/forms only (no direct API test calls).

### What was validated

1. Full reset + first-launch wizard reached from a clean state.
2. Setup without password completed successfully.
3. Setup with password completed successfully.
4. Auth flow validated:
   - invalid password rejected with clear message,
   - valid password accepted,
   - sign-out returns to login screen.
5. Provider CRUD via UI validated in wizard and providers page:
   - add provider,
   - edit provider name,
   - delete provider.
6. Service import in setup validated:
   - provider scan detected importable services,
   - selected service imported successfully.
7. Service CRUD validated on Endpoints page:
   - create route,
   - edit route,
   - delete route.
8. Docker endpoint flow validated in setup:
   - delete last endpoint blocked with explicit error,
   - add endpoint works,
   - delete extra endpoint works.
9. Backup flow validated in Settings:
   - export without credentials works,
   - secure export with passphrase works,
   - restore from backup JSON via file picker works.

### Issues discovered during this run

1. Webhook URL validation is inconsistent:
   - adding `https://example.invalid/nope` succeeded (`Webhook added`),
   - later test on same webhook fails (`Invalid or unrecognized Apprise URL`).
2. Webhook toggle error path appears broken:
   - toggling enabled/disabled returned `Name and URL are required`.
3. Restoring a backup that contains empty `settings` returns instance to initial wizard state.
   - This may be intended for full restore semantics, but behavior should be explicitly documented in-product before confirmation.
4. Docker endpoint input accepted malformed value when user mistyped duplicated text.
   - Suggest stricter URL scheme validation before save.

### Recommended follow-up

1. Add frontend + backend validation parity tests for webhook URLs.
2. Add regression test for webhook enable/disable update payload.
3. Clarify restore semantics in UI warning (setup reset/auth impact).
4. Add strict validation for Docker endpoint URL format and supported schemes.

## Post-fix Verification Run (2026-05-04, UI-only, round 2)

Scope: full browser-only validation pass after backend fixes and container rebuild, including Settings tabs, reverse/DNS provider checks, and endpoint up/down lifecycle.

### Verified OK in this round

1. Settings tabs validated end-to-end:
   - General (WAN policy save, monitoring toggle),
   - Language (switch to French and back to English),
   - DNS Domains (add/delete),
   - Tags (add/delete),
   - Environments (add/delete),
   - Import & Sync (scan providers),
   - Backup & Restore (export plain, export with passphrase, restore from file),
   - API Keys (create/revoke),
   - How-To & API (content render),
   - System Logs (refresh/clear).
2. Previously reported backend issues now fixed in UI behavior:
   - invalid webhook URL is rejected at creation,
   - webhook enable/disable no longer fails with missing required fields,
   - restore keeps instance in configured state,
   - Docker endpoint malformed URL validation is stricter.
3. Provider reverse + DNS validation completed from Vauxtra UI:
   - Nginx Proxy Manager connected, tested, permissions validated, health 100/100.
   - AdGuard Home connected, tested, permissions validated, health 100/100.
4. Endpoint lifecycle validated with both providers selected:
   - create route (auto-push successful),
   - drift check reports no drift,
   - disable then enable works (up/down state transitions),
   - delete route returns list to empty state.

### Remaining observation

1. During disable flow, endpoint row label briefly rendered with an unexpected trailing `0` before returning to normal after re-enable/refresh.
   - Logged as low-severity UX follow-up (see UX tracker).

### Additional provider coverage (non-Cloudflare)

1. Traefik provider flow validated in Integrations:
   - create in expert mode,
   - validation passes,
   - provider links successfully.
2. Pi-hole provider flow validated:
   - create,
   - validation passes,
   - test connection and validate permissions both pass.
3. Technitium provider flow validated:
   - create,
   - validation passes,
   - test connection and validate permissions both pass.
4. Docker Host endpoint flow validated from Integrations modal:
   - endpoint add succeeds with valid host syntax.

### Extra fix applied during this retest

1. Traefik form submit gating bug fixed in frontend.
   - Before fix: Validate & Connect could stay disabled unless password was filled.
   - After fix: Traefik password remains optional as intended and submit is enabled with required fields only.

## UX Polish Verification (2026-05-04, UI-only)

Scope: browser validation of navigation and status readability refinements.

### Verified in this pass

1. Dashboard quick actions are visible and route correctly:
   - Create endpoint -> Services
   - Add integration -> Providers
   - Export backup -> Settings/Backup
   - Open settings -> Settings/General
2. Global keyboard shortcuts work in-app:
   - `g` then `d` -> Dashboard
   - `g` then `p` -> Providers
   - `g` then `s` -> Settings (General tab)
3. Settings page now displays breadcrumb context:
   - format: `System / <active tab>`
4. Integrations cards show cleaner status language:
   - operational state and numeric health score are separated to reduce repetition.
5. Settings save actions trigger a temporary visual highlight on the content panel.

## Documentation Hardening Pass (2026-05-04)

Scope: final user-facing documentation quality pass for release readiness.

### Verified in this pass

1. In-app "How-To & API" tab was removed to prevent duplicated/stale guidance.
2. README was aligned with current provider setup behavior (notably Cloudflare Tunnel and Pi-hole URL guidance).
3. Deployment guide was rewritten as a practical production runbook.
4. Troubleshooting guide was rewritten as an operator-focused incident runbook.
5. How-To guide was updated as canonical end-user reference.
