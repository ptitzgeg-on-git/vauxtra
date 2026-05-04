# Vauxtra Troubleshooting & FAQ

## 🔧 Common Issues & Solutions

### **Provider Connection Issues**

#### ❌ "Provider not reachable"
**Symptoms**: `test_connection()` fails, red status indicator

**Causes**:
- Provider service not running
- Network connectivity issue
- Firewall blocking port
- Wrong credentials/URL

**Solutions**:
```bash
# 1. Check provider is running
docker ps | grep -E "adguard|pihole|technitium|npm|traefik"

# 2. Test network connectivity
ping <provider_ip>
curl -I http://<provider_ip>:<port>

# 3. Check firewall
sudo ufw status
sudo iptables -L -n

# 4. Verify URL in Vauxtra
# UI: Providers page → provider details → Connection Test

# 5. Check provider logs
docker logs vauxtra-adguard
docker logs vauxtra-npm
```

#### ❌ "Authentication failed"
**Symptoms**: Provider marked as offline after initial connection test

**Causes**:
- Wrong password/API key
- Credentials changed in provider
- Token expired (Technitium)
- API rate limiting

**Solutions**:
```bash
# 1. Re-enter credentials in UI
# Providers → Edit → Re-enter password → Save

# 2. Check provider authentication logs
# AdGuard: Settings → Logs
# Pi-hole: UI → Query Log
# NPM: Admin dashboard → System

# 3. Test manually
curl -u admin:password http://adguard:3000/api/settings

# 4. Reset provider password
# Most providers have admin password reset (see provider docs)
```

#### ❌ "DNS record not created"
**Symptoms**: Service created but DNS rewrite missing from provider

**Causes**:
- DNS provider offline during sync
- Duplicate record (already exists)
- Domain invalid for provider
- Zone not configured in provider

**Solutions**:
```bash
# 1. Check provider DNS zones
# AdGuard: Settings → DNS Rewrites
# Pi-hole: Admin → Adlist
# Technitium: Dashboard → Zones

# 2. Manually sync
# Services page → [service] → Sync

# 3. Check logs for error details
# Settings → Logs → Search for service name

# 4. Verify domain format
# Domain must be valid: app.example.com
# Not: app/example or ://example
```

---

### **Service & Routing Issues**

#### ❌ "Route already exists"
**Symptoms**: 409 Conflict error on service creation

**Causes**:
- Another service has same FQDN
- Duplicate in proxy provider
- Subdomain/domain combination collision

**Solutions**:
```bash
# 1. Check existing services
# Services page → Search by domain

# 2. Delete conflicting service (if safe)
# Or rename subdomain

# 3. Check proxy provider for duplicate host
# NPM: Proxy Hosts → Find host
# Traefik: API → /api/http/routers
```

#### ❌ "Service unreachable (504 Gateway)"
**Symptoms**: Service created but target returns 504

**Causes**:
- Target IP/port wrong
- Target service not running
- Firewall blocking access
- Proxy misconfigured

**Solutions**:
```bash
# 1. Test target directly
curl http://<target_ip>:<port>

# 2. Check proxy logs
docker logs vauxtra-npm
docker logs vauxtra-traefik

# 3. Verify proxy rule
# NPM: Proxy Hosts → [host] → Details
# Traefik: API → /api/http/routers/[router]

# 4. Check service status
docker ps | grep <service_name>
```

#### ❌ "SSL certificate error (HTTPS)"
**Symptoms**: Browser warning "not secure" or `NET::ERR_CERT_INVALID`

**Causes**:
- Certificate not issued
- Certificate expired
- Wrong domain
- Cert not provisioned in proxy

**Solutions**:
```bash
# 1. Check certificate status
# Services page → [service] → Certificate

# 2. Force certificate renewal
# Services → [service] → Actions → Renew Certificate

# 3. Check proxy provider certificates
# NPM: SSL Certificates → [cert] → Details
# Traefik: /api/tls/certificates

# 4. Verify domain DNS resolves
dig app.example.com
nslookup app.example.com
```

---

### **Database & Storage Issues**

