"""Built-in authentication stored in Neon.

* Passwords: Argon2id (argon2-cffi defaults), transparently re-hashed when parameters change.
* Sessions: 256-bit random token in an httpOnly cookie; only its SHA-256 is stored.
  Sliding 30-day expiry, cached briefly to avoid a query per request.
* Email verification + password reset: single-use hashed tokens with expiry.
* Brute-force protection: per-IP and per-email login rate limits.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app import db, emailer
from app.cache import get_cache
from app.config import get_settings
from app.repository import NeonRepository, Repository

log = logging.getLogger(__name__)

SESSION_COOKIE = "nt_session"
SESSION_CACHE_TTL = 60
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
MAX_PASSWORD = 128

_hasher = PasswordHasher()
# Used to spend the same time verifying when the email does not exist
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


@dataclass
class AuthUser:
    id: str
    email: str
    email_verified: bool = False


class NotAuthenticated(Exception):
    pass


class AuthError(Exception):
    pass


class RateLimited(AuthError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _sha(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def validate_credentials(email: str, password: str) -> str:
    email = normalize_email(email)
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AuthError("Enter a valid email address.")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"Password must be at most {MAX_PASSWORD} characters.")
    return email


def _rate_limit(key: str, limit: int, window_s: int) -> None:
    cache = get_cache()
    count = int(cache.get(key) or 0) + 1
    cache.set(key, count, window_s)
    if count > limit:
        raise RateLimited("Too many attempts. Please wait a few minutes and try again.")


def _user(row: dict) -> AuthUser:
    return AuthUser(id=str(row["id"]), email=row["email"], email_verified=row["email_verified_at"] is not None)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def create_user(email: str, password: str, display_name: str = "") -> AuthUser:
    email = validate_credentials(email, password)
    password_hash = _hasher.hash(password)
    with db.system_tx() as conn:
        row = conn.execute(
            "insert into users (email, password_hash) values (%s, %s) on conflict (email) do nothing "
            "returning id, email, email_verified_at",
            (email, password_hash),
        ).fetchone()
        if not row:
            raise AuthError("An account with this email already exists. Try signing in.")
        conn.execute(
            "insert into profiles (user_id, display_name) values (%s, %s)",
            (row["id"], (display_name or email.split("@")[0])[:80]),
        )
    return _user(row)


def authenticate(email: str, password: str, ip: str = "") -> AuthUser:
    email = normalize_email(email)
    _rate_limit(f"rl:login:ip:{ip}", 30, 15 * 60)
    _rate_limit(f"rl:login:email:{email}", 10, 15 * 60)
    with db.system_tx() as conn:
        row = conn.execute(
            "select id, email, email_verified_at, password_hash from users where email = %s", (email,)
        ).fetchone()
    try:
        _hasher.verify(row["password_hash"] if row else _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
        raise AuthError("Incorrect email or password.") from exc
    if not row:
        raise AuthError("Incorrect email or password.")
    user = _user(row)
    if get_settings().require_email_verification and not user.email_verified:
        raise AuthError("Please confirm your email first - check your inbox for the link.")
    with db.system_tx() as conn:
        if _hasher.check_needs_rehash(row["password_hash"]):
            conn.execute("update users set password_hash = %s where id = %s", (_hasher.hash(password), user.id))
        conn.execute("update users set last_login_at = now() where id = %s", (user.id,))
    get_cache().delete(f"rl:login:email:{email}")
    return user


def change_password(user_id: str, current: str, new: str, keep_token: str | None = None) -> None:
    if not (MIN_PASSWORD <= len(new) <= MAX_PASSWORD):
        raise AuthError(f"New password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters.")
    with db.system_tx() as conn:
        row = conn.execute("select password_hash from users where id = %s", (user_id,)).fetchone()
        try:
            _hasher.verify(row["password_hash"], current)
        except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
            raise AuthError("Your current password is incorrect.") from exc
        conn.execute(
            "update users set password_hash = %s, updated_at = now() where id = %s", (_hasher.hash(new), user_id)
        )
    revoke_sessions(user_id, except_token=keep_token)


def delete_account(user_id: str, password: str) -> None:
    with db.system_tx() as conn:
        row = conn.execute("select password_hash from users where id = %s", (user_id,)).fetchone()
        try:
            _hasher.verify(row["password_hash"], password)
        except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
            raise AuthError("Password is incorrect.") from exc
        conn.execute("delete from users where id = %s", (user_id,))  # cascades to all user data


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def create_session(user_id: str, user_agent: str = "", ip: str = "") -> str:
    token = secrets.token_urlsafe(32)
    days = get_settings().session_days
    with db.system_tx() as conn:
        conn.execute(
            "insert into sessions (token_hash, user_id, expires_at, user_agent, ip) "
            "values (%s, %s, now() + make_interval(days => %s), %s, %s)",
            (_sha(token), user_id, days, (user_agent or "")[:300], (ip or "")[:64]),
        )
        # opportunistic cleanup of this user's expired sessions
        conn.execute("delete from sessions where user_id = %s and expires_at < now()", (user_id,))
    return token


def user_for_session(token: str) -> AuthUser | None:
    if not token or len(token) > 100:
        return None
    cache = get_cache()
    key = f"sess:{_sha(token).hex()}"
    cached = cache.get(key)
    if cached:
        return AuthUser(**cached)
    days = get_settings().session_days
    with db.system_tx() as conn:
        row = conn.execute(
            "select u.id, u.email, u.email_verified_at, s.last_seen_at < now() - interval '1 day' as stale "
            "from sessions s join users u on u.id = s.user_id where s.token_hash = %s and s.expires_at > now()",
            (_sha(token),),
        ).fetchone()
        if not row:
            return None
        if row["stale"]:  # sliding expiry, written at most once a day
            conn.execute(
                "update sessions set last_seen_at = now(), expires_at = now() + make_interval(days => %s) "
                "where token_hash = %s",
                (days, _sha(token)),
            )
    user = _user(row)
    cache.set(key, {"id": user.id, "email": user.email, "email_verified": user.email_verified}, SESSION_CACHE_TTL)
    return user


def destroy_session(token: str) -> None:
    if not token:
        return
    with db.system_tx() as conn:
        conn.execute("delete from sessions where token_hash = %s", (_sha(token),))
    get_cache().delete(f"sess:{_sha(token).hex()}")


def revoke_sessions(user_id: str, except_token: str | None = None) -> None:
    with db.system_tx() as conn:
        rows = conn.execute(
            "delete from sessions where user_id = %s and token_hash <> %s returning token_hash",
            (user_id, _sha(except_token) if except_token else b""),
        ).fetchall()
    for r in rows:
        get_cache().delete(f"sess:{bytes(r['token_hash']).hex()}")


# ---------------------------------------------------------------------------
# Email verification + password reset tokens
# ---------------------------------------------------------------------------
def _issue_token(user_id: str, purpose: str, ttl: timedelta) -> str:
    token = secrets.token_urlsafe(32)
    with db.system_tx() as conn:
        conn.execute(
            "update auth_tokens set used_at = now() where user_id = %s and purpose = %s and used_at is null",
            (user_id, purpose),
        )
        conn.execute(
            "insert into auth_tokens (token_hash, user_id, purpose, expires_at) values (%s, %s, %s, now() + %s)",
            (_sha(token), user_id, purpose, ttl),
        )
    return token


def _consume_token(token: str, purpose: str) -> dict | None:
    if not token or len(token) > 100:
        return None
    with db.system_tx() as conn:
        return conn.execute(
            "update auth_tokens t set used_at = now() from users u where t.token_hash = %s and t.purpose = %s "
            "and t.used_at is null and t.expires_at > now() and u.id = t.user_id "
            "returning u.id, u.email, u.email_verified_at",
            (_sha(token), purpose),
        ).fetchone()


def send_verification_email(user: AuthUser, base_url: str) -> bool:
    token = _issue_token(user.id, "verify_email", timedelta(hours=48))
    return emailer.send_verification(user.email, f"{base_url}/verify-email?token={token}")


def verify_email(token: str) -> AuthUser | None:
    row = _consume_token(token, "verify_email")
    if not row:
        return None
    with db.system_tx() as conn:
        conn.execute("update users set email_verified_at = coalesce(email_verified_at, now()) where id = %s", (row["id"],))
    return AuthUser(id=str(row["id"]), email=row["email"], email_verified=True)


def request_password_reset(email: str, base_url: str, ip: str = "") -> None:
    """Always behaves the same whether or not the account exists."""
    email = normalize_email(email)
    _rate_limit(f"rl:reset:ip:{ip}", 10, 60 * 60)
    _rate_limit(f"rl:reset:email:{email}", 3, 60 * 60)
    with db.system_tx() as conn:
        row = conn.execute("select id from users where email = %s", (email,)).fetchone()
    if row:
        token = _issue_token(str(row["id"]), "reset_password", timedelta(hours=1))
        emailer.send_password_reset(email, f"{base_url}/reset-password?token={token}")


def reset_password(token: str, new_password: str) -> AuthUser:
    if not (MIN_PASSWORD <= len(new_password) <= MAX_PASSWORD):
        raise AuthError(f"Password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters.")
    row = _consume_token(token, "reset_password")
    if not row:
        raise AuthError("This reset link is invalid or has expired. Request a new one.")
    user_id = str(row["id"])
    with db.system_tx() as conn:
        # Resetting through an emailed link also proves the email address
        conn.execute(
            "update users set password_hash = %s, email_verified_at = coalesce(email_verified_at, now()), "
            "updated_at = now() where id = %s",
            (_hasher.hash(new_password), user_id),
        )
    revoke_sessions(user_id)
    return AuthUser(id=user_id, email=row["email"], email_verified=True)


# ---------------------------------------------------------------------------
# HTTP integration
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def base_url(request: Request) -> str:
    return get_settings().app_base_url.rstrip("/") or str(request.base_url).rstrip("/")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=get_settings().session_days * 86400,
        httponly=True, secure=get_settings().cookie_secure, samesite="lax", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


async def start_session(request: Request, response: Response, user: AuthUser) -> None:
    token = await run_in_threadpool(
        create_session, user.id, request.headers.get("user-agent", ""), client_ip(request)
    )
    set_session_cookie(response, token)
    request.state.session_set = True


async def auth_middleware(request: Request, call_next):
    request.state.user = None
    if request.url.path.startswith("/static"):
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    lookup_failed = False
    if token:
        try:
            user = await run_in_threadpool(user_for_session, token)
        except db.DatabaseNotConfigured:
            raise
        except Exception:  # noqa: BLE001 - a DB hiccup must not log everyone out
            log.exception("session lookup failed")
            lookup_failed = True
    request.state.user = user
    request.state.session_token = token if user else None
    response = await call_next(request)
    if token and user is None and not lookup_failed and not getattr(request.state, "session_set", False):
        clear_session_cookie(response)
    return response


def current_user(request: Request) -> AuthUser:
    user = getattr(request.state, "user", None)
    if user is None:
        raise NotAuthenticated()
    return user


def get_repo(user: AuthUser = Depends(current_user)) -> Iterator[Repository]:
    repo = NeonRepository(user.id)
    try:
        yield repo
    finally:
        repo.close()
