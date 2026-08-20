"""Backend-agnostic async caching layer.

Architecture summary:
    - `CacheBackend` (ABC): the interface every cache backend implements.
      It operates on `str` keys and `str` values only — never arbitrary
      Python objects. This is deliberate: every real cache backend
      (in-memory dict, Redis, Memcached) is fundamentally a string/bytes
      store, so standardizing on strings here means a future
      `RedisCacheBackend` requires zero changes to this interface or to
      any calling code. Callers (the tool layer, built later) are
      responsible for `json.dumps()` before `set()` and `json.loads()`
      after `get()`.
    - `InMemoryCacheBackend`: the only implementation that exists today
      (`CACHE_BACKEND=memory`). A process-local dict with per-key TTL
      expiry, guarded by an `asyncio.Lock` so it's safe under concurrent
      MCP tool calls.
    - `create_cache_backend(settings)`: factory that reads
      `Settings.cache_backend` and returns the configured implementation.
      Selecting `CACHE_BACKEND=redis` today raises `CacheError` loudly —
      there is intentionally no silent fallback to memory, so a
      real-deployment misconfiguration is never hidden.
    - `build_cache_key(*parts)`: consistent, collision-resistant cache
      key construction. The tool layer will key cached CFR content on
      `(title, part, section, date)`, since contracts repeatedly
      reference the same regulation text across many clauses.

Error handling convention for callers:
    `CacheBackend` methods raise `CacheError` (from `exceptions.py`) on
    failure. The cache is a performance optimization, not a correctness
    dependency for the compliance pipeline — callers should generally
    wrap cache reads in a try/except that falls through to a live API
    call on `CacheError`, and log-and-swallow cache write failures
    rather than surfacing them to the end user.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from asyncio import Lock
from dataclasses import dataclass

from cfr_compliance_mcp.config import Settings, get_settings
from cfr_compliance_mcp.exceptions import CacheError
from cfr_compliance_mcp.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class CacheBackend(ABC):
    """Abstract interface every cache backend must implement.

    All methods operate on string keys/values so any backend (in-memory,
    Redis, Memcached, ...) can implement this without special-casing
    Python object serialization. Callers are responsible for
    serializing/deserializing values (typically via `json`) before and
    after calling this interface.
    """

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """Return the cached value for `key`, or `None` if missing or expired."""
        raise NotImplementedError

    @abstractmethod
    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        """Store `value` under `key`.

        If `ttl_seconds` is `None`, the backend's configured default TTL
        applies. Overwrites any existing value for `key`.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove `key` from the cache. A no-op if the key does not exist."""
        raise NotImplementedError

    @abstractmethod
    async def clear(self) -> None:
        """Remove all entries. Intended for tests and administrative use, not
        the normal request path."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release any underlying resources (connections, background tasks).

        Must be safe to call even if the backend holds no real resources
        (e.g. `InMemoryCacheBackend`), so callers can treat every
        `CacheBackend` uniformly during server shutdown.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CacheEntry:
    """Internal record for one cached value plus its expiry time.

    `expires_at` is measured against `time.monotonic()`, not wall-clock
    time, so the cache is immune to system clock adjustments (NTP
    corrections, manual clock changes) during the server's lifetime.
    """

    value: str
    expires_at: float


