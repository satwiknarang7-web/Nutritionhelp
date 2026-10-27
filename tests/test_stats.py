from datetime import date

import pytest

from app import stats
from app.nutrients import DEFAULT_GOALS, scale, suggest_goals


def log(day, meal, name, grams, **nutrients):
    return {"logged_on": day, "meal": meal, "food_name": name, "grams": grams, "nutrients": nutrients}


def test_scale_per_100g():
    out = scale({"energy": 200, "protein": 10}, 150)
    assert out["energy"] == 300 and out["protein"] == 15 and out["fat"] == 0


def test_suggest_goals_uses_profile():
    g = suggest_goals(sex="male", age=30, height_cm=175, weight_kg=70, activity_level="moderate", goal="maintain")
    # BMR = 700 + 1093.75 - 150 + 5 = 1648.75; x1.55 = 2555.6 -> 2560
    assert g["energy"] == 2560
    assert g["protein"] == round(70 * 0.83)
    assert g["water"] == 2400
    f = suggest_goals(sex="female", age=None, height_cm=None, weight_kg=None, activity_level=None, goal=None)
    assert f["iron"] == 29 and f["energy"] == 1660


def test_day_summary_groups_meals_and_water():
    logs = [
        log("2026-10-01", "breakfast", "Idli", 80, energy=108, protein=3.2, carbs=22.4, fat=0.4, water=50),
        log("2026-10-01", "lunch", "Dal", 150, energy=157, protein=8, carbs=20, fat=5, water=115),
        {**log("2026-10-01", "snacks", "Water", 500, water=500), "food_ref": "water"},
    ]
    s = stats.day_summary(logs, dict(DEFAULT_GOALS))
    assert s["totals"]["energy"] == 265
    assert s["totals"]["water"] == 665
    assert s["plain_water_ml"] == 500
    assert len(s["meals"]["breakfast"]["entries"]) == 1
    assert s["meals"]["snacks"]["entries"] == []  # water shown in the water card, not as a meal
    assert len(s["water_entries"]) == 1
    sodium = next(t for t in s["targets"] if t["key"] == "sodium")
    assert sodium["kind"] == "max"


def test_progress_status():
    rows = stats.nutrient_progress({"energy": 2000, "sodium": 2500}, {"energy": 2000, "sodium": 2000})
    by = {r["key"]: r for r in rows}
    assert by["energy"]["status"] == "ok"
    assert by["sodium"]["status"] == "over"
    assert by["iron"]["status"] == "none"


def test_range_summary_averages_over_logged_days():
    logs = [
        log("2026-10-01", "lunch", "Rice", 200, energy=1000, protein=50),
        log("2026-10-03", "lunch", "Rice", 200, energy=2000, protein=30),
    ]
    r = stats.range_summary(logs, {"energy": 2000, "protein": 40}, date(2026, 10, 1), date(2026, 10, 7))
    assert r["logged_days"] == 2 and r["days"] == 7
    assert r["averages"]["energy"] == 1500
    assert r["days_on_energy_target"] == 1
    assert r["days_protein_met"] == 1
    assert r["granularity"] == "day" and len(r["series"]) == 7
    assert r["top_foods"][0]["name"] == "Rice" and r["top_foods"][0]["count"] == 2


def test_range_granularity_for_long_periods():
    r = stats.range_summary([], {}, date(2025, 10, 1), date(2026, 9, 30))
    assert r["granularity"] == "month" and len(r["series"]) == 12


@pytest.mark.parametrize(
    "period,expected_days", [("day", 1), ("week", 7), ("month", 30), ("year", 365)]
)
def test_resolve_range(period, expected_days):
    s, e = stats.resolve_range(period, date(2026, 10, 27))
    assert e == date(2026, 10, 27) and (e - s).days + 1 == expected_days


def test_resolve_custom_range_swaps_and_caps():
    s, e = stats.resolve_range("custom", date.today(), date(2026, 10, 10), date(2026, 10, 1))
    assert (s, e) == (date(2026, 10, 1), date(2026, 10, 10))
    s, e = stats.resolve_range("custom", date.today(), date(2000, 1, 1), date(2026, 1, 1))
    assert (e - s).days < stats.MAX_RANGE_DAYS
    with pytest.raises(ValueError):
        stats.resolve_range("custom", date.today())


def test_insights_score_streak_and_suggestions():
    from datetime import date

    from app import insights

    empty = insights.nutrient_score(stats.nutrient_progress({"sodium": 3000}, dict(DEFAULT_GOALS)))
    assert empty["value"] == 0 and empty["label"] == "Needs work"
    targets = stats.nutrient_progress({**DEFAULT_GOALS, "iron": 2, "sodium": 3000}, dict(DEFAULT_GOALS))
    score = insights.nutrient_score(targets)
    assert 80 <= score["value"] < 100
    tips = insights.tips(targets, "veg", True)
    assert tips[0]["kind"] == "limit" and "Sodium" in tips[0]["title"]
    iron_tip = next(t for t in tips if t.get("nutrient") == "iron")
    assert iron_tip["title"] == "Low on iron" and iron_tip["foods"]
    veg_sources = insights.rich_sources("protein", "veg", 10)
    assert all(not any(w in f["name"].lower() for w in ("chicken", "mutton", "fish", "egg")) for f in veg_sources)
    assert insights.tips(targets, "veg", False)[0]["kind"] == "start"
    logged = {"2026-10-27": 1, "2026-10-26": 1, "2026-10-24": 1}
    assert insights.streak(logged, date(2026, 10, 27)) == 2
    assert insights.streak(logged, date(2026, 10, 28)) == 2  # today not logged yet
    week = insights.week_strip(logged, date(2026, 10, 27), 2000)
    assert [d["dow"] for d in week][0] == "Mon" and sum(d["selected"] for d in week) == 1


def test_blank_env_values_fall_back_to_defaults(tmp_path, monkeypatch):
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_text("SMTP_PORT=\nCOOKIE_SECURE=\nREQUIRE_EMAIL_VERIFICATION=\nGEMINI_MODEL=\nUSDA_API_KEY=\n")
    for key in ("SMTP_PORT", "COOKIE_SECURE", "REQUIRE_EMAIL_VERIFICATION", "GEMINI_MODEL", "USDA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=env)
    assert s.smtp_port == 587 and s.cookie_secure is True and s.require_email_verification is False
    assert s.gemini_model == "gemini-2.5-flash" and s.usda_api_key == "DEMO_KEY"
