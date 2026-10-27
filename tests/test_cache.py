import time

from app.cache import Cache, MemoryCache


def test_memory_cache_ttl(monkeypatch):
    c = Cache(MemoryCache())
    c.set("a", {"x": 1}, ttl=10)
    assert c.get("a") == {"x": 1}
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 11)
    assert c.get("a") is None


def test_get_or_set_calls_producer_once():
    c = Cache(MemoryCache())
    calls = []
    producer = lambda: calls.append(1) or [1, 2]  # noqa: E731
    assert c.get_or_set("k", 60, producer) == [1, 2]
    assert c.get_or_set("k", 60, producer) == [1, 2]
    assert len(calls) == 1


def test_user_version_bump_invalidates_keys():
    c = Cache(MemoryCache())
    k1 = c.user_key("u1", "day", "2026-10-01")
    c.set(k1, "old", 60)
    c.bump_user("u1")
    k2 = c.user_key("u1", "day", "2026-10-01")
    assert k1 != k2 and c.get(k2) is None
    assert c.user_key("u2", "x") == "u:u2:v0:x"


def test_cache_failures_are_swallowed():
    class Broken:
        def get(self, k):
            raise ConnectionError

        def set(self, *a):
            raise ConnectionError

        def delete(self, k):
            raise ConnectionError

        def incr(self, k):
            raise ConnectionError

    c = Cache(Broken())
    assert c.get_or_set("k", 1, lambda: 5) == 5
    c.bump_user("u")
