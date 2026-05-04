# Vauxtra Security & Deployment Guide

## 🔐 Security Hardening Checklist

### **Before Production Deployment**

#### Authentication & Authorization
- [ ] Set strong `APP_PASSWORD` in `.env` (minimum 16 characters, mixed case + digits + special)
- [ ] Generate strong `SECRET_KEY` (32+ random characters) — use `$(python -c 'import secrets; print(secrets.token_urlsafe(32))')`
- [ ] Rotate API keys monthly in production
- [ ] Implement API key scope validation on all endpoints
- [ ] Enable HTTPS only (`HTTPS_ONLY=true`)

#### CORS & Network
- [ ] Validate `CORS_ORIGINS` — only allow known frontend domains
- [ ] Never use wildcard CORS (`*`)
- [ ] Use environment-specific CORS origins (prod/staging/dev)
- [ ] Restrict Docker socket access (read-only mount)
- [ ] Enable firewall rules (UFW/iptables) to limit API access

#### Secrets Management
- [ ] Never commit `.env` files to Git
- [ ] Use secrets manager (AWS Secrets Manager, HashiCorp Vault)
- [ ] Encrypt provider credentials at rest
- [ ] Rotate database encryption keys annually
- [ ] Store SSL certificates securely (not in Git)

#### Database Security
- [ ] Enable SQLite WAL (Write-Ahead Logging) mode
- [ ] Set restrictive file permissions (600: rw-------)
- [ ] Regular backups (daily, tested recovery)
- [ ] Backup encryption (AES-256)
- [ ] Separate read-only DB replicas for exports

#### API Security
- [ ] Enable rate limiting (default: 100 req/min)
- [ ] Implement request ID tracking for audit trails
- [ ] Validate all input (domain names, IPs, ports)
- [ ] Use Content-Security-Policy headers
- [ ] Implement CSRF protection (already enabled via SessionMiddleware)
- [ ] Add API versioning for backward compatibility

#### Infrastructure
- [ ] Run Vauxtra in container with non-root user (appuser:appuser)
- [ ] Use read-only root filesystem where possible
- [ ] Network policies: isolate DNS/proxy provider networks
- [ ] Monitor logs for suspicious activity
- [ ] Implement centralized logging (ELK, Splunk, CloudWatch)

#### Monitoring & Alerting
- [ ] Set up alerts for provider connection failures
- [ ] Monitor database disk usage
- [ ] Track API error rates
- [ ] Alert on unauthorized access attempts
- [ ] Daily security audit logs review

---

## 🚀 Deployment Configurations

### **Local Development**
```bash
# Start locally with providers
cd vauxtra-dev
bash docker-compose-test.sh
bash integration-test.sh
```

### **Single-Instance Docker (Staging)**
```dockerfile
# docker-compose.yml
services:
  vauxtra:
    image: vauxtra:latest
    ports:
      - "8888:8888"
    environment:
      - CORS_ORIGINS=https://staging.example.com
      - APP_PASSWORD=${VAUXTRA_PASSWORD}
      - SECRET_KEY=${SECRET_KEY}
      - HTTPS_ONLY=true
      - DEBUG=false
    volumes:
      - ./data:/app/data  # Persistent database
      - ./backups:/app/backups
    networks:
      - vauxtra-net
  
  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro  # SSL certs
    networks:
      - vauxtra-net
    depends_on:
      - vauxtra

networks:
  vauxtra-net:
    driver: bridge
```

### **Kubernetes Deployment**
```yaml
# vauxtra-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vauxtra
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vauxtra
  template:
    metadata:
      labels:
        app: vauxtra
    spec:
      serviceAccountName: vauxtra
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
      - name: vauxtra
        image: vauxtra:latest
        ports:
        - containerPort: 8888
        env:
        - name: CORS_ORIGINS
          value: "https://vauxtra.example.com"
        - name: APP_PASSWORD
          valueFrom:
            secretKeyRef:
              name: vauxtra-secrets
              key: app-password
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: vauxtra-secrets
              key: secret-key
        - name: HTTPS_ONLY
          value: "true"
        - name: DEBUG
          value: "false"
        volumeMounts:
        - name: data
          mountPath: /app/data
        - name: docker-socket
          mountPath: /var/run/docker.sock
        livenessProbe:
          httpGet:
            path: /api/health
            port: 8888
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /api/health
            port: 8888
          initialDelaySeconds: 10
          periodSeconds: 5
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: vauxtra-data
      - name: docker-socket
        hostPath:
          path: /var/run/docker.sock
          type: Socket
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: vauxtra-data
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: standard
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: vauxtra-service
spec:
  selector:
    app: vauxtra
  type: LoadBalancer
  ports:
  - protocol: TCP
    port: 443
    targetPort: 8888
```

