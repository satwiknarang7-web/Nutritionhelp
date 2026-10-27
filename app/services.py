"""Cached read helpers shared by page and API routes.

Every write for a user bumps that user's cache version (see app.cache), which
invalidates all of their cached diary days, stats, goals and foods at once.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app import crypto, external_foods, food_db, insights
from app.cache import get_cache
from app.config import get_settings
from app.nutrients import age_on, suggest_goals
from app.repository import Repository
from app.stats import day_summary, merged_goals, range_summary


def invalidate(repo: Repository) -> None:
    get_cache().bump_user(repo.user_id)


def get_profile(repo: Repository) -> dict:
    cache = get_cache()
    key = cache.user_key(repo.user_id, "profile")
    return cache.get_or_set(key, 3600, lambda: repo.get_profile() or {"id": repo.user_id}) or {}


def get_goals(repo: Repository) -> dict[str, float]:
    cache = get_cache()
    key = cache.user_key(repo.user_id, "goals")
    return cache.get_or_set(key, 3600, lambda: merged_goals(repo.get_goals()))


def get_custom_foods(repo: Repository) -> list[dict]:
    cache = get_cache()
    key = cache.user_key(repo.user_id, "custom_foods")
    return cache.get_or_set(key, 3600, repo.list_custom_foods) or []


def get_day(repo: Repository, day: date) -> dict:
    cache = get_cache()
    key = cache.user_key(repo.user_id, "day", day.isoformat())
    return cache.get_or_set(
        key,
        get_settings().stats_cache_ttl,
        lambda: day_summary(_with_categories(repo.list_logs(day, day)), get_goals(repo)),
    )


def _with_categories(logs: list[dict]) -> list[dict]:
    """Attach the built-in food category (used for the food emoji in the UI)."""
    for entry in logs:
        food = food_db.get_food(entry.get("food_ref") or "")
        entry["category"] = food.category if food else None
    return logs


def get_range(repo: Repository, start: date, end: date) -> dict:
    cache = get_cache()
    key = cache.user_key(repo.user_id, "range", start.isoformat(), end.isoformat())
    return cache.get_or_set(
        key,
        get_settings().stats_cache_ttl,
        lambda: range_summary(_with_categories(repo.list_logs(start, end)), get_goals(repo), start, end),
    )


def suggested_goals(repo: Repository) -> dict[str, float]:
    p = get_profile(repo)
    dob = p.get("date_of_birth")
    return suggest_goals(
        sex=p.get("sex"),
        age=age_on(date.fromisoformat(dob)) if dob else None,
        height_cm=float(p["height_cm"]) if p.get("height_cm") else None,
        weight_kg=float(p["weight_kg"]) if p.get("weight_kg") else None,
        activity_level=p.get("activity_level"),
        goal=p.get("weight_goal"),
    )


def search_foods(repo: Repository, query: str, limit: int = 25) -> list[dict]:
    q = query.strip().lower()
    cache = get_cache()
    builtin = cache.get_or_set(
        f"foods:search:{q}",
        get_settings().food_search_cache_ttl,
        lambda: [f.to_dict() for f in food_db.search(q, limit)],
    )
    custom = [
        {**f, "source": "custom", "diet": None}
        for f in get_custom_foods(repo)
        if q in f["name"].lower()
    ]
    return (custom + builtin)[:limit]


def user_gemini_key(repo: Repository) -> str | None:
    row = repo.get_api_key("gemini")
    return crypto.decrypt(row["encrypted_key"]) if row else None


def resolve_food(repo: Repository, food_ref: str) -> dict | None:
    """Find a food by reference: built-in id, custom food uuid, `off:<barcode>` or `usda:<fdcId>`."""
    food = food_db.get_food(food_ref)
    if food:
        return food.to_dict()
    if food_ref.startswith(("off:", "usda:")):
        return external_foods.get(food_ref)
    try:
        uuid.UUID(food_ref)
    except ValueError:
        return None
    custom = repo.get_custom_food(food_ref)
    return {**custom, "source": "custom"} if custom else None


def logged_days(repo: Repository, start: date, end: date) -> dict[str, float]:
    cache = get_cache()
    key = cache.user_key(repo.user_id, "logged", start.isoformat(), end.isoformat())
    return cache.get_or_set(key, get_settings().stats_cache_ttl, lambda: repo.logged_days(start, end)) or {}


def day_view(repo: Repository, day: date) -> dict:
    """Everything the diary page shows: totals, meals, targets, score, tips, week strip, streak."""
    summary = get_day(repo, day)
    goals = get_goals(repo)
    today = date.today()
    monday = day - timedelta(days=day.weekday())
    logged = logged_days(repo, min(monday, today - timedelta(days=120)), max(monday + timedelta(days=6), today))
    entries = sum(len(m["entries"]) for m in summary["meals"].values())
    diet = get_profile(repo).get("diet_type")
    return {
        **summary,
        "score": insights.nutrient_score(summary["targets"]),
        "tips": insights.tips(summary["targets"], diet, entries > 0),
        "week": insights.week_strip(logged, day, goals.get("energy", 0)),
        "streak": insights.streak(logged, today),
    }
