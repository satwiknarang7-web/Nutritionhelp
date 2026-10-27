"""JSON API used by the web UI."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app import ai, auth, crypto, emailer, external_foods, food_db, services
from app.auth import AuthUser, current_user, get_repo
from app.config import get_settings
from app.nutrients import NUTRIENTS, NUTRIENTS_BY_KEY, clean_nutrients, scale
from app.repository import Repository
from app.stats import WATER_REF, resolve_range


def require_ajax(request: Request) -> None:
    """CSRF guard: state-changing API calls must come from our own JS (custom header)."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "fetch":
        raise HTTPException(403, "Missing X-Requested-With header")


router = APIRouter(prefix="/api", dependencies=[Depends(require_ajax)])

Meal = Literal["breakfast", "lunch", "dinner", "snacks"]


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
@router.get("/nutrients")
def list_nutrients():
    return [
        {"key": n.key, "label": n.label, "unit": n.unit, "category": n.category, "kind": n.kind}
        for n in NUTRIENTS
    ]


# ---------------------------------------------------------------------------
# Foods
# ---------------------------------------------------------------------------
@router.get("/foods/search")
def search_foods(q: str = "", repo: Repository = Depends(get_repo)):
    if len(q.strip()) < 1:
        return []
    return services.search_foods(repo, q[:80])


@router.get("/foods/search/external")
def search_external(q: str = "", repo: Repository = Depends(get_repo)):
    """Packaged & global foods from Open Food Facts and USDA (slower, so searched separately)."""
    try:
        return external_foods.search(q)
    except external_foods.ExternalError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/foods/barcode/{code}")
def barcode_lookup(code: str, repo: Repository = Depends(get_repo)):
    try:
        food = external_foods.barcode(code)
    except external_foods.ExternalError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not food:
        raise HTTPException(404, "Product not found. Try searching by name or add it as your own food.")
    return food


@router.get("/foods/{food_ref}")
def get_food(food_ref: str, repo: Repository = Depends(get_repo)):
    food = _resolve(repo, food_ref)
    if not food:
        raise HTTPException(404, "Food not found")
    return food


def _resolve(repo: Repository, food_ref: str) -> dict | None:
    try:
        return services.resolve_food(repo, food_ref)
    except external_foods.ExternalError as exc:
        raise HTTPException(503, str(exc)) from exc


class ServingIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    grams: float = Field(gt=0, le=5000)


class CustomFoodIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    serving_label: str = Field(default="1 serving", max_length=60)
    serving_grams: float = Field(gt=0, le=5000)
    # Either the nutrients for one serving (as printed on a label) ...
    nutrients_per_serving: dict[str, float] | None = None
    # ... or per 100 g (e.g. saving an AI estimate)
    per_100g: dict[str, float] | None = None
    category: str = Field(default="My foods", max_length=40)
    extra_servings: list[ServingIn] = []


def _save_custom(repo: Repository, name: str, per_100g: dict, servings: list[dict], category: str) -> dict:
    per_100g = clean_nutrients(per_100g)
    if not per_100g.get("water"):
        per_100g["water"] = food_db.estimate_water(per_100g)
    food = repo.add_custom_food(
        {"name": name.strip(), "category": category, "per_100g": per_100g, "servings": servings}
    )
    services.invalidate(repo)
    return {**food, "source": "custom"}


@router.post("/foods/custom")
def create_custom_food(body: CustomFoodIn, repo: Repository = Depends(get_repo)):
    if body.per_100g is not None:
        per_100g = body.per_100g
    elif body.nutrients_per_serving is not None:
        per_serving = clean_nutrients(body.nutrients_per_serving)
        per_100g = {k: round(v * 100 / body.serving_grams, 4) for k, v in per_serving.items()}
    else:
        raise HTTPException(422, "Provide nutrients per serving or per 100 g.")
    servings = [{"label": body.serving_label, "grams": body.serving_grams}] + [
        s.model_dump() for s in body.extra_servings
    ]
    return _save_custom(repo, body.name, per_100g, servings, body.category)


class Ingredient(BaseModel):
    food_ref: str = Field(min_length=1, max_length=64)
    grams: float = Field(gt=0, le=20000)


class RecipeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ingredients: list[Ingredient] = Field(min_length=1, max_length=60)
    servings: float = Field(default=1, gt=0, le=200)
    # Weight after cooking (water lost or absorbed). Defaults to the sum of ingredients.
    cooked_weight_g: float | None = Field(default=None, gt=0, le=100000)


