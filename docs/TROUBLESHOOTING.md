# Vauxtra Troubleshooting

This runbook focuses on common real-world failures and fast recovery steps.

## 1. UI Not Loading

Symptoms:

- Browser cannot open Vauxtra
- Blank page or perpetual loading

Checks:

```bash
docker ps | findstr vauxtra
docker logs vauxtra --tail 120
```

Actions:

1. Confirm container is running and bound to `:8888`.
2. If build was local, rebuild:

```bash
docker compose up -d --build vauxtra
```

3. Hard refresh browser cache (`Ctrl+F5`).

## 2. Login or Password Problems

Symptoms:

- Login fails despite expected password
- Setup wizard reappears unexpectedly

Checks:

1. Verify whether `APP_PASSWORD` is set in environment.
2. Verify setup state in DB if needed.

Actions:

- If `APP_PASSWORD` is set, it overrides wizard-managed password behavior.
- If locked out and no env password is intended:

```bash
sqlite3 data/vauxtra.db "DELETE FROM settings WHERE key='app_password_hash';"
```

Restart container after change.

## 3. Provider Test Fails

Symptoms:

- Integrations card shows failed test/validation

Checks:

1. Reachability from Vauxtra runtime to provider URL.
2. Correct credentials/token.
3. Correct URL format for provider type.

Provider URL notes:

- NPM: `http://<host>:81` (or mapped host port)
- Traefik API: `http://<host>:8080`
- Pi-hole: base URL only (`http://pihole`), not `/admin`
- AdGuard: web/API URL (commonly `:3000`)

Actions:

- Re-save provider with corrected URL/credentials.
- Run both "Test connection" and "Validate permissions".

## 4. Docker Discovery Empty

Symptoms:

- No containers returned in discovery

Checks:

1. Docker endpoint exists and tests successfully.
2. Socket or remote endpoint is reachable from Vauxtra.
3. Containers are running on selected endpoint.

Actions:

- For local socket, ensure mount exists: `/var/run/docker.sock:/var/run/docker.sock:ro`
- For remote host, verify `tcp://` or `ssh://` URL correctness.

## 5. Push/Reconcile or Drift Issues

Symptoms:

- Push fails
- Drift always reported

Checks:

1. Provider write permissions still valid.
2. Service target and domain fields valid.
3. Provider type supports writes (Traefik is read-only).

Actions:

1. Use dry-run push first (`/api/services/{sid}/push/dry-run`).
2. Inspect logs for precise provider-side error.
3. Reconcile only after validation succeeds.

## 6. Backup/Restore Problems

Symptoms:

- Restore fails
- Providers appear but credentials fail after restore

Checks:

1. Backup file integrity and format.
2. Presence of matching `data/.secret_key` for encrypted credentials.

Actions:

- If credentials cannot decrypt post-restore, re-enter provider secrets or restore matching `.secret_key`.
- Keep DB and `.secret_key` backed up together.

## 7. API Docs Missing

Symptom:

- `/api/docs` returns 404

Cause:

- `DEBUG=false` (expected in production)

Action:

- Enable only for temporary inspection in non-production:

```bash
DEBUG=true
```

Then restart service.

## 8. Quick Diagnostics Commands

```bash
# Container status
docker ps | findstr vauxtra

# Recent logs
docker logs vauxtra --tail 200

# Health endpoint
curl http://127.0.0.1:8888/api/health

# Services list (requires API key)
curl -H "Authorization: Bearer <API_KEY>" http://127.0.0.1:8888/api/services
```

## 9. When to Escalate

Escalate to issue/maintainer with:

1. Vauxtra version
2. Deployment mode (GHCR image or source build)
3. Exact failing action
4. Relevant logs (redact secrets)
5. Provider type and URL pattern (without secret)

GitHub issues: [https://github.com/ptitzgeg-on-git/vauxtra/issues](https://github.com/ptitzgeg-on-git/vauxtra/issues)
