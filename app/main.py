"""NutriTrack - FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from app import db
from app.auth import NotAuthenticated, auth_middleware
from app.config import get_settings
from app.migrate import migrate
from app.routers import api, pages

logging.basicConfig(level=logging.INFO)


log = logging.getLogger("nutritrack")
APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if get_settings().should_auto_migrate:
        try:
            applied = await run_in_threadpool(migrate, None, False)
            if applied:
                log.info("applied migrations: %s", ", ".join(applied))
        except Exception:  # noqa: BLE001 - serve requests even if the migration check fails
            log.exception("automatic migration failed")
    yield
    db.close_pool()


app = FastAPI(title="NutriTrack", docs_url=None, redoc_url=None, lifespan=lifespan)


# Plain StaticFiles mount: on Vercel these files are promoted to the CDN at build time.
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.middleware("http")(auth_middleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self)")
    path = request.url.path
    if path.startswith("/static/") and response.status_code == 200:
        # assets are versioned by query string in templates
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif path.startswith("/api") or path in ("/diary", "/goals", "/profile", "/foods"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    return response


@app.exception_handler(NotAuthenticated)
async def not_authenticated(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Not signed in"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [
        f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()
    ]
    return JSONResponse({"detail": "; ".join(errors) or "Invalid input"}, status_code=422)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


app.include_router(api.router)
app.include_router(pages.router)
