"""Caching layer: Redis when REDIS_URL is configured, otherwise in-process TTL cache.

Values are JSON-serialised so both backends behave identically. Per-user data is
invalidated by bumping a version counter that is part of the cache key, so all
stats for a user become stale at once without scanning keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable

from app.config import get_settings

log = logging.getLogger(__name__)


class MemoryCache:
    def __init__(self, maxsize: int = 10_000):
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.monotonic():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            if len(self._data) >= self._maxsize:
                self._evict()
            self._data[key] = (time.monotonic() + ttl, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str) -> int:
        with self._lock:
            _, value = self._data.get(key, (0, "0"))
            new = int(value) + 1
            self._data[key] = (float("inf"), str(new))
            return new

    def _evict(self) -> None:
        now = time.monotonic()
        for k in [k for k, (exp, _) in self._data.items() if exp < now]:
            del self._data[k]
        while len(self._data) >= self._maxsize:
            self._data.pop(next(iter(self._data)))


class RedisCache:
    def __init__(self, url: str):
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._r.set(key, value, ex=ttl)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def incr(self, key: str) -> int:
        return int(self._r.incr(key))


class Cache:
    """Thin JSON wrapper; cache failures never break a request."""

    def __init__(self, backend):
        self.backend = backend

    def get(self, key: str) -> Any | None:
        try:
            raw = self.backend.get(key)
        except Exception:  # noqa: BLE001 - a cache outage must not fail requests
            log.warning("cache get failed for %s", key, exc_info=True)
            return None
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            self.backend.set(key, json.dumps(value, default=str), ttl)
        except Exception:  # noqa: BLE001
            log.warning("cache set failed for %s", key, exc_info=True)

    def delete(self, key: str) -> None:
        try:
            self.backend.delete(key)
        except Exception:  # noqa: BLE001
            log.warning("cache delete failed for %s", key, exc_info=True)

    def get_or_set(self, key: str, ttl: int, producer: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        if value is not None:
            self.set(key, value, ttl)
        return value

    # --- per-user namespace versioning ---
    def user_version(self, user_id: str) -> int:
        v = self.get(f"uv:{user_id}")
        return int(v) if v is not None else 0

    def bump_user(self, user_id: str) -> None:
        try:
            self.backend.incr(f"uv:{user_id}")
        except Exception:  # noqa: BLE001
            log.warning("cache version bump failed for %s", user_id, exc_info=True)

    def user_key(self, user_id: str, *parts: Any) -> str:
        return ":".join(["u", user_id, f"v{self.user_version(user_id)}", *map(str, parts)])


def hash_key(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode())
        h.update(b"\0")
    return h.hexdigest()


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        url = get_settings().redis_url
        backend = None
        if url:
            try:
                backend = RedisCache(url)
                backend._r.ping()
                log.info("Using Redis cache")
            except Exception:  # noqa: BLE001
                log.warning("Redis unavailable, falling back to in-memory cache", exc_info=True)
                backend = None
        _cache = Cache(backend or MemoryCache())
    return _cache
