# Open Source Hygiene Guide

This guide defines the minimum quality bar before publishing changes.

## 1. Repository Cleanliness

- Keep runtime data out of git: `data/`, `data.backup-*`, `data.reset-*`, logs, temp files.
- Keep local settings out of git: `.env`, editor/workspace files, machine-specific scripts.
- Prefer small, focused commits over mixed large diffs.

## 2. Security and Secrets

- Never commit credentials, API keys, tokens, private certificates, or `.secret_key` material.
- Use placeholder values in documentation examples.
- Redact secrets in screenshots, logs, and issue reports.

## 3. Documentation Quality

- Every operational statement must be verifiable in the current codebase.
- Remove stale references when files are renamed or deleted.
- Keep docs action-oriented: symptom -> checks -> recovery.

## 4. UI and i18n Quality

- No new hardcoded UI copy in pages/components; use i18n keys.
- Add keys for every supported locale when adding new UI text.
- Remove orphan translation keys when features are removed.

## 5. Assets and Branding

- Store project graphics under `frontend/public/` with clear names.
- Prefer `svg` for logos/icons where possible.
- Keep source files for logos (if any) in a dedicated `assets/` folder with license notes.
- Document any third-party asset license in `LICENSE` or a dedicated attribution file.

## 6. Pull Request Gate

Before merge, confirm all items:

- [ ] `git status` does not include unintended local/runtime files.
- [ ] Backend tests pass.
- [ ] Frontend build and lint pass.
- [ ] Docs and changelog are updated and consistent.
- [ ] No secrets or internal-only files are introduced.

## 7. Release Gate

- [ ] Runtime behavior validated on Docker deployment.
- [ ] README links and doc map verified.
- [ ] Migration/upgrade notes included for behavior changes.
- [ ] Changelog reflects user-visible impact.
