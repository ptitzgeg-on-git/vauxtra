## Summary

<!-- What does this PR do? Why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature / provider
- [ ] Refactor
- [ ] Documentation
- [ ] CI / tooling

## Checklist

- [ ] `make check` passes: `ruff check .`, `python -m pytest tests/`, every `scripts/check_*.py`, and in `frontend/` `npm run lint`, `npx tsc -b --force`, `npm run i18n:check`, `npm run i18n:quality`, `npm run test`
- [ ] Frontend builds without errors (`cd frontend && npm run build`)
- [ ] No new `any` types introduced in TypeScript
- [ ] All UI text in English
- [ ] No secrets committed (`.env` is gitignored)
- [ ] Public wording is product-focused (no authoring-process or tool-attribution text outside the README's "How it's built" section)
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [ ] `CHANGELOG.md` updated (for features and bug fixes)

## Testing

<!-- How did you test this? Screenshots if UI change. -->
