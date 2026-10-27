import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("APP_ENCRYPTION_KEY", "y24zFgtVXiUV_mapc97MJrN5bV1nNrcJ12dsflgm-k4=")
os.environ["ENABLE_OPENFOODFACTS"] = "false"
os.environ["ENABLE_USDA"] = "false"

from app import auth, cache  # noqa: E402
from app.main import app  # noqa: E402
from tests.fakes import InMemoryRepository  # noqa: E402

USER = auth.AuthUser(id="00000000-0000-0000-0000-000000000001", email="asha@example.com")


@pytest.fixture(autouse=True)
def fresh_cache():
    cache._cache = cache.Cache(cache.MemoryCache())
    yield


@pytest.fixture
def repo():
    return InMemoryRepository(USER.id)


@pytest.fixture
def client(repo, monkeypatch):
    monkeypatch.setattr(auth, "verify_access_token", lambda token: USER if token == "good" else None)
    monkeypatch.setattr(auth, "refresh", lambda token: None)
    app.dependency_overrides[auth.get_repo] = lambda: repo
    c = TestClient(app, headers={"X-Requested-With": "fetch"})
    c.cookies.set(auth.ACCESS_COOKIE, "good")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    return TestClient(app)