@router.post("/foods/recipe")
def create_recipe(body: RecipeIn, repo: Repository = Depends(get_repo)):
    """Build a custom food from ingredients - covers any home recipe."""
    totals = clean_nutrients({})
    raw_weight = 0.0
    for ing in body.ingredients:
        food = _resolve(repo, ing.food_ref)
        if not food:
            raise HTTPException(422, f"Unknown ingredient: {ing.food_ref}")
        for k, v in scale(food["per_100g"], ing.grams).items():
            totals[k] += v
        raw_weight += ing.grams
    cooked = body.cooked_weight_g or raw_weight
    # Cooking changes water content only: adjust water by the weight difference.
    totals["water"] = max(0.0, totals["water"] + (cooked - raw_weight))
    per_100g = {k: round(v * 100 / cooked, 4) for k, v in totals.items()}
    per_serving = round(cooked / body.servings, 1)
    servings = [{"label": "1 serving", "grams": per_serving}, {"label": "whole recipe", "grams": round(cooked, 1)}]
    return _save_custom(repo, body.name, per_100g, servings, "Recipes")


@router.delete("/foods/custom/{food_id}")
def delete_custom_food(food_id: str, repo: Repository = Depends(get_repo)):
    _validate_uuid(food_id)
    if not repo.delete_custom_food(food_id):
        raise HTTPException(404, "Food not found")
    services.invalidate(repo)
    return {"ok": True}


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _validate_uuid(value: str) -> None:
    if not _is_uuid(value):
        raise HTTPException(404, "Not found")


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------
@router.get("/diary")
def diary(day: date, repo: Repository = Depends(get_repo)):
    return services.day_view(repo, day)


class LogIn(BaseModel):
    logged_on: date
    meal: Meal
    food_ref: str | None = Field(default=None, max_length=64)
    food_name: str = Field(min_length=1, max_length=160)
    quantity: float = Field(gt=0, le=10000)
    unit: str = Field(min_length=1, max_length=60)
    grams: float = Field(gt=0, le=20000)
    source: Literal["manual", "photo", "text_ai", "custom"] = "manual"
    # Only used for AI-estimated items that are not in any database
    per_100g: dict[str, float] | None = None
    photo_id: str | None = Field(default=None, max_length=36)

    @field_validator("food_name", "unit")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


def _nutrients_for(repo: Repository, body: LogIn) -> tuple[dict, str | None, str]:
    """Compute entry nutrients server-side from trusted data where possible."""
    if body.food_ref:
        food = _resolve(repo, body.food_ref)
        if food:
            return scale(food["per_100g"], body.grams), body.food_ref, food["name"]
    if body.per_100g is None:
        raise HTTPException(422, "Unknown food. Pick one from search or provide nutrition values.")
    return scale(body.per_100g, body.grams), None, body.food_name


@router.post("/logs")
def add_log(body: LogIn, repo: Repository = Depends(get_repo)):
    nutrients, ref, name = _nutrients_for(repo, body)
    photo_id = body.photo_id if body.photo_id and _is_uuid(body.photo_id) and repo.owns_photo(body.photo_id) else None
    entry = repo.add_log(
        {
            "logged_on": body.logged_on.isoformat(),
            "meal": body.meal,
            "food_ref": ref,
            "food_name": name if ref else body.food_name,
            "source": body.source,
            "quantity": body.quantity,
            "unit": body.unit,
            "grams": round(body.grams, 2),
            "nutrients": nutrients,
            "photo_id": photo_id,
        }
    )
    services.invalidate(repo)
    return entry


class LogsBatchIn(BaseModel):
    entries: list[LogIn] = Field(min_length=1, max_length=30)


@router.post("/logs/batch")
def add_logs(body: LogsBatchIn, repo: Repository = Depends(get_repo)):
    return [add_log(entry, repo) for entry in body.entries]


class LogPatch(BaseModel):
    meal: Meal | None = None
    quantity: float | None = Field(default=None, gt=0, le=10000)
    unit: str | None = Field(default=None, min_length=1, max_length=60)
    grams: float | None = Field(default=None, gt=0, le=20000)


@router.patch("/logs/{log_id}")
def update_log(log_id: str, body: LogPatch, repo: Repository = Depends(get_repo)):
    _validate_uuid(log_id)
    current = repo.get_log(log_id)
    if not current:
        raise HTTPException(404, "Entry not found")
    fields = body.model_dump(exclude_none=True)
    if body.grams is not None:
        old_grams = float(current["grams"]) or 1.0
        factor = body.grams / old_grams
        fields["nutrients"] = {k: round(v * factor, 3) for k, v in clean_nutrients(current["nutrients"]).items()}
    updated = repo.update_log(log_id, fields)
    services.invalidate(repo)
    return updated


