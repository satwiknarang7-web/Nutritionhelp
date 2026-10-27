"""Server-rendered pages and auth form handlers."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import auth, services
from app.auth import AuthUser, current_user, get_repo
from app.nutrients import CATEGORIES, NUTRIENTS
from app.repository import Repository
from app.stats import MEALS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals.update(NUTRIENTS=NUTRIENTS, CATEGORIES=CATEGORIES, MEALS=MEALS)


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"user": getattr(request.state, "user", None), **ctx})


def _parse_day(value: str | None) -> date:
    try:
        return date.fromisoformat(value) if value else date.today()
    except ValueError:
        return date.today()


# ---------------------------------------------------------------------------
# App pages (login required)
# ---------------------------------------------------------------------------
@router.get("/", include_in_schema=False)
def home(request: Request):
    target = "/diary" if getattr(request.state, "user", None) else "/login"
    return RedirectResponse(target, status_code=303)


@router.get("/diary", response_class=HTMLResponse)
def diary_page(request: Request, d: str | None = None, user: AuthUser = Depends(current_user),
               repo: Repository = Depends(get_repo)):
    day = _parse_day(d)
    return _render(
        request, "diary.html",
        day=day, prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
        summary=services.get_day(repo, day), profile=services.get_profile(repo), active="diary",
    )


@router.get("/trends", response_class=HTMLResponse)
def trends_page(request: Request, user: AuthUser = Depends(current_user)):
    return _render(request, "trends.html", today=date.today(), active="trends")


@router.get("/goals", response_class=HTMLResponse)
def goals_page(request: Request, user: AuthUser = Depends(current_user), repo: Repository = Depends(get_repo)):
    return _render(request, "goals.html", goals=services.get_goals(repo), active="goals")


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, user: AuthUser = Depends(current_user), repo: Repository = Depends(get_repo)):
    return _render(request, "profile.html", profile=services.get_profile(repo), active="profile")


@router.get("/foods", response_class=HTMLResponse)
def foods_page(request: Request, user: AuthUser = Depends(current_user), repo: Repository = Depends(get_repo)):
    return _render(request, "foods.html", custom_foods=services.get_custom_foods(repo), active="foods")


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str | None = None):
    if getattr(request.state, "user", None):
        return RedirectResponse("/diary", status_code=303)
    return _render(request, "login.html", message=msg)


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        tokens = await run_in_threadpool(auth.sign_in, email.strip(), password)
    except auth.AuthError as exc:
        return _render(request, "login.html", error=str(exc), email=email)
    response = RedirectResponse("/diary", status_code=303)
    auth.set_session_cookies(response, tokens)
    request.state.session_set = True
    return response


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return _render(request, "signup.html")


@router.post("/signup")
async def signup(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
):
    if len(password) < 8:
        return _render(request, "signup.html", error="Password must be at least 8 characters.",
                       email=email, display_name=display_name)
    callback = str(request.url_for("auth_callback"))
    try:
        tokens = await run_in_threadpool(auth.sign_up, email.strip(), password, display_name.strip(), callback)
    except auth.AuthError as exc:
        return _render(request, "signup.html", error=str(exc), email=email, display_name=display_name)
    if tokens is None:
        return RedirectResponse("/login?msg=Check+your+email+to+confirm+your+account.", status_code=303)
    response = RedirectResponse("/profile?welcome=1", status_code=303)
    auth.set_session_cookies(response, tokens)
    request.state.session_set = True
    return response


@router.post("/logout")
async def logout(request: Request):
    token = getattr(request.state, "access_token", None)
    if token:
        await run_in_threadpool(auth.sign_out, token)
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookies(response)
    request.state.session_set = True
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_page(request: Request):
    return _render(request, "forgot.html")


@router.post("/forgot-password")
async def forgot(request: Request, email: str = Form(...)):
    callback = str(request.url_for("auth_callback"))
    await run_in_threadpool(auth.send_password_reset, email.strip(), callback)
    return _render(request, "forgot.html", sent=True)


@router.get("/auth/callback", response_class=HTMLResponse, name="auth_callback")
def auth_callback(request: Request):
    """Supabase email links land here with tokens in the URL fragment; JS posts them to /auth/session."""
    return _render(request, "callback.html")


@router.post("/auth/session")
async def auth_session(request: Request):
    if request.headers.get("x-requested-with") != "fetch":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not access or not refresh:
        return JSONResponse({"error": "Missing tokens"}, status_code=400)
    user = await run_in_threadpool(auth.verify_access_token, access)
    if not user:
        return JSONResponse({"error": "Invalid or expired link"}, status_code=400)
    response = JSONResponse({"ok": True})
    auth.set_session_cookies(
        response, auth.SessionTokens(access, refresh, int(body.get("expires_in") or 3600), user)
    )
    request.state.session_set = True
    return response


@router.post("/auth/reset-password")
async def reset_password(request: Request):
    if request.headers.get("x-requested-with") != "fetch":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    password = body.get("password") or ""
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters."}, status_code=400)
    try:
        await run_in_threadpool(auth.update_password, body.get("access_token", ""), body.get("refresh_token", ""), password)
    except auth.AuthError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True})
