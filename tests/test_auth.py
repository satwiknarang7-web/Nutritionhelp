import psycopg
import pytest
from fastapi.testclient import TestClient

from app import auth, db, emailer
from app.main import app
from app.repository import NeonRepository
from tests.conftest import PASSWORD

DAY = "2026-10-27"


def login(client, email="asha@example.com", password=PASSWORD):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def test_signup_creates_account_profile_and_session(anon_client):
    r = anon_client.post(
        "/signup", data={"display_name": "Ravi", "email": " Ravi@Example.com ", "password": "longenough1"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/profile")
    cookie = [c for c in r.headers.get_list("set-cookie") if c.startswith(auth.SESSION_COOKIE)][0]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    profile = anon_client.get("/api/profile", headers={"X-Requested-With": "fetch"}).json()
    assert profile["display_name"] == "Ravi"
    with db.system_tx() as conn:
        row = conn.execute("select email, password_hash from users").fetchone()
        sessions = conn.execute("select token_hash from sessions").fetchall()
    assert row["email"] == "ravi@example.com"
    assert row["password_hash"].startswith("$argon2id$")
    token = r.cookies.get(auth.SESSION_COOKIE) or cookie.split("=", 1)[1].split(";")[0]
    assert all(bytes(s["token_hash"]) != token.encode() for s in sessions)  # only hashes stored


def test_signup_validation(anon_client, user):
    r = anon_client.post("/signup", data={"email": "asha@example.com", "password": "longenough1"})
    assert "already exists" in r.text
    r = anon_client.post("/signup", data={"email": "nope", "password": "longenough1"})
    assert "valid email" in r.text
    r = anon_client.post("/signup", data={"email": "x@y.co", "password": "short"})
    assert "at least 8" in r.text


def test_login_logout(anon_client, user):
    assert "Incorrect email or password" in login(anon_client, password="wrong-password").text
    assert "Incorrect email or password" in login(anon_client, email="ghost@example.com").text
    r = login(anon_client, email="ASHA@example.com")
    assert r.status_code == 303 and r.headers["location"] == "/diary"
    assert anon_client.get("/diary").status_code == 200
    anon_client.post("/logout")
    assert anon_client.get("/diary", follow_redirects=False).status_code == 303
    with db.system_tx() as conn:
        assert conn.execute("select count(*) as n from sessions").fetchone()["n"] == 0


def test_login_rate_limited(anon_client, user):
    for _ in range(10):
        login(anon_client, password="wrong-password")
    r = login(anon_client)  # even the right password is refused while limited
    assert "Too many attempts" in r.text


def test_invalid_or_expired_session_cookie_is_cleared(anon_client, user):
    anon_client.cookies.set(auth.SESSION_COOKIE, "forged")
    r = anon_client.get("/diary", follow_redirects=False)
    assert r.status_code == 303
    assert any(c.startswith(f'{auth.SESSION_COOKIE}=""') for c in r.headers.get_list("set-cookie"))

    token = auth.create_session(user.id)
    with db.system_tx() as conn:
        conn.execute("update sessions set expires_at = now() - interval '1 minute'")
    anon_client.cookies.set(auth.SESSION_COOKIE, token)
    assert anon_client.get("/diary", follow_redirects=False).status_code == 303


def test_password_reset_flow(anon_client, user, monkeypatch):
    sent = {}
    monkeypatch.setattr(emailer, "send_password_reset", lambda to, link: sent.update(to=to, link=link) or True)
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    other = TestClient(app)
    assert login(other).status_code == 303  # an existing session elsewhere

    r = anon_client.post("/forgot-password", data={"email": "asha@example.com"})
    assert "reset link is on its way" in r.text
    anon_client.post("/forgot-password", data={"email": "nobody@example.com"})  # same response, no email
    assert sent["to"] == "asha@example.com"
    token = sent["link"].split("token=")[1]

    r = anon_client.post("/reset-password", data={"token": token, "password": "brand-new-pass"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/diary"
    r = anon_client.post("/reset-password", data={"token": token, "password": "another-pass1"})
    assert "invalid or has expired" in r.text  # single use
    assert other.get("/diary", follow_redirects=False).status_code == 303  # old sessions revoked
    assert login(TestClient(app), password="brand-new-pass").status_code == 303


def test_email_verification(client, user, monkeypatch):
    sent = {}
    monkeypatch.setattr(emailer, "send_verification", lambda to, link: sent.update(link=link) or True)
    assert client.get("/api/account").json()["email_verified"] is False
    assert client.post("/api/account/verify-email").json() == {"sent": True}
    token = sent["link"].split("token=")[1]
    client.get(f"/verify-email?token={token}")
    with db.system_tx() as conn:
        assert conn.execute("select email_verified_at from users").fetchone()["email_verified_at"] is not None


def test_require_email_verification(anon_client, user, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "require_email_verification", True)
    assert "confirm your email" in login(anon_client).text


def test_change_password_keeps_current_session(client, user):
    other = TestClient(app)
    login(other)
    r = client.post("/api/account/password", json={"current_password": "nope", "new_password": "newpassword1"})
    assert r.status_code == 400
    r = client.post("/api/account/password", json={"current_password": PASSWORD, "new_password": "newpassword1"})
    assert r.status_code == 200
    assert client.get("/api/account").status_code == 200
    assert other.get("/api/account", headers={"X-Requested-With": "fetch"}).status_code == 401


def test_delete_account_removes_everything(client, user):
    client.post("/api/water", json={"logged_on": DAY, "ml": 250})
    assert client.post("/api/account/delete", json={"password": "wrong"}).status_code == 400
    assert client.post("/api/account/delete", json={"password": PASSWORD}).status_code == 204
    with db.system_tx() as conn:
        assert conn.execute("select count(*) as n from food_logs").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from users").fetchone()["n"] == 0


def test_row_level_security_isolates_users(user):
    mine = NeonRepository(user.id)
    entry = mine.add_log({"logged_on": DAY, "meal": "lunch", "food_name": "Dal", "quantity": 1, "unit": "katori",
                          "grams": 150, "nutrients": {"energy": 150}})
    other_user = auth.create_user("ravi@example.com", PASSWORD)
    theirs = NeonRepository(other_user.id)
    assert theirs.get_log(entry["id"]) is None
    assert theirs.delete_log(entry["id"]) is False
    # Even a query without a user_id filter only sees the caller's rows
    with db.user_tx(other_user.id) as conn:
        assert conn.execute("select count(*) as n from food_logs").fetchone()["n"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from users")  # auth tables are not granted to the app role
    with db.user_tx(other_user.id) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "insert into food_logs (user_id, logged_on, meal, food_name, quantity, unit, grams) "
            "values (%s, %s, 'lunch', 'x', 1, 'g', 1)",
            (user.id, DAY),
        )
