"""Supabase Auth integration.

Sessions live in two httpOnly cookies (access + refresh token). A middleware
validates the access token on each request (cached briefly), transparently
refreshes expired sessions, and exposes `request.state.user`.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from supabase import Client, ClientOptions, create_client

from app.cache import get_cache, hash_key
from app.config import Settings, get_settings
from app.repository import Repository, SupabaseRepository

log = logging.getLogger(__name__)

ACCESS_COOKIE = "nt_access"
REFRESH_COOKIE = "nt_refresh"
REFRESH_MAX_AGE = 30 * 24 * 3600


@dataclass
class AuthUser:
    id: str
    email: str


@dataclass
class SessionTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user: AuthUser


class NotAuthenticated(Exception):
    pass


class AuthError(Exception):
    pass


def _client(settings: Settings | None = None) -> Client:
    """A fresh, stateless Supabase client (no shared session between users)."""
    settings = settings or get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise AuthError("Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY.")
    return create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options=ClientOptions(persist_session=False, auto_refresh_token=False, flow_type="implicit"),
    )


def _tokens(session, user) -> SessionTokens:
    return SessionTokens(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in or 3600,
        user=AuthUser(id=user.id, email=user.email or ""),
    )


def _message(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc) or "Authentication failed"


def sign_up(email: str, password: str, display_name: str, redirect_to: str | None = None) -> SessionTokens | None:
    """Returns tokens when email confirmation is off, None when the user must confirm by email."""
    options: dict = {"data": {"display_name": display_name}}
    if redirect_to:
        options["email_redirect_to"] = redirect_to
    try:
        res = _client().auth.sign_up({"email": email, "password": password, "options": options})
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - supabase raises several auth error types
        raise AuthError(_message(exc)) from exc
    if res.session and res.user:
        return _tokens(res.session, res.user)
    return None


def sign_in(email: str, password: str) -> SessionTokens:
    try:
        res = _client().auth.sign_in_with_password({"email": email, "password": password})
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AuthError(_message(exc)) from exc
    if not res.session or not res.user:
        raise AuthError("Invalid login credentials")
    return _tokens(res.session, res.user)


def send_password_reset(email: str, redirect_to: str) -> None:
    try:
        _client().auth.reset_password_for_email(email, {"redirect_to": redirect_to})
    except Exception:  # noqa: BLE001 - never reveal whether the email exists
        log.info("password reset request failed", exc_info=True)


def update_password(access_token: str, refresh_token: str, new_password: str) -> None:
    client = _client()
    try:
        client.auth.set_session(access_token, refresh_token)
        client.auth.update_user({"password": new_password})
    except Exception as exc:  # noqa: BLE001
        raise AuthError(_message(exc)) from exc


def sign_out(access_token: str) -> None:
    try:
        _client().auth.admin.sign_out(access_token)
    except Exception:  # noqa: BLE001 - cookie removal is what matters
        log.debug("remote sign-out failed", exc_info=True)


def refresh(refresh_token: str) -> SessionTokens | None:
    try:
        res = _client().auth.refresh_session(refresh_token)
    except Exception:  # noqa: BLE001
        return None
    if res.session and res.user:
        return _tokens(res.session, res.user)
    return None


def _jwt_exp(token: str) -> int | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:  # noqa: BLE001
        return None


def verify_access_token(token: str) -> AuthUser | None:
    """Validate a token with Supabase Auth, caching the result until shortly before expiry."""
    exp = _jwt_exp(token)
    now = int(time.time())
    if exp is not None and exp <= now + 5:
        return None  # expired: let the caller refresh

    settings = get_settings()
    cache = get_cache()
    key = f"auth:{hash_key(token)}"
    cached = cache.get(key)
    if cached:
        return AuthUser(**cached)

    try:
        res = _client(settings).auth.get_user(token)
    except Exception:  # noqa: BLE001
        return None
    if not res or not res.user:
        return None
    user = AuthUser(id=res.user.id, email=res.user.email or "")
    ttl = settings.user_cache_ttl
    if exp is not None:
        ttl = max(1, min(ttl, exp - now - 5))
    cache.set(key, {"id": user.id, "email": user.email}, ttl)
    return user


def set_session_cookies(response: Response, tokens: SessionTokens) -> None:
    secure = get_settings().cookie_secure
    response.set_cookie(
        ACCESS_COOKIE, tokens.access_token, max_age=tokens.expires_in,
        httponly=True, secure=secure, samesite="lax", path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, tokens.refresh_token, max_age=REFRESH_MAX_AGE,
        httponly=True, secure=secure, samesite="lax", path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


async def auth_middleware(request: Request, call_next):
    request.state.user = None
    request.state.access_token = None
    if request.url.path.startswith("/static"):
        return await call_next(request)

    access = request.cookies.get(ACCESS_COOKIE)
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    user = await run_in_threadpool(verify_access_token, access) if access else None
    new_tokens: SessionTokens | None = None
    if user is None and refresh_token:
        new_tokens = await run_in_threadpool(refresh, refresh_token)
        if new_tokens:
            user, access = new_tokens.user, new_tokens.access_token

    request.state.user = user
    request.state.access_token = access if user else None
    response = await call_next(request)

    if new_tokens:
        set_session_cookies(response, new_tokens)
    elif user is None and (access or refresh_token) and not getattr(request.state, "session_set", False):
        clear_session_cookies(response)
    return response


def current_user(request: Request) -> AuthUser:
    user = getattr(request.state, "user", None)
    if user is None:
        raise NotAuthenticated()
    return user


def get_repo(
    request: Request, user: AuthUser = Depends(current_user)
) -> Iterator[Repository]:
    repo = SupabaseRepository(get_settings(), user.id, request.state.access_token)
    try:
        yield repo
    finally:
        repo.close()