class InMemoryCacheBackend(CacheBackend):
    """Process-local, in-memory cache with per-key TTL expiry.

    Suitable for the POC and for single-process deployments. State is
    not shared across processes or restarts — a multi-worker deployment
    should switch to a Redis-backed implementation (`CACHE_BACKEND=redis`,
    not yet implemented — see `create_cache_backend`) once that need
    arises. Nothing in the calling code (the tool layer) would need to
    change to make that switch, since both implement `CacheBackend`.
    """

    def __init__(self, *, default_ttl_seconds: int = 3600) -> None:
        self._default_ttl_seconds = default_ttl_seconds
        self._store: dict[str, _CacheEntry] = {}
        self._lock = Lock()
        logger.debug(
            "InMemoryCacheBackend initialized",
            extra={"default_ttl_seconds": default_ttl_seconds},
        )

    async def get(self, key: str) -> str | None:
        try:
            async with self._lock:
                entry = self._store.get(key)
                if entry is None:
                    logger.debug("Cache miss", extra={"cache_key": key})
                    return None
                if entry.expires_at < time.monotonic():
                    logger.debug("Cache entry expired", extra={"cache_key": key})
                    del self._store[key]
                    return None
                logger.debug("Cache hit", extra={"cache_key": key})
                return entry.value
        except Exception as exc:  # defensive: dict/lock ops shouldn't normally raise
            raise CacheError(f"Failed to read cache key {key!r}: {exc}") from exc

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        try:
            effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
            if effective_ttl <= 0:
                raise CacheError(
                    f"ttl_seconds must be positive, got {effective_ttl} for key {key!r}."
                )
            expires_at = time.monotonic() + effective_ttl
            async with self._lock:
                self._store[key] = _CacheEntry(value=value, expires_at=expires_at)
            logger.debug("Cache set", extra={"cache_key": key, "ttl_seconds": effective_ttl})
        except CacheError:
            raise
        except Exception as exc:
            raise CacheError(f"Failed to write cache key {key!r}: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            async with self._lock:
                self._store.pop(key, None)
            logger.debug("Cache delete", extra={"cache_key": key})
        except Exception as exc:
            raise CacheError(f"Failed to delete cache key {key!r}: {exc}") from exc

    async def clear(self) -> None:
        try:
            async with self._lock:
                removed = len(self._store)
                self._store.clear()
            logger.info("Cache cleared", extra={"entries_removed": removed})
        except Exception as exc:
            raise CacheError(f"Failed to clear cache: {exc}") from exc

    async def close(self) -> None:
        # No real resources to release for a plain dict; present for
        # interface symmetry with backends that hold live connections
        # (e.g. a future RedisCacheBackend), so server.py can call
        # `await cache.close()` uniformly during shutdown regardless of
        # which backend is configured.
        logger.debug("InMemoryCacheBackend closed (no-op)")

    async def evict_expired(self) -> int:
        """Proactively remove all currently-expired entries.

        Not required for correctness — `get()` already evicts lazily on
        read — but useful if a server wants to periodically bound memory
        use from keys that were written once and never read again (e.g.
        a background `asyncio` task calling this every few minutes).
        Returns the number of entries removed.
        """
        now = time.monotonic()
        async with self._lock:
            expired_keys = [k for k, entry in self._store.items() if entry.expires_at < now]
            for k in expired_keys:
                del self._store[k]
        if expired_keys:
            logger.debug("Evicted expired cache entries", extra={"count": len(expired_keys)})
        return len(expired_keys)

    def __len__(self) -> int:
        """Current entry count, including not-yet-lazily-evicted expired
        entries. Primarily useful for tests/diagnostics."""
        return len(self._store)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_cache_backend(settings: Settings | None = None) -> CacheBackend:
    """Construct the configured `CacheBackend` from `Settings`.

    `CACHE_BACKEND=memory` -> `InMemoryCacheBackend` (the only
    implementation that exists today).

    `CACHE_BACKEND=redis` -> intentionally raises `CacheError` rather
    than silently falling back to in-memory. A Redis-backed
    implementation is a documented future upgrade (see
    `exceptions.CacheError` usage here) — when it's built, it should be
    added as a new `RedisCacheBackend(CacheBackend)` class in this
    module and wired in below; no other module should need to change.
    """
    resolved_settings = settings or get_settings()

    if resolved_settings.cache_backend == "memory":
        return InMemoryCacheBackend(default_ttl_seconds=resolved_settings.cache_ttl_seconds)

    if resolved_settings.cache_backend == "redis":
        raise CacheError(
            "CACHE_BACKEND=redis is configured but no Redis-backed CacheBackend "
            "implementation exists yet. Either implement RedisCacheBackend(CacheBackend) "
            "in cache_backend.py and wire it into create_cache_backend(), or set "
            "CACHE_BACKEND=memory in your environment."
        )

    # Unreachable in practice: Settings.cache_backend is a Pydantic
    # Literal["memory", "redis"], so invalid values are already rejected
    # at config-load time. Kept as defense-in-depth against future
    # changes to that Literal that forget to update this factory.
    raise CacheError(f"Unknown CACHE_BACKEND: {resolved_settings.cache_backend!r}")


# ---------------------------------------------------------------------------
# Cache key construction
# ---------------------------------------------------------------------------


def build_cache_key(*parts: str | int | None) -> str:
    """Build a consistent, collision-resistant cache key from ordered parts.

    Example:
        build_cache_key("section", 40, "261", "10", "2026-01-01")
        -> "section:40:261:10:2026-01-01"

    `None` parts are rendered as the literal string `"none"` rather than
    omitted, so that e.g. `build_cache_key("part", 40, "261", None)` and
    `build_cache_key("part", 40, "261")` never collide with each other.

    This is the standard key-builder the tool layer will use to cache
    eCFR content keyed on `(title, part, section, date)`, per the
    caching strategy used by the tool layer.
    """
    rendered = [str(part) if part is not None else "none" for part in parts]
    return ":".join(rendered)