---

## 🛡️ Network Architecture

### **Isolated Provider Networks**
```
┌─────────────────────────────────────────────┐
│           Vauxtra (Internal)                │
│  - API: localhost:8888                      │
│  - Frontend: http://localhost:5173          │
└──────┬──────────────────────────────────────┘
       │
       ├─→ DNS Provider Network (internal)
       │   ├─ AdGuard: 10.0.100.0/24
       │   ├─ Pi-hole: 10.0.100.0/24
       │   └─ Technitium: 10.0.100.0/24
       │
       ├─→ Proxy Provider Network (internal)
       │   ├─ NPM: 10.0.101.0/24
       │   └─ Traefik: 10.0.101.0/24
       │
       └─→ Backend Services (external)
           ├─ 192.168.x.x (local network)
           └─ Public internet (via providers)
```

### **Firewall Rules**

**Ingress (Vauxtra Container)**
- Allow TCP 8888 from: Nginx/Load Balancer
- Allow Docker socket access (read-only)
- Block all other ingress

**Egress (Vauxtra Container)**
- Allow DNS (UDP 53) to internal DNS providers
- Allow HTTP/HTTPS (TCP 80, 443) to internal proxies
- Allow Docker API calls (read-only) to Docker daemon
- Block all other egress

---

## 🔍 Monitoring & Logging

### **Key Metrics to Track**

```python
# Prometheus metrics (integrate with app/main.py)
from prometheus_client import Counter, Histogram, Gauge

# Request metrics
api_requests_total = Counter('vauxtra_api_requests_total', 'Total API requests', ['method', 'endpoint', 'status'])
api_request_duration = Histogram('vauxtra_api_request_duration_seconds', 'API request duration')

# Provider metrics
provider_health = Gauge('vauxtra_provider_health', 'Provider health status', ['provider_type', 'provider_name'])
provider_sync_errors = Counter('vauxtra_provider_sync_errors_total', 'Provider sync errors', ['provider_type'])

# Database metrics
db_connection_pool_size = Gauge('vauxtra_db_pool_size', 'Database connection pool size')
db_query_duration = Histogram('vauxtra_db_query_duration_seconds', 'Database query duration')

# Service metrics
services_total = Gauge('vauxtra_services_total', 'Total services')
services_by_provider = Gauge('vauxtra_services_by_provider', 'Services by provider', ['provider_type'])
```

### **Log Aggregation**

```yaml
# Centralized logging with ELK Stack
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /app/logs/vauxtra.log
    fields:
      app: vauxtra
      environment: production

output.elasticsearch:
  hosts: ["elasticsearch:9200"]
  protocol: "https"
```

### **Alerting Rules**

```yaml
# Prometheus alert rules (prometheus-rules.yaml)
groups:
  - name: vauxtra
    rules:
      - alert: ProviderDown
        expr: vauxtra_provider_health == 0
        for: 5m
        annotations:
          summary: "Provider {{ $labels.provider_name }} is down"
      
      - alert: HighErrorRate
        expr: rate(vauxtra_api_requests_total{status=~"5.."}[5m]) > 0.05
        for: 10m
        annotations:
          summary: "High API error rate (>5%)"
      
      - alert: DatabaseDiskFull
        expr: vauxtra_db_disk_usage_percent > 90
        annotations:
          summary: "Database disk usage > 90%"
```

---

## 🔄 Backup & Disaster Recovery

### **Backup Strategy**

