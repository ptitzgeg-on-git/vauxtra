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
- Every request returns 401 saying the hash is no longer in the database

Checks:

1. Verify whether `APP_PASSWORD` is set in environment.
2. Verify setup state in DB if needed.

Actions:

- If `APP_PASSWORD` is set, it overrides wizard-managed password behavior.
- If locked out and no env password is intended, delete **both** rows:

```bash
sqlite3 data/vauxtra.db \
  "DELETE FROM settings WHERE key IN ('app_password_hash','auth_mode');"
```

Restart container after change.

> **Delete both, or the instance stays locked.** This page used to name
> `app_password_hash` alone, which is the one recipe that makes things worse: `auth_mode`
> is the row that records *that* this instance was protected, so with the hash gone and the
> marker still there Vauxtra refuses every single request rather than falling back to
> anonymous access. That refusal is deliberate — it is what stops a database restored from
> the wrong file from quietly opening a protected instance — but an operator who ran the
> old one-row command reached it while trying to get back in.
>
> If you are already in that state, the two rows disagree and you have two ways out: run
> the command above to reopen the wizard, or keep the protection by setting `APP_PASSWORD`
> to a `pbkdf2:`-prefixed hash and restarting. See
> [HOWTO — Forgot your password?](HOWTO.md#forgot-your-password) for both, in full.

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
- Zoraxy: management URL `http://<host>:8000`, admin credentials (empty with `-noauth`); a 403 `CSRF token invalid` means the URL is fronted by a proxy that drops cookies
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
- A disabled service is reported as still served
- A hostname answers nothing while every screen says the service is published and in sync
- A hostname still answers after the provider was cleared from the service
- A second DNS server, or a second proxy, still answers for a service it was removed from
- Two services answer for one hostname, and each undoes the other's push
- A save was refused after the providers had already been called

Checks:

1. Provider write permissions still valid.
2. Service target and domain fields valid.
3. Provider type supports writes (Traefik is read-only; Zoraxy only manages host rules, and a rule renamed in Zoraxy is reported as drift).
4. The service is enabled. A push converges the providers on the record, so pushing a **disabled** service withdraws it instead of publishing it: the primary proxy host is suspended, everything else is removed. Drift on a disabled service asks the opposite question and reports what still answers (`proxy_route_still_served`, `dns_rewrite_still_served`) rather than what is missing.
5. No provider was dropped from the service while it was published. Emptying the proxy or the DNS field in the editor, and removing a target from the multi-sync list, both withdraw that provider's route as the service saves. If the provider refuses the withdrawal the save answers with it -- `Former target: ...`, in the warning the editor shows beside *Service updated* -- and writes the same thing to the journal. Act on it then: the target row is unlinked regardless, and the columns the route would be found through are the ones just emptied, so nothing in Vauxtra can reach that route afterwards. It has to be removed on the provider itself. The same is true of any route left over from before these fixes.
6. The proxy host is not suspended. Disabling a service suspends its primary proxy host rather than deleting it, so a route can exist and answer nothing -- and a failed re-enable leaves exactly that. Drift reports it as `proxy_route_suspended`, and a push lifts the suspension as it updates the host. If a disable reports `Failed to suspend the proxy host`, the host is still there and still serving: the provider refused the call, and Vauxtra stops rather than deleting a host it was only asked to switch off. The three ways to disable -- the push, the `enabled` field on `PUT /api/services/{sid}`, and the bulk action -- all report it the same way. Fix the provider (an expired token is the usual cause) and disable again.
7. No two services carry one hostname in two spellings. An import made before this fix stored the name the way the provider spelled it, so `NAS.maison.lan` and `nas.maison.lan` were two rows the unique index on `(subdomain, domain)` could not tell apart -- and both push, and drift, under `nas.maison.lan`, because the public hostname is derived lowercased. Vauxtra says so at startup (`Duplicate service hostnames prevent the uniqueness index`) and this lists them: `SELECT lower(subdomain || '.' || domain) AS h, count(*) FROM services GROUP BY h HAVING count(*) > 1;`. Merge what you need out of the extra row, delete it, and restart so the index is created.
8. A save that answered `409` after the providers had already been called. Both write endpoints check the hostname before they touch a provider and store the row after, so a second operator saving the same hostname in between passes the same check and the unique index refuses whichever write lands second. The refusal names the service that won, and the journal carries what the refused save had already done: `<host> was published on <providers> and then refused` for a creation, `Service #<id> was reconfigured for <host> ... run a drift check` for a rename. Act on the journal line, not only on the refusal. Nothing is withdrawn on purpose -- the winning service holds that hostname on those same providers now, so a withdrawal keyed on the name would remove its records instead of the orphaned ones. After a refused creation there is no service row at all, so the providers the line names have to be cleaned by hand; after a refused rename the row still spells its old hostname while its providers answer for the new one, and a push puts the two back in step.

Actions:

1. Use dry-run push first (`/api/services/{sid}/push/dry-run`). Its `withheld` field says which of the two plans you are reading.
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
