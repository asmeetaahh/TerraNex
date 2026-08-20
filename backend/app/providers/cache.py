"""TTL caching for provider responses.

Coordinate keys are rounded to three decimals (~110 m). Two points inside the same
field therefore share an entry, which is what turns repeated dashboard loads and
repeated demo clicks into cache hits instead of upstream calls.

A hit is re-stamped `cached` rather than `live`: the data is genuinely real, but it
was not fetched during this request, and the distinction is visible to the frontend.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cachetools import TTLCache

from app.core.logging import get_logger
from app.providers.base import ProviderResult
from app.schemas.common import DataMode

logger = get_logger(__name__)

T = TypeVar("T")

COORD_PRECISION = 3
"""Decimal places kept in a coordinate cache key. 3 dp ≈ 110 m."""


def coord_key(latitude: float, longitude: float) -> str:
    """Round a coordinate pair into a stable cache key."""
    return f"{round(latitude, COORD_PRECISION)},{round(longitude, COORD_PRECISION)}"


def build_key(*parts: Any) -> str:
    return "|".join(str(p) for p in parts)


class ProviderCache:
    """A named TTL cache guarding one provider's responses.

    Concurrent requests for the same key are coalesced behind a per-key lock, so ten
    simultaneous dashboard loads for one farm make one upstream call rather than ten.
    """

    def __init__(self, name: str, ttl_seconds: int, maxsize: int = 512) -> None:
        self.name = name
        self.ttl_seconds = ttl_seconds
        self._cache: TTLCache[str, ProviderResult[Any]] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()

    def peek(self, key: str) -> ProviderResult[Any] | None:
        return self._cache.get(key)

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get_or_fetch(
        self, key: str, fetch: Callable[[], Awaitable[ProviderResult[T]]]
    ) -> ProviderResult[T]:
        """Return a cached result, or call `fetch` and cache a successful one.

        Failures are never cached — a provider that is down for one request must be
        retried on the next, not remembered as broken for the whole TTL.
        """
        hit = self._cache.get(key)
        if hit is not None:
            logger.debug("cache_hit", extra={"cache": self.name, "key": key})
            return hit.with_mode(DataMode.cached)

        async with self._lock_for(key):
            # Another coroutine may have populated it while we waited.
            hit = self._cache.get(key)
            if hit is not None:
                return hit.with_mode(DataMode.cached)

            result = await fetch()
            if result.ok and result.data is not None:
                self._cache[key] = result
            return result


_registry: dict[str, ProviderCache] = {}


def get_cache(name: str, ttl_seconds: int) -> ProviderCache:
    """Fetch or create a named cache. Registered so tests can clear every cache."""
    cache = _registry.get(name)
    if cache is None or cache.ttl_seconds != ttl_seconds:
        cache = ProviderCache(name, ttl_seconds)
        _registry[name] = cache
    return cache


def clear_all_caches() -> None:
    """Used by the test suite so cached data cannot leak between tests."""
    for cache in _registry.values():
        cache.clear()
