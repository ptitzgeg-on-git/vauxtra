# Security Policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Open a
[private security advisory](https://github.com/ptitzgeg-on-git/vauxtra/security/advisories/new)
with:

- a description of the problem and its impact
- steps to reproduce
- a suggested fix, if you have one

You should get an acknowledgement within 72 hours, then an assessment and a plan.

## What is in place

What Vauxtra stores, who the defences are aimed at and where they stop is written down in
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). In short:

- **Credentials.** Provider passwords and tokens are encrypted at rest with Fernet, under a
  key derived from `SECRET_KEY`. Notification URLs, which carry their own tokens, are encrypted the same way and masked in
  every response. Backups take the `admin` scope: a plain one contains no secrets, an encrypted one
  holds them under a passphrase you choose.
- **Admin password.** PBKDF2-HMAC-SHA256 with 600,000 iterations, 12 characters minimum.
  Changing it ends every open session.
- **API keys.** `vx_` tokens, stored as SHA-256 hashes, shown once, scoped `read`, `write` or
  `admin`.
- **Sessions.** `HttpOnly`, `SameSite=Strict`, 7 days, and `Secure` when `HTTPS_ONLY=true`.
- **Rate limits.** Sign-in: 5 a minute and 20 an hour per client address. Password setup and
  change: 3 a minute. API key creation: 10 a minute.
- **Headers.** A strict Content-Security-Policy, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and HSTS when `HTTPS_ONLY=true`.
- **CORS.** No cross-origin caller by default: the interface is served by the same
  application.
- **Open mode.** Without a password, every request is admin. The default Compose file binds
  the port to `127.0.0.1` for that reason.
- **Supply chain.** Dependencies are audited with `pip-audit` and `npm audit`, the source and
  the image are scanned with Trivy and Grype, and every image is signed with cosign and given
  a SLSA provenance attestation. See [how to verify it](docs/THREAT_MODEL.md#verify-the-image).

## Running it safely

- Set an admin password before the port is reachable from anything but the host.
- Put it behind a reverse proxy with HTTPS, set `HTTPS_ONLY=true` and `FORWARDED_ALLOW_IPS`.
- Set `SECRET_KEY` to 32 random characters or more, outside the `data/` volume if backups of
  that volume leave the host.
- Give every integration the narrowest credential its provider offers, and every API key the
  lowest scope it needs. The [threat model](docs/THREAT_MODEL.md#credentials-each-provider-needs)
  lists them per provider.
- Leave `DEBUG=false` and `CORS_ORIGINS` empty.
- Mount the Docker socket only if you use container discovery.
