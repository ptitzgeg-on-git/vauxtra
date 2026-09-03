"""The rate limiter, and the two things that have to be true for it to work.

`default_limits` used to sit here announcing 120 requests a minute. slowapi applies those
through `SlowAPIMiddleware`, which this application does not mount: the number was never in
force, and reading this file was a way to believe the whole API was throttled when only the
routes carrying `@limiter.limit(...)` ever were. It is removed rather than made real -- a
blanket per-IP limit on an instance whose client address may be a single reverse proxy (see
below) throttles every operator at once, which is worse than the gap it closes.

`get_remote_address` reads `request.client.host`, the socket peer. Behind a reverse proxy
that is the proxy, for everyone: the 5/minute on the login route becomes five attempts a
minute shared by the whole internet, so one attacker locks the operator out as effectively
as the limit slows the attacker down. Uvicorn only reads `X-Forwarded-For` when it is told
which hop may set it, so `docker-entrypoint.sh` passes `--proxy-headers
--forwarded-allow-ips` when `FORWARDED_ALLOW_IPS` is configured, and `app/main.py` says so
out loud, once, if a forwarded request arrives while it is not.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
