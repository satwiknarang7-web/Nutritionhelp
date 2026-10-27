import os

import pytest

os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("APP_ENCRYPTION_KEY", "y24zFgtVXiUV_mapc97MJrN5bV1nNrcJ12dsflgm-k4=")
os.environ["ENABLE_OPENFOODFACTS"] = "false"
os.environ["ENABLE_USDA"] = "false"

from tests.pg import TempPostgres  # noqa: E402

_pg = None
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
else:
    _pg = TempPostgres()
    os.environ["DATABASE_URL"] = _pg.start()

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, cache, db  # noqa: E402
from app.main import app  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.repository import NeonRepository  # noqa: E402

migrate(os.environ["DATABASE_URL"], verbose=False)

PASSWORD = "correct-horse-battery"


def pytest_sessionfinish(session, exitstatus):
    db.close_pool()
    if _pg:
        _pg.stop()


@pytest.fixture(autouse=True)
def clean_state():
    cache._cache = cache.Cache(cache.MemoryCache())
    with db.system_tx() as conn:
        conn.execute("truncate users cascade")
    yield


@pytest.fixture
def user():
    return auth.create_user("asha@example.com", PASSWORD, "Asha")


@pytest.fixture
def repo(user):
    return NeonRepository(user.id)


@pytest.fixture
def anon_client():
    return TestClient(app)


@pytest.fixture
def client(user):
    c = TestClient(app, headers={"X-Requested-With": "fetch"})
    r = c.post("/login", data={"email": user.email, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c
