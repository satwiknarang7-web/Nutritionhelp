"""Server-rendered pages and auth form handlers."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import auth, emailer, services
from app.config import get_settings
from app.auth import AuthUser, current_user, get_repo
from app.nutrients import CATEGORIES, NUTRIENTS
from app.repository import NeonRepository, Repository
from app.stats import MEALS

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.globals.update(NUTRIENTS=NUTRIENTS, CATEGORIES=CATEGORIES, MEALS=MEALS)


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    user = getattr(request.state, "user", None)
    display_name = None
    if user is not None:
        try:
            display_name = services.get_profile(NeonRepository(user.id)).get("display_name")
        except Exception:  # noqa: BLE001 - the page should still render
            display_name = None
    return templates.TemplateResponse(request, name, {"user": user, "display_name": display_name, **ctx})


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
        summary=services.day_view(repo, day), profile=services.get_profile(repo), active="diary",
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
        user = await run_in_threadpool(auth.authenticate, email, password, auth.client_ip(request))
    except auth.AuthError as exc:
        return _render(request, "login.html", error=str(exc), email=email)
    response = RedirectResponse("/diary", status_code=303)
    await auth.start_session(request, response, user)
    return response


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse("/diary", status_code=303)
    return _render(request, "signup.html")


@router.post("/signup")
async def signup(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
):
    form = {"email": email, "display_name": display_name}
    try:
        await run_in_threadpool(auth._rate_limit, f"rl:signup:ip:{auth.client_ip(request)}", 10, 3600)
        user = await run_in_threadpool(auth.create_user, email, password, display_name.strip())
    except auth.AuthError as exc:
        return _render(request, "signup.html", error=str(exc), **form)
    sent = await run_in_threadpool(auth.send_verification_email, user, auth.base_url(request))
    if get_settings().require_email_verification and sent:
        return RedirectResponse("/login?msg=Check+your+email+to+confirm+your+account.", status_code=303)
    response = RedirectResponse("/profile?welcome=1", status_code=303)
    await auth.start_session(request, response, user)
    return response


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        await run_in_threadpool(auth.destroy_session, token)
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(response)
    request.state.session_set = True
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_page(request: Request):
    return _render(request, "forgot.html", email_enabled=emailer.email_configured())


@router.post("/forgot-password")
async def forgot(request: Request, email: str = Form(...)):
    try:
        await run_in_threadpool(auth.request_password_reset, email, auth.base_url(request), auth.client_ip(request))
    except auth.RateLimited as exc:
        return _render(request, "forgot.html", error=str(exc), email_enabled=True)
    return _render(request, "forgot.html", sent=True, email_enabled=True)


@router.get("/reset-password", response_class=HTMLResponse)
def reset_page(request: Request, token: str = ""):
    return _render(request, "reset.html", token=token)


@router.post("/reset-password")
async def reset_password(request: Request, token: str = Form(...), password: str = Form(...)):
    try:
        user = await run_in_threadpool(auth.reset_password, token, password)
    except auth.AuthError as exc:
        return _render(request, "reset.html", token=token, error=str(exc))
    response = RedirectResponse("/diary", status_code=303)
    await auth.start_session(request, response, user)
    return response


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(request: Request, token: str = ""):
    user = await run_in_threadpool(auth.verify_email, token)
    if not user:
        return _render(request, "login.html", error="That confirmation link is invalid or has expired.")
    return RedirectResponse("/login?msg=Email+confirmed+-+welcome+to+NutriTrack!" if not getattr(
        request.state, "user", None) else "/diary", status_code=303)