#### ❌ "Database is locked"
**Symptoms**: API requests timeout or fail with "database is locked"

**Causes**:
- SQLite concurrent write access
- Long-running query
- Corrupted database
- Disk full

**Solutions**:
```bash
# 1. Check disk space
df -h /app/data

# 2. Verify database integrity
sqlite3 /app/data/vauxtra.db "PRAGMA integrity_check;"

# 3. Restart Vauxtra to clear locks
docker restart vauxtra

# 4. Check for long-running operations
# Settings → Logs → Look for slow queries

# 5. Enable WAL mode (better concurrency)
sqlite3 /app/data/vauxtra.db "PRAGMA journal_mode=WAL;"
```

#### ❌ "Backup failed"
**Symptoms**: Backup button fails or backup file is empty

**Causes**:
- Insufficient disk space
- Permission denied on backup dir
- Database locked during backup
- Invalid backup path

**Solutions**:
```bash
# 1. Check disk space
df -h /app/backups

# 2. Verify permissions
ls -la /app/backups
chmod 755 /app/backups

# 3. Manual backup
docker exec vauxtra sqlite3 /app/data/vauxtra.db .dump > /backups/vauxtra_manual.sql

# 4. Restore backup
docker exec vauxtra sqlite3 /app/data/vauxtra.db < /backups/vauxtra_manual.sql
```

---

### **API & Integration Issues**

#### ❌ "CORS error" (browser)
**Symptoms**: Browser shows "has been blocked by CORS policy"

**Causes**:
- Frontend origin not in CORS_ORIGINS
- Credentials issue
- Missing headers

**Solutions**:
```bash
# 1. Check current CORS config
echo $CORS_ORIGINS

# 2. Update CORS in docker-compose or .env
CORS_ORIGINS=http://localhost:5173,http://example.com

# 3. Test CORS header
curl -H "Origin: http://localhost:5173" http://localhost:8888/api/health -v

# 4. Restart Vauxtra
docker restart vauxtra
```

#### ❌ "API key authentication failed"
**Symptoms**: 401 Unauthorized with valid API key

**Causes**:
- API key revoked
- Wrong API key format
- Scope insufficient
- API key expired

**Solutions**:
```bash
# 1. Verify API key in header
# Must be: Authorization: Bearer <key>

# 2. Check API key status
# Settings → API Keys → [key] → Status

# 3. Regenerate API key if needed
# Settings → API Keys → [key] → Regenerate

# 4. Check scope
# Settings → API Keys → [key] → Scopes
```

#### ❌ "Webhook not firing"
**Symptoms**: Webhook configured but never called

**Causes**:
- Webhook endpoint down
- Network connectivity issue
- Webhook disabled
- URL validation failed

**Solutions**:
```bash
# 1. Test webhook endpoint manually
curl -X POST http://your-webhook-endpoint \
  -H "Content-Type: application/json" \
  -d '{"event": "test", "timestamp": "2026-05-02T22:00:00Z"}'

# 2. Check webhook status
# Settings → Webhooks → [webhook] → Status

# 3. Verify URL is accessible
# Vauxtra must reach: https://your-domain.com/webhook

# 4. Check webhook logs
# Settings → Logs → Filter by "webhook"

# 5. Re-enable webhook
# Settings → Webhooks → [webhook] → Enable
```

---

### **Performance Issues**

#### ❌ "Vauxtra is slow"
**Symptoms**: API requests take >5s, UI is laggy

**Causes**:
- Provider calls timing out
- Database queries slow
- Too many services
- High CPU/memory usage

**Solutions**:
```bash
# 1. Check system resources
docker stats vauxtra

# 2. Monitor provider response times
# Settings → Logs → Search provider errors

# 3. Optimize database
sqlite3 /app/data/vauxtra.db "VACUUM;"

# 4. Check provider load
# Provider UI: Check CPU, memory, connections

# 5. Increase timeouts (if services are many)
# Edit app/config.py: PROVIDER_TIMEOUT = 30  # seconds
```

#### ❌ "Memory leak"
**Symptoms**: Vauxtra memory usage increases over time

**Causes**:
- Circular references in Python
- Cache not clearing
- Provider connections not closed
- WebSocket connections leaking