@router.delete("/logs/{log_id}")
def delete_log(log_id: str, repo: Repository = Depends(get_repo)):
    _validate_uuid(log_id)
    if not repo.delete_log(log_id):
        raise HTTPException(404, "Entry not found")
    services.invalidate(repo)
    return {"ok": True}


class CopyIn(BaseModel):
    from_day: date
    to_day: date
    meal: Meal | None = None


@router.post("/logs/copy")
def copy_logs(body: CopyIn, repo: Repository = Depends(get_repo)):
    """Copy a day's (or one meal's) entries to another day - handy for repeated meals."""
    source = [
        e for e in repo.list_logs(body.from_day, body.from_day)
        if e.get("food_ref") != WATER_REF and (body.meal is None or e["meal"] == body.meal)
    ]
    copied = []
    for e in source:
        copied.append(
            repo.add_log(
                {
                    "logged_on": body.to_day.isoformat(),
                    "meal": e["meal"],
                    "food_ref": e.get("food_ref"),
                    "food_name": e["food_name"],
                    "source": e.get("source", "manual"),
                    "quantity": e["quantity"],
                    "unit": e["unit"],
                    "grams": e["grams"],
                    "nutrients": e["nutrients"],
                }
            )
        )
    services.invalidate(repo)
    return {"copied": len(copied)}


# ---------------------------------------------------------------------------
# Water
# ---------------------------------------------------------------------------
class WaterIn(BaseModel):
    logged_on: date
    ml: float = Field(gt=0, le=5000)


@router.post("/water")
def add_water(body: WaterIn, repo: Repository = Depends(get_repo)):
    entry = repo.add_log(
        {
            "logged_on": body.logged_on.isoformat(),
            "meal": "snacks",
            "food_ref": WATER_REF,
            "food_name": "Water",
            "source": "manual",
            "quantity": body.ml,
            "unit": "ml",
            "grams": body.ml,
            "nutrients": {"water": body.ml},
        }
    )
    services.invalidate(repo)
    return entry


# ---------------------------------------------------------------------------
# AI: photo + text
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif"}


def _provider(repo: Repository) -> ai.Provider | None:
    return ai.resolve_provider(services.user_gemini_key(repo))


@router.post("/analyze/photo")
async def analyze_photo(
    photo: UploadFile = File(...),
    note: str = Form(""),
    save_photo: bool = Form(True),
    repo: Repository = Depends(get_repo),
):
    settings = get_settings()
    if photo.content_type and photo.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, "Please upload a JPEG, PNG or WebP photo.")
    data = await photo.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Photo is larger than {settings.max_upload_mb} MB.")
    if not data:
        raise HTTPException(400, "Empty upload.")

    def work():
        provider = _provider(repo)
        result = ai.analyze_photo(data, note, provider, repo.user_id)
        if save_photo and result.get("items"):
            thumb, mime = ai.prepare_image(data, max_side=1024, quality=80)
            result["photo_id"] = repo.save_photo(thumb, mime)
        return result

    try:
        return await run_in_threadpool(work)
    except ai.AIUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc


class TextIn(BaseModel):
    description: str = Field(min_length=2, max_length=1000)


@router.post("/analyze/text")
def analyze_text(body: TextIn, repo: Repository = Depends(get_repo)):
    try:
        return ai.analyze_text(body.description, _provider(repo), repo.user_id)
    except ai.AIUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/photos/{photo_id}")