```bash
#!/bin/bash
# Daily automated backup (cron: 0 2 * * *)

BACKUP_DIR="/backups/vauxtra"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DB_FILE="/app/data/vauxtra.db"

# 1. Backup database
cp "$DB_FILE" "$BACKUP_DIR/vauxtra_$TIMESTAMP.db"

# 2. Backup configuration
tar -czf "$BACKUP_DIR/config_$TIMESTAMP.tar.gz" /app/data/config/

# 3. Upload to S3
aws s3 cp "$BACKUP_DIR/vauxtra_$TIMESTAMP.db" s3://vauxtra-backups/

# 4. Retain only last 30 days
find "$BACKUP_DIR" -mtime +30 -delete

# 5. Alert if backup fails
if [ $? -ne 0 ]; then
  curl -X POST https://monitoring.example.com/alerts \
    -d "Vauxtra backup failed on $(hostname)"
fi
```

### **Recovery Procedure**

```bash
# 1. Stop Vauxtra container
docker stop vauxtra

# 2. Restore database
cp /backups/vauxtra/vauxtra_20260501_020000.db /app/data/vauxtra.db

# 3. Restore permissions
chown appuser:appuser /app/data/vauxtra.db
chmod 600 /app/data/vauxtra.db

# 4. Start Vauxtra
docker start vauxtra

# 5. Verify health
curl http://localhost:8888/api/health
```

---

## 🚨 Incident Response

### **Provider Connection Failure**

1. **Immediate**: Notification to ops team (PagerDuty/Slack)
2. **Investigation**: Check provider logs, network connectivity
3. **Mitigation**: Switch to failover provider (if configured)
4. **Resolution**: Fix provider issue, verify sync
5. **Post-Mortem**: Document root cause, add monitoring

### **Data Corruption**

1. **Immediate**: Take database snapshot
2. **Investigation**: Verify backup integrity
3. **Recovery**: Restore from last known good backup
4. **Validation**: Run `sqlite3 vauxtra.db PRAGMA integrity_check`
5. **Prevention**: Enable WAL mode, add constraints

### **Security Breach**

1. **Immediate**: Isolate affected systems, revoke API keys
2. **Investigation**: Review logs, check unauthorized access
3. **Mitigation**: Rotate secrets, reset passwords
4. **Notification**: Alert affected services/users
5. **Prevention**: Review security practices, strengthen auth

---

## 🧪 Testing Before Production

### **Security Testing**

```bash
# 1. CORS validation
curl -H "Origin: http://malicious.com" http://localhost:8888/api/health

# 2. CSRF protection
curl -X POST http://localhost:8888/api/services -c cookies.txt -b cookies.txt

# 3. Input validation
curl -X POST http://localhost:8888/api/services \
  -d '{"subdomain": "../../etc/passwd", "domain": "test"}'

# 4. Rate limiting
for i in {1..150}; do curl http://localhost:8888/api/health; done

# 5. Authentication bypass
curl -H "Authorization: Bearer invalid_key" http://localhost:8888/api/providers
```

### **Load Testing**

```bash
# Using Apache Bench (ab)
ab -n 1000 -c 10 http://localhost:8888/api/health

# Using k6
k6 run --vus 50 --duration 30s load-test.js
```

### **Database Testing**

```bash
# Backup/restore test
cp vauxtra.db vauxtra_backup.db
sqlite3 vauxtra.db .dump | sqlite3 vauxtra_restored.db
diff <(sqlite3 vauxtra.db .dump | sort) <(sqlite3 vauxtra_restored.db .dump | sort)
```

---

## 📋 Production Checklist

- [ ] All secrets in secure manager (not .env files)
- [ ] HTTPS configured with valid certificate
- [ ] CORS origins validated
- [ ] Rate limiting enabled
- [ ] Database backups configured & tested
- [ ] Monitoring & alerting in place
- [ ] Centralized logging enabled
- [ ] Firewall rules configured
- [ ] Security headers verified
- [ ] Load testing passed
- [ ] Disaster recovery plan documented
- [ ] On-call team trained
- [ ] Post-deployment verification complete

---

**Last Updated**: May 2, 2026