**Solutions**:
```bash
# 1. Monitor memory growth
docker stats vauxtra --no-stream | watch -n 5

# 2. Restart Vauxtra periodically
# Via cron: 0 3 * * * docker restart vauxtra

# 3. Check for leaks in app/cache.py
# Ensure RequestCache is cleared after request

# 4. Review provider connection pooling
# Check app/providers/*.py for resource cleanup
```

---

## ❓ FAQ

### **General**

**Q: Can I use Vauxtra with Kubernetes?**  
A: Yes! See [SECURITY_DEPLOYMENT.md](./SECURITY_DEPLOYMENT.md#kubernetes-deployment) for K8s manifests.

**Q: Does Vauxtra support IPv6?**  
A: Partial. Providers support it (check their docs), but Vauxtra UI still targets IPv4. IPv6 support coming in v2.

**Q: Can I backup to cloud storage (S3, GCS)?**  
A: Yes! Use your provider's CLI in the backup script. Example in `SECURITY_DEPLOYMENT.md`.

**Q: How often should I backup?**  
A: Daily minimum, hourly in production. Test recovery monthly.

---

### **Providers**

**Q: Which DNS provider should I choose?**  
A: 
- **AdGuard**: Best for home labs, easy setup
- **Pi-hole**: Most popular, good performance
- **Technitium**: Lightweight, great for edge devices

**Q: Can I use multiple DNS providers?**  
A: Yes! Create multiple provider instances and assign to different services.

**Q: What if my DNS provider goes down?**  
A: Configure a failover provider. Vauxtra will skip sync if provider is offline (no data loss).

**Q: Does Vauxtra sync to Cloudflare?**  
A: Yes! Via Cloudflare API (for zones) or Cloudflare Tunnel (for proxying).

---

### **Services & Routing**

**Q: Can I use wildcard domains?**  
A: Partially. Proxy providers (NPM, Traefik) support `*.example.com`, but DNS must target main domain first.

**Q: What's the difference between "DNS", "Proxy+DNS", and "Tunnel" modes?**  
A:
- **DNS**: Only A record, no HTTP routing (use for internal services)
- **Proxy+DNS**: DNS A record + HTTP proxy host (typical setup)
- **Tunnel**: Via Cloudflare Tunnel (no local proxy needed)

**Q: Can I change a service's provider after creation?**  
A: Not directly. Delete and recreate with new provider (configs persist in backup).

**Q: How many services can Vauxtra handle?**  
A: Tested up to 1000 services. Performance degrades >5000 (needs pagination).

---

### **Security**

**Q: Is my provider password encrypted?**  
A: Yes, stored encrypted in SQLite. Use strong APP_PASSWORD to protect.

**Q: Can I disable the web UI and use API only?**  
A: Yes, remove frontend static mount in Docker.

**Q: How do I rotate API keys?**  
A: Settings → API Keys → [key] → Regenerate. Old key still works for 24h (grace period).

**Q: Should I enable HTTPS_ONLY?**  
A: Yes, always in production. Set `HTTPS_ONLY=true` in `.env`.

---

### **Troubleshooting**

**Q: Where are the logs?**  
A: 
- Docker: `docker logs vauxtra`
- File: `/app/data/logs/vauxtra.log`
- UI: Settings → Logs

**Q: How do I debug a sync failure?**  
A: Check logs for error, verify provider health, check network connectivity.

**Q: Can I manually edit the database?**  
A: No, use the API. Direct edits risk corruption.

**Q: How do I recover from a failed import?**  
A: Use the backup from before import: restore and retry with fixed import file.

---

## 📞 Getting Help

1. **Check logs**: `docker logs vauxtra | tail -100`
2. **Review status**: UI → Settings → Logs (last 24h)
3. **Test provider**: Services → [service] → Providers → Test
4. **Consult docs**: [ARCHITECTURE.md](./ARCHITECTURE.md), [SECURITY_DEPLOYMENT.md](./SECURITY_DEPLOYMENT.md)
5. **Community**: GitHub Issues, Discussions

---

**Last Updated**: May 2, 2026
