"""Request-scoped cache for provider operations."""

import time
from typing import TypeVar, Callable, Any, Optional

T = TypeVar("T")


class RequestCache:
    """
    Per-request cache for expensive provider operations.
    
    Prevents N+1 queries and redundant provider calls within a single request.
    Automatically expires after request completes.
    """
    
    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl: dict[str, float] = {}  # Per-key TTL in seconds
    
    def set(self, key: str, value: Any, ttl: float = 300.0) -> None:
        """
        Cache a value with optional TTL.
        
        Args:
            key: Cache key (e.g., "provider:123:connection")
            value: Value to cache
            ttl: Time-to-live in seconds (default 5 minutes)
        """
        self._cache[key] = (value, time.time())
        self._ttl[key] = ttl
    
    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a cached value if not expired.
        
        Args:
            key: Cache key
        
        Returns:
            Cached value, or None if missing or expired
        """
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
        """
        Get cached value or compute if missing.
        
        Implements cache-aside pattern.
        
        Args:
            key: Cache key
            compute_fn: Function to call if cache miss
            ttl: Time-to-live for computed value
        
        Returns:
            Cached or computed value
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        
        value = compute_fn()
        self.set(key, value, ttl)
        return value
    
    def clear(self) -> None:
        """Clear all cached values."""
        self._cache.clear()
        self._ttl.clear()
    
    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "keys": list(self._cache.keys()),
        }


# Global request cache (will be created per request in middleware)
_request_cache: Optional[RequestCache] = None


def get_request_cache() -> RequestCache:
    """Get the current request's cache."""
    global _request_cache
    if _request_cache is None:
        _request_cache = RequestCache()
    return _request_cache


def reset_request_cache() -> None:
    """Reset the global request cache (call after each request)."""
    global _request_cache
    _request_cache = None
