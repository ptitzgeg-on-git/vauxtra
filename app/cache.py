"""Request-scoped cache for provider operations."""

import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypeVar

T = TypeVar("T")


class RequestCache:
    """Per-request cache — prevents N+1 provider calls within a single request."""

    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl: dict[str, float] = {}

    def set(self, key: str, value: Any, ttl: float = 300.0) -> None:
        self._cache[key] = (value, time.time())
        self._ttl[key] = ttl

    def get(self, key: str) -> Any | None:
        if key not in self._cache:
            return None
        value, timestamp = self._cache[key]
        ttl = self._ttl.get(key, 300.0)
        if time.time() - timestamp > ttl:
            del self._cache[key]
            del self._ttl[key]
            return None
        return value

    def get_or_compute(self, key: str, compute_fn: Callable[[], T], ttl: float = 300.0) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute_fn()
        self.set(key, value, ttl)
        return value

    def clear(self) -> None:
        self._cache.clear()
        self._ttl.clear()

    def stats(self) -> dict[str, Any]:
        return {"size": len(self._cache), "keys": list(self._cache.keys())}


# Per-coroutine request cache — isolated per async task, safe under concurrent requests
_request_cache_var: ContextVar[RequestCache | None] = ContextVar("_request_cache", default=None)


def get_request_cache() -> RequestCache:
    """Get the current request's cache, creating one if needed."""
    cache = _request_cache_var.get()
    if cache is None:
        cache = RequestCache()
        _request_cache_var.set(cache)
    return cache
