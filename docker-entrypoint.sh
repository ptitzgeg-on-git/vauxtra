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

# Uvicorn reads `X-Forwarded-For` only when told which hop is allowed to set it, and it
# has to be told explicitly: accepting that header from anyone lets any caller claim any
# client address, which is worse than the problem it solves. Unset, every rate limit keys
# on the socket peer -- behind a proxy, one address for every visitor. `app/main.py` logs a
# warning the first time a forwarded request arrives while this is empty.
if [ -n "${FORWARDED_ALLOW_IPS:-}" ]; then
    set -- --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
else
    set --
fi

chown -R appuser:appuser /app/data
exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8888 "$@"
