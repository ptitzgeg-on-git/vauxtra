# Getting help with Vauxtra

## Questions

Setup help, usage questions and ideas you want to discuss first go to
[GitHub Discussions](https://github.com/ptitzgeg-on-git/vauxtra/discussions).
Before asking, [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) covers the most common
problems, and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) the supported ways to run Vauxtra.

## Bugs and feature requests

Open an issue and pick the matching form:
[new issue](https://github.com/ptitzgeg-on-git/vauxtra/issues/new/choose).
The bug form asks for everything needed to reproduce the problem.

## Security vulnerabilities

Never in a public issue or discussion. Follow [SECURITY.md](SECURITY.md) and report it
privately through a
[security advisory](https://github.com/ptitzgeg-on-git/vauxtra/security/advisories/new).

## What to include

- The Vauxtra version (`GET /api/health` returns it) and how you deploy it: GHCR image,
  Compose built from source, or a source run.
- The provider types involved (NPM, Traefik, Cloudflare, AdGuard Home and so on).
- What you did, what you expected, and what happened instead, with the exact error text.
- Relevant logs, for example `docker logs vauxtra --tail 200`.

Redact API keys, tokens, passwords and cookies before posting anything. Issues and
discussions are public, and a pasted secret has to be rotated, not just edited out.
