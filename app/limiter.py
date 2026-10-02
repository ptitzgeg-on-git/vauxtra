"""Rate limiter for routes decorated with @limiter.limit.

No default limits (SlowAPIMiddleware is not mounted). Behind a reverse proxy set
FORWARDED_ALLOW_IPS so the client address is the real one, not the proxy.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
