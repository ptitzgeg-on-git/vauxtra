# Build arguments for multi-architecture (implicit with buildx)
FROM node:26-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.14-slim
ARG APP_VERSION=dev
WORKDIR /app
ENV TZ=UTC
ENV APP_VERSION=${APP_VERSION}
# `upgrade` runs before `install`: the base image is rebuilt on its own schedule, so between
# two of its rebuilds Debian publishes security updates for packages already baked into it.
# Without this line they are never applied, and the image ships CVEs that are fixable today.
# perl-base, libsqlite3-0, libpcre2-8-0 and gzip were all in exactly that state -- fourteen
# fixable CRITICAL/HIGH findings, every one of them already patched in the Debian archive.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends curl gosu tzdata && rm -rf /var/lib/apt/lists/* && groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
COPY requirements.txt .
# pip is a build tool, and this is a runtime image.
#
# It carries its own dependency tree in `pip/_vendor/`, pinned in a vendor.txt that no
# scanner can tell apart from a real install list. So every advisory against something pip
# vendors -- msgpack, requests, urllib3 -- is reported against this image, and none of them
# is fixable from requirements.txt, because none of those packages is installed here. The
# first image scan of this repository found exactly two HIGH findings and both were this:
# msgpack GHSA-6v7p-g79w-8964 (vendored code, reachable only by running pip) and setuptools
# CVE-2025-47273, whose vulnerable module is not even present -- pip pins the version
# without shipping the code.
#
# Removing pip drops both, and takes a working package installer out of a network-facing
# container at the same time. Nothing here imports pip, pkg_resources or setuptools, and
# the entrypoint does not call them; the ensurepip wheel goes too, since it is a complete
# copy of what we just deleted.
#
# The two directories are asked of `sysconfig` instead of being spelled out. They used to
# read /usr/local/lib/python3.13/..., and when the base image moved to 3.14 both `rm -rf`
# calls began deleting a path that no longer existed. That succeeds, silently, so the
# removal stopped happening while the build stayed green and the image kept shipping the
# 1.8 MB ensurepip wheel this comment says it deletes. The last line exists for the same
# reason: if ensurepip ever survives again, the build fails instead of going unnoticed.
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y pip \
    && rm -rf "$(python -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')/ensurepip" \
    && rm -rf "$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"/pip* \
    && if python -c 'import ensurepip' 2>/dev/null; then echo 'ensurepip survived its own removal'; exit 1; fi
COPY app/ ./app/
COPY --from=frontend-builder /build/dist ./frontend/dist/
RUN mkdir -p /app/data && chown -R appuser:appuser /app
COPY docker-entrypoint.sh /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh && chmod +x /usr/local/bin/docker-entrypoint.sh
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fs http://localhost:8888/api/health || exit 1
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
