#!/bin/sh
set -e

# Grant appuser access to Docker socket if mounted
if [ -S /var/run/docker.sock ]; then
    SOCK_GID=$(stat -c '%g' /var/run/docker.sock)
    if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
        groupadd -g "$SOCK_GID" dockersock || true
    fi
    SOCK_GROUP=$(getent group "$SOCK_GID" | cut -d: -f1)
    usermod -aG "$SOCK_GROUP" appuser || true
fi

chown -R appuser:appuser /app/data
exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8888
