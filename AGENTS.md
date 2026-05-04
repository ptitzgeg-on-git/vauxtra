# AGENTS

Operational guide for AI coding agents working on Vauxtra.

## Goals

- Keep production behavior stable and explicit.
- Preserve security posture and release hygiene.
- Prefer small, reviewable changes with clear validation.

## Project Map

- Backend API: `app/` (FastAPI + SQLite)
- Frontend: `frontend/` (React + TypeScript + Vite)
- MCP bridge: `vauxtra_mcp/`
- Tests: `tests/`
- CI workflows: `.github/workflows/`

## Non-Negotiable Rules

- Do not commit secrets, keys, or runtime data.
- Never disable security gates to make CI pass.
- Keep branch protection expectations intact (PR + checks + review).
- Use UTC defaults for timezone-sensitive settings.
- Keep release/version metadata coherent across docs and packaging.

## Coding Standards

- Python: run `ruff check app/ vauxtra_mcp/`
- Backend tests: `python -m pytest tests/ -v`
- Frontend checks: use existing npm scripts in `frontend/package.json`
- Keep diffs focused; do not refactor unrelated files.

## CI/CD Expectations

- Required checks are green before merge.
- Security workflow must remain blocking for high/critical vulnerabilities.
- Docker publish should keep signing (cosign) and provenance attestation.

## Release Flow

1. Merge approved PR to `main`.
2. Create semantic tag (example: `v1.0.1`).
3. Verify Build & Publish workflow completes.
4. Verify GitHub release exists for the tag.

## Local Private Notes (Not Versioned)

Use `.git/info/exclude` for machine-local AI notes and scratch files instead of adding many patterns to `.gitignore`.

Recommended local-only patterns:

- `docs/ai/`
- `*.local.md`
- `.agents/`

This keeps the public repo clean while allowing private AI working context locally.