def get_photo(photo_id: str, repo: Repository = Depends(get_repo)):
    _validate_uuid(photo_id)
    photo = repo.get_photo(photo_id)
    if not photo:
        raise HTTPException(404, "Not found")
    content, mime = photo
    return Response(content, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@router.get("/stats")
def stats(
    period: Literal["day", "week", "month", "year", "custom"] = "week",
    anchor: date | None = None,
    start: date | None = None,
    end: date | None = None,
    repo: Repository = Depends(get_repo),
):
    try:
        s, e = resolve_range(period, anchor or date.today(), start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return services.get_range(repo, s, e)


@router.get("/calendar")
def calendar(end: date | None = None, weeks: int = 26, repo: Repository = Depends(get_repo)):
    """Energy per logged day for a GitHub-style consistency calendar."""
    end = end or date.today()
    weeks = max(4, min(weeks, 53))
    start = end - timedelta(days=end.weekday()) - timedelta(weeks=weeks - 1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "goal": services.get_goals(repo).get("energy", 0),
        "days": services.logged_days(repo, start, end),
    }


# ---------------------------------------------------------------------------
# Goals & profile
# ---------------------------------------------------------------------------
@router.get("/goals")
def get_goals(repo: Repository = Depends(get_repo)):
    return services.get_goals(repo)


@router.put("/goals")
def put_goals(goals: dict[str, float], repo: Repository = Depends(get_repo)):
    clean = {k: round(max(0.0, float(v)), 2) for k, v in goals.items() if k in NUTRIENTS_BY_KEY}
    if not clean:
        raise HTTPException(422, "No valid goals supplied")
    repo.set_goals(clean)
    services.invalidate(repo)
    return services.get_goals(repo)


@router.get("/goals/suggested")
def suggested_goals(repo: Repository = Depends(get_repo)):
    return services.suggested_goals(repo)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    sex: Literal["male", "female", "other"] | None = None
    date_of_birth: date | None = None
    height_cm: float | None = Field(default=None, ge=50, le=260)
    weight_kg: float | None = Field(default=None, ge=20, le=350)
    activity_level: Literal["sedentary", "light", "moderate", "active", "very_active"] = "sedentary"
    weight_goal: Literal["lose", "maintain", "gain"] = "maintain"
    diet_type: Literal["veg", "egg", "non-veg", "vegan", "jain"] = "veg"
    timezone: str = Field(default="Asia/Kolkata", max_length=64)


@router.get("/profile")
def get_profile(repo: Repository = Depends(get_repo)):
    return services.get_profile(repo)


@router.put("/profile")
def put_profile(body: ProfileIn, repo: Repository = Depends(get_repo)):
    data = body.model_dump(mode="json")
    profile = repo.update_profile(data)
    services.invalidate(repo)
    return profile


# ---------------------------------------------------------------------------
# User's own Gemini API key
# ---------------------------------------------------------------------------
class KeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=200)


@router.get("/settings/ai")
def ai_settings(repo: Repository = Depends(get_repo)):
    row = repo.get_api_key("gemini")
    provider = _provider(repo)
    return {
        "has_user_key": bool(row),
        "key_hint": row["key_hint"] if row else None,
        "active_provider": provider.label if provider else None,
        "byok_available": crypto.encryption_available(),
    }


@router.put("/settings/ai/gemini")
def save_gemini_key(body: KeyIn, repo: Repository = Depends(get_repo)):
    if not crypto.encryption_available():
        raise HTTPException(503, "The server is not configured to store API keys (APP_ENCRYPTION_KEY missing).")
    key = body.api_key.strip()
    if not ai.validate_gemini_key(key):
        raise HTTPException(400, "Gemini rejected this API key. Check it at aistudio.google.com/apikey.")
    repo.set_api_key("gemini", crypto.encrypt(key), crypto.key_hint(key))
    return ai_settings(repo)


@router.delete("/settings/ai/gemini")
def delete_gemini_key(repo: Repository = Depends(get_repo)):
    repo.delete_api_key("gemini")
    return ai_settings(repo)



# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.get("/account")
def account(user: AuthUser = Depends(current_user)):
    return {"email": user.email, "email_verified": user.email_verified, "email_enabled": emailer.email_configured()}


@router.post("/account/password")
def change_password(body: PasswordChange, request: Request, user: AuthUser = Depends(current_user)):
    try:
        auth.change_password(user.id, body.current_password, body.new_password, keep_token=request.state.session_token)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.post("/account/verify-email")
def resend_verification(request: Request, user: AuthUser = Depends(current_user)):
    if user.email_verified:
        return {"sent": False, "detail": "Your email is already confirmed."}
    try:
        auth._rate_limit(f"rl:verify:{user.id}", 3, 3600)
    except auth.RateLimited as exc:
        raise HTTPException(429, str(exc)) from exc
    sent = auth.send_verification_email(user, auth.base_url(request))
    if not sent:
        raise HTTPException(503, "Email sending is not set up on this server.")
    return {"sent": True}


class DeleteAccount(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.post("/account/delete")
def delete_account(body: DeleteAccount, user: AuthUser = Depends(current_user)):
    try:
        auth.delete_account(user.id, body.password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = Response(status_code=204)
    auth.clear_session_cookie(response)
    return response
