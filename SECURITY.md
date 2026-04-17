# Security Policy

## Supported Versions

Only the latest release on the `main` branch is actively maintained and receives security patches.

## Reporting a Vulnerability

Please **do not** open a public issue for sensitive vulnerabilities.

Contact the maintainer via GitHub private message with:
- Description of the problem
- Estimated impact
- Steps to reproduce
- Suggested mitigation (if any)

An acknowledgement is expected within 72 hours, followed by an assessment and remediation plan.

## Project Security Practices

### Credentials & Secrets
- Never commit `.env` files or private keys.
- Provider credentials are encrypted at rest using Fernet symmetric encryption.
- JSON backups do not export provider passwords.
- Admin password is hashed with PBKDF2-HMAC-SHA256 (600k iterations) when configured via the Setup wizard.

### Authentication
- **Password authentication**: Set during initial Setup wizard or via `APP_PASSWORD` env var.
- **API keys**: Bearer tokens with `vx_` prefix, SHA-256 hashed before storage.
- **Session cookies**: HttpOnly, SameSite=Strict, 7-day expiration.
- No password = open access (intended for trusted local networks only).

### Rate Limiting
- Login endpoint: 5 requests/minute per IP
- Password setup: 3 requests/minute per IP
- General API: No hard limit (homelab use case)

### CORS Policy
- Default: `http://localhost:5173,http://127.0.0.1:5173,http://localhost:8888`
- Configurable via `CORS_ORIGINS` environment variable
- In production, restrict to your actual domain(s)

### Security Headers
The application sets these HTTP headers on all responses:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`

### Production Recommendations
- Set a strong `APP_PASSWORD` or configure via Setup wizard
- Set `SECRET_KEY` to a random 32+ character string
- Restrict `CORS_ORIGINS` to your domain
- Run behind a reverse proxy with HTTPS
- Avoid `DEBUG=true` in production
