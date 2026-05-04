# UX Bug Tracker

Updated: 2026-05-04

## Scope
- Setup onboarding flow
- Settings navigation and data management flows
- Frontend performance and route loading

## Fixed Today

| ID | Severity | Area | Issue | Resolution | Files |
|---|---|---|---|---|---|
| UX-001 | High | Setup | Nested components were created during render, risking unstable behavior and failing lint rules | Replaced nested component declarations with pure render helpers | frontend/src/components/features/setup/ProvidersStep.tsx |
| UX-002 | High | i18n runtime | Language effect triggered sync setState in effect and tripped strict lint rule | Switched to cancellation-safe async pattern and moved loading state to language change action | frontend/src/i18n/index.tsx |
| UX-003 | High | Routing performance | Initial bundle too large and all pages loaded eagerly | Implemented route-level lazy loading with Suspense fallback for page chunks | frontend/src/App.tsx |
| UX-004 | Medium | Build chunking | Single large app chunk warning in production build | Added vendor chunk strategy and route split compatibility for Vite 8 | frontend/vite.config.ts |
| UX-005 | Medium | Settings accessibility | Change password flow used click-only behavior and lacked password manager hints | Converted to semantic form submit with required fields and autocomplete hints | frontend/src/pages/Settings.tsx |
| UX-006 | Medium | Import review table | Checkbox onChange was no-op and row selection behavior was brittle | Added explicit toggle handler, checkbox click stopPropagation, and row-select label | frontend/src/pages/Settings.tsx |
| UX-007 | Medium | Settings + Setup i18n | Setup import flow still had hardcoded strings while i18n was available | Wired setup import screen to translation hook and added locale keys in all 8 locale files | frontend/src/components/features/setup/ImportStep.tsx; frontend/src/locales/*.json |
| UX-008 | Medium | Import table keyboard UX | Route selection was mostly mouse-oriented | Added keyboard toggling (Enter/Space), row tab focus and aria-selected state | frontend/src/pages/Settings.tsx |
| UX-009 | Low | Long list scanning | API key/webhook lists became dense at scale | Added search fields, compact mode toggles, and sticky list controls | frontend/src/pages/Settings.tsx |
| UX-010 | Low | Backup flow reassurance | Restore confirmation lacked concrete impact summary | Added pre-restore counters and enriched confirmation message with entity counts | frontend/src/pages/Settings.tsx |
| UX-011 | Medium | Full Settings i18n | Remaining DNS/tags/environments/migration and action strings were still literal English | Completed extraction to i18n keys and propagated parity across all locale files | frontend/src/pages/Settings.tsx; frontend/src/locales/*.json |
| UX-012 | Low | Row focus visibility | Keyboard row selection was functional but focus ring was subtle in dense table | Added strong focus-visible ring and offset styling on selectable migration rows | frontend/src/pages/Settings.tsx |
| UX-013 | Low | Logs ergonomics | Logs tab became hard to scan during long sessions | Added search, level filter, auto-scroll toggle, and refresh action in logs tab | frontend/src/pages/Settings.tsx |
| UX-014 | Medium | Localization reliability | Locale parity depended on manual discipline only | Added automated locale parity checker and npm quality gate script | frontend/scripts/check-locale-parity.mjs; frontend/package.json |

## Validation Results
- Lint status: PASS
- Build status: PASS
- Bundle outcome: initial route split into lazy page chunks and vendor chunks

## Performance Delta (Build)
- Previous largest JS chunk: around 591.86 kB (single main chunk)
- Current largest JS chunk: around 178.30 kB (vendor-react), with pages split around 40-55 kB each

## Recommended Next Sprint
1. Enforce `npm run quality` in CI before merge.
2. Add pseudo-localization test mode for UI overflow checks.
3. Add locale coverage checks in pull request template.

## New Findings (2026-05-04 E2E UI Run)

| ID | Severity | Area | Issue | Repro (UI-only) | Status |
|---|---|---|---|---|---|
| UX-015 | High | Settings / Webhooks | Non-Apprise URL accepted during webhook creation, then fails on test | Settings -> Webhooks -> add `https://example.invalid/nope` -> creation succeeds -> Test returns invalid URL | Fixed (2026-05-04 retest) |
| UX-016 | High | Settings / Webhooks | Toggle enabled/disabled can fail with `Name and URL are required` | Settings -> Webhooks -> click Enabled/Disabled toggle on existing webhook | Fixed (2026-05-04 retest) |
| UX-017 | Medium | Backup / Restore UX | Restore with empty `settings` resets setup flow and returns to first-launch wizard | Settings -> Backup & Restore -> restore backup containing empty settings list | Fixed (2026-05-04 retest) |
| UX-018 | Medium | Setup / Docker endpoints | Malformed Docker endpoint URL can be saved from setup form | Setup -> Docker -> mistype/duplicate URL text -> Add Docker endpoint accepts invalid format | Fixed (2026-05-04 retest) |
| UX-019 | Low | Endpoints table rendering | Endpoint name briefly shows unexpected trailing `0` after disable action before stabilizing | Services -> disable route -> observe temporary row label mismatch (`appe2e0`) | Open |
| UX-020 | Medium | Integrations / Traefik | Traefik submit button incorrectly required password although field is optional | Integrations -> Add -> Traefik -> expert mode -> fill URL only -> Validate & Connect stayed disabled | Fixed (2026-05-04 retest) |

## Regression Notes (2026-05-04)

- Setup wizard path works in both modes (open access and password-protected).
- Auth errors are surfaced correctly on invalid login.
- Provider CRUD and endpoint CRUD flows remain operational in browser testing.
- Backup export/import flows functionally complete, with caveat tracked in UX-017.

## Regression Notes (2026-05-04, post-fix retest)

- All Settings tabs were revalidated with browser-only interactions.
- Reverse and DNS integrations were linked from UI and validated healthy (NPM + AdGuard).
- Endpoint create/push/drift-check/disable/enable/delete lifecycle completed without functional failures.
- Prior UX-015/016/017/018 issues are no longer reproducible after backend fixes.

## Additional Provider Retest (2026-05-04)

- Traefik, Pi-hole, and Technitium were added and validated in browser-only mode.
- Pi-hole and Technitium test/permission actions pass from Integrations cards.
- Traefik optional-password gating mismatch was fixed in frontend submit validation (UX-020).

## UX Polish Pass (2026-05-04, browser-validated)

| ID | Severity | Area | Change | Status |
|---|---|---|---|---|
| UX-021 | Low | Dashboard | Added quick actions block (create endpoint, add integration, export backup, open settings) with shortcut hint | Implemented + validated |
| UX-022 | Low | Global navigation | Added keyboard shortcuts `g` then `d/p/s` for Dashboard/Providers/Settings | Implemented + validated |
| UX-023 | Low | Settings | Added breadcrumb context (`System / <tab>`) under Settings title | Implemented + validated |
| UX-024 | Low | Integrations status language | Unified provider state wording to `Active/Degraded/Error/Disabled` and separated score display | Implemented + validated |
| UX-025 | Low | Feedback UX | Added temporary highlight after successful save actions in Settings | Implemented |
| UX-026 | Low | Settings information architecture | Removed in-app "How-To & API" tab to avoid stale duplicate guidance; consolidated end-user docs in repo markdown files | Implemented + validated |
