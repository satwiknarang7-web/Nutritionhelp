"""NutriTrack - FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app import db
from app.auth import NotAuthenticated, auth_middleware
from app.routers import api, pages

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    db.close_pool()


app = FastAPI(title="NutriTrack", docs_url=None, redoc_url=None, lifespan=lifespan)


class CachedStaticFiles(StaticFiles):
    """Static assets are versioned by query string in templates, so cache them for a week."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=604800"
        return response


app.mount("/static", CachedStaticFiles(directory="app/static"), name="static")
app.middleware("http")(auth_middleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self)")
    if request.url.path.startswith("/api") or request.url.path in ("/diary", "/goals", "/profile", "/foods"):
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
