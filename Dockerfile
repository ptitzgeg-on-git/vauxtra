# Build arguments for multi-architecture (implicit with buildx)
FROM node:22-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.13-slim
ARG APP_VERSION=dev
WORKDIR /app
ENV TZ=UTC
ENV APP_VERSION=${APP_VERSION}
RUN apt-get update && apt-get install -y --no-install-recommends curl gosu tzdata && rm -rf /var/lib/apt/lists/* && groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY --from=frontend-builder /build/dist ./frontend/dist/
RUN mkdir -p /app/data && chown -R appuser:appuser /app
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fs http://localhost:8888/api/health || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
