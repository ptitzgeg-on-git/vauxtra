#!/bin/sh
set -e

# Started with `--user` (or `user:` in Compose): no root, so no group to change, nothing to
# chown and no gosu to drop privileges with. Each of those failed under `set -e` and the
# container never came up. The volume's ownership is then the operator's to set.
if [ "$(id -u)" != "0" ]; then
    if [ -n "${FORWARDED_ALLOW_IPS:-}" ]; then
        exec uvicorn app.main:app --host 0.0.0.0 --port 8888 --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
    fi
    exec uvicorn app.main:app --host 0.0.0.0 --port 8888
fi

# Grant appuser access to Docker socket if mounted
if [ -S /var/run/docker.sock ]; then
    SOCK_GID=$(stat -c '%g' /var/run/docker.sock)
    if [ "$SOCK_GID" = "0" ]; then
        # A socket owned by group root (Docker Desktop, some rootless setups): joining gid 0
        # would hand appuser every root-group file in the image, not just the socket.
        echo "vauxtra: /var/run/docker.sock belongs to gid 0; not adding appuser to the root group. Docker discovery stays unavailable until the socket has its own group." >&2
    else
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -g "$SOCK_GID" dockersock || true
        fi
        SOCK_GROUP=$(getent group "$SOCK_GID" | cut -d: -f1)
        usermod -aG "$SOCK_GROUP" appuser || true
    fi
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

# Only what is not already appuser's, and never through a symlink: a recursive chown
# rewrote the whole volume on every start, and `-h` keeps a link planted in the volume from handing
# appuser the file it points at. Directories and single-link regular files only: without a
# mounted volume /app/data shares the image's filesystem, and a hard link planted there to
# a root file (two links) would otherwise hand that file to appuser.
find /app/data \( -type d -o \( -type f -links 1 \) \) \( ! -user appuser -o ! -group appuser \) \
    -exec chown -h appuser:appuser {} +
exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8888 "$@"
