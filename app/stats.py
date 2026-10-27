"""Aggregation of diary entries into daily totals, range summaries and chart series."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.nutrients import (
    DEFAULT_GOALS,
    KCAL_PER_G,
    NUTRIENTS,
    NUTRIENTS_BY_KEY,
    add_into,
    empty_totals,
)

MEALS = ["breakfast", "lunch", "dinner", "snacks"]
WATER_REF = "water"
MAX_RANGE_DAYS = 3 * 366


def resolve_range(period: str, anchor: date, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    """Map a period name to an inclusive date range ending on `anchor`."""
    if period == "day":
        return anchor, anchor
    if period == "week":
        return anchor - timedelta(days=6), anchor
    if period == "month":
        return anchor - timedelta(days=29), anchor
    if period == "year":
        return anchor - timedelta(days=364), anchor
    if period == "custom" and start and end:
        if start > end:
            start, end = end, start
        if (end - start).days >= MAX_RANGE_DAYS:
            start = end - timedelta(days=MAX_RANGE_DAYS - 1)
        return start, end
    raise ValueError("Unknown period or missing custom dates")


def merged_goals(saved: dict[str, float]) -> dict[str, float]:
    return {**DEFAULT_GOALS, **{k: v for k, v in saved.items() if k in NUTRIENTS_BY_KEY}}


def day_summary(logs: list[dict], goals: dict[str, float]) -> dict:
    totals = empty_totals()
    meals: dict[str, dict] = {m: {"entries": [], "totals": empty_totals()} for m in MEALS}
    water_entries = []
    for entry in logs:
        add_into(totals, entry.get("nutrients"))
        if entry.get("food_ref") == WATER_REF:
            water_entries.append(entry)
            continue
        meal = meals.get(entry.get("meal"), meals["snacks"])
        meal["entries"].append(entry)
        add_into(meal["totals"], entry.get("nutrients"))
    return {
        "totals": _round(totals),
        "meals": {m: {"entries": v["entries"], "totals": _round(v["totals"])} for m, v in meals.items()},
        "targets": nutrient_progress(totals, goals),
        "macro_split": macro_split(totals),
        "plain_water_ml": plain_water_ml(logs),
        "water_entries": water_entries,
    }


def nutrient_progress(values: dict[str, float], goals: dict[str, float]) -> list[dict]:
    rows = []
    for n in NUTRIENTS:
        target = goals.get(n.key) or 0
        value = values.get(n.key, 0.0)
        pct = (value / target * 100) if target else None
        if pct is None:
            status = "none"
        elif n.kind == "max":
            status = "over" if pct > 100 else ("near" if pct >= 85 else "ok")
        else:
            status = "ok" if pct >= 90 else ("near" if pct >= 60 else "low")
        rows.append(
            {
                "key": n.key,
                "label": n.label,
                "unit": n.unit,
                "category": n.category,
                "kind": n.kind,
                "value": round(value, 1),
                "target": target,
                "pct": round(pct, 1) if pct is not None else None,
                "status": status,
            }
        )
    return rows


def macro_split(totals: dict[str, float]) -> dict[str, float]:
    kcal = {k: totals.get(k, 0.0) * f for k, f in KCAL_PER_G.items()}
    total = sum(kcal.values())
    if not total:
        return {k: 0.0 for k in KCAL_PER_G}
    return {k: round(v / total * 100, 1) for k, v in kcal.items()}


def plain_water_ml(logs: list[dict]) -> float:
    """Plain drinking water logged via the water tracker (total water also includes drinks and food)."""
    return round(sum(float(e.get("grams") or 0) for e in logs if e.get("food_ref") == WATER_REF), 0)


def _round(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 1) for k, v in d.items()}


def _bucket_key(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def _granularity(days: int) -> str:
    if days <= 62:
        return "day"
    if days <= 200:
        return "week"
    return "month"


def range_summary(logs: list[dict], goals: dict[str, float], start: date, end: date) -> dict:
    """Totals, per-day averages, goal adherence, chart series, top foods."""
    n_days = (end - start).days + 1
    per_day: dict[date, dict[str, float]] = defaultdict(empty_totals)
    meal_totals = {m: 0.0 for m in MEALS}
    foods: dict[str, dict] = {}
    totals = empty_totals()

    for entry in logs:
        day = entry["logged_on"]
        day = date.fromisoformat(day) if isinstance(day, str) else day
        nutrients = entry.get("nutrients") or {}
        add_into(per_day[day], nutrients)
        add_into(totals, nutrients)
        meal = entry.get("meal") if entry.get("meal") in meal_totals else "snacks"
        meal_totals[meal] += float(nutrients.get("energy") or 0)
        if entry.get("food_ref") == WATER_REF:
            continue
        name = entry.get("food_name") or "Unknown"
        f = foods.setdefault(
            name, {"name": name, "category": entry.get("category"), "count": 0, "energy": 0.0, "protein": 0.0}
        )
        f["count"] += 1
        f["energy"] += float(nutrients.get("energy") or 0)
        f["protein"] += float(nutrients.get("protein") or 0)

    logged_days = len(per_day)
    averages = {k: (v / logged_days if logged_days else 0.0) for k, v in totals.items()}

    # Goal adherence: share of logged days within target
    energy_goal = goals.get("energy") or 0
    days_on_target = sum(
        1 for t in per_day.values() if energy_goal and abs(t["energy"] - energy_goal) <= 0.1 * energy_goal
    )
    protein_goal = goals.get("protein") or 0
    days_protein_met = sum(1 for t in per_day.values() if protein_goal and t["protein"] >= protein_goal)

    granularity = _granularity(n_days)
    buckets: dict[date, dict] = {}
    day = start
    while day <= end:
        b = buckets.setdefault(_bucket_key(day, granularity), {"totals": empty_totals(), "logged": 0})
        if day in per_day:
            add_into(b["totals"], per_day[day])
            b["logged"] += 1
        day += timedelta(days=1)

    series = []
    for bucket_start, b in sorted(buckets.items()):
        # For week/month buckets plot the per-logged-day average so values compare with daily goals
        div = b["logged"] if (granularity != "day" and b["logged"]) else 1
        series.append(
            {
                "date": bucket_start.isoformat(),
                "logged": b["logged"] > 0,
                **{k: round(v / div, 1) for k, v in b["totals"].items()},
            }
        )

    top_foods = sorted(foods.values(), key=lambda f: -f["energy"])[:10]
    for f in top_foods:
        f["energy"] = round(f["energy"])
        f["protein"] = round(f["protein"], 1)

    meal_total = sum(meal_totals.values())
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": n_days,
        "logged_days": logged_days,
        "granularity": granularity,
        "totals": _round(totals),
        "averages": _round(averages),
        "targets": nutrient_progress(averages, goals),
        "macro_split": macro_split(averages),
        "meal_split": {m: (round(v / meal_total * 100, 1) if meal_total else 0.0) for m, v in meal_totals.items()},
        "days_on_energy_target": days_on_target,
        "days_protein_met": days_protein_met,
        "plain_water_ml_avg": round(plain_water_ml(logs) / logged_days) if logged_days else 0,
        "series": series,
        "top_foods": top_foods,
        "goals": goals,
    }
