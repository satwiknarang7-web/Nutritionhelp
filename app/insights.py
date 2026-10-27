"""Personal insights: nutrient score, streaks, week overview and food suggestions."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from app import food_db
from app.nutrients import DEFAULT_GOALS, NUTRIENTS_BY_KEY

# Nutrients that make up the score (energy and water have their own widgets)
SCORE_KEYS = [
    "protein", "fiber", "vit_a", "vit_b1", "vit_b2", "vit_b3", "vit_b6", "folate", "vit_b12",
    "vit_c", "vit_d", "vit_e", "calcium", "iron", "magnesium", "phosphorus", "zinc", "potassium",
]
LIMIT_KEYS = ["sodium", "sugar", "sat_fat", "cholesterol"]

TIP_PRIORITY = ["protein", "iron", "calcium", "vit_b12", "fiber", "vit_c", "folate", "vit_d", "zinc", "magnesium", "potassium"]

EXCLUDED_CATEGORIES = {"Supplements", "Ingredients", "Fats & Oils", "Condiments", "Beverages", "Fast Food", "Sweets"}
DIET_ALLOWED = {
    "veg": {"veg"},
    "jain": {"veg"},
    "vegan": {"veg"},
    "egg": {"veg", "egg"},
    "non-veg": {"veg", "egg", "non-veg"},
}
VEGAN_EXCLUDED = {"Dairy", "Sweets"}
DAIRY_WORDS = ("milk", "cheese", "paneer", "curd", "dahi", "ghee", "butter", "cream", "khoa", "lassi",
               "raita", "kheer", "yogurt", "malai", "kadhi", "chaas", "doi", "shrikhand", "kulfi")
JAIN_EXCLUDED_WORDS = ("aloo", "potato", "onion", "garlic", "carrot", "beetroot", "radish", "ginger", "arbi", "yam", "kanda")


def nutrient_score(targets: list[dict]) -> dict:
    """0-100 quality score: coverage of key nutrients minus penalties for exceeding limits."""
    by_key = {t["key"]: t for t in targets}
    covered = [min(100.0, by_key[k]["pct"] or 0) for k in SCORE_KEYS if by_key.get(k) and by_key[k]["target"]]
    score = sum(covered) / len(covered) if covered else 0.0
    for k in LIMIT_KEYS:
        t = by_key.get(k)
        if t and t["pct"] and t["pct"] > 100:
            score -= min(10.0, (t["pct"] - 100) / 5)
    score = max(0, round(score))
    label = "Excellent" if score >= 85 else "Good" if score >= 70 else "Fair" if score >= 50 else "Needs work"
    return {"value": score, "label": label}


def streak(logged: dict[str, float], today: date) -> int:
    """Consecutive days with food logged, ending today (or yesterday if today is still empty)."""
    day = today if today.isoformat() in logged else today - timedelta(days=1)
    count = 0
    while day.isoformat() in logged:
        count += 1
        day -= timedelta(days=1)
    return count


def week_strip(logged: dict[str, float], day: date, energy_goal: float) -> list[dict]:
    monday = day - timedelta(days=day.weekday())
    out = []
    for i in range(7):
        d = monday + timedelta(days=i)
        energy = logged.get(d.isoformat(), 0.0)
        out.append(
            {
                "date": d.isoformat(),
                "dow": d.strftime("%a"),
                "dom": d.day,
                "energy": round(energy),
                "pct": round(min(150.0, energy / energy_goal * 100)) if energy_goal else 0,
                "selected": d == day,
                "future": d > date.today(),
            }
        )
    return out


@lru_cache(maxsize=256)
def rich_sources(nutrient: str, diet: str = "non-veg", limit: int = 4) -> tuple[dict, ...]:
    """Built-in foods providing the most of a nutrient per typical serving, respecting diet."""
    allowed = DIET_ALLOWED.get(diet, DIET_ALLOWED["non-veg"])
    ranked = []
    for food in food_db.load_foods().values():
        if food.category in EXCLUDED_CATEGORIES or food.diet not in allowed:
            continue
        if diet == "vegan" and (
            food.category in VEGAN_EXCLUDED or any(w in food.name.lower() for w in DAIRY_WORDS)
        ):
            continue
        if diet == "jain" and any(w in (food.name + " " + " ".join(food.aliases)).lower() for w in JAIN_EXCLUDED_WORDS):
            continue
        serving = food.servings[0]
        amount = food.per_100g.get(nutrient, 0) * serving["grams"] / 100
        energy = food.per_100g.get("energy", 0) * serving["grams"] / 100
        # A serving must give a meaningful share of the daily target...
        if amount < 0.1 * DEFAULT_GOALS.get(nutrient, 0) or amount <= 0:
            continue
        # ...and we prefer nutrient-dense foods (more per calorie) over big portions of rich food
        density = amount / max(energy, 25)
        ranked.append((density, amount, food, serving))
    ranked.sort(key=lambda r: -r[0])
    seen: set[str] = set()
    out = []
    for _density, amount, food, serving in ranked:
        base = food.name.split("(")[0].strip().lower()
        if base in seen:
            continue
        seen.add(base)
        out.append({"id": food.id, "name": food.name, "category": food.category,
                    "serving": serving["label"], "amount": round(amount, 1)})
        if len(out) == limit:
            break
    return tuple(out)


def tips(targets: list[dict], diet: str | None, logged_anything: bool, max_tips: int = 3) -> list[dict]:
    """Actionable tips for the day: biggest nutrient gaps with Indian food suggestions, and limit warnings."""
    if not logged_anything:
        return [{
            "kind": "start", "icon": "🌅", "title": "Start your day's log",
            "body": "Snap a photo of your breakfast or search a dish - poha, idli, paratha, chai…",
            "foods": [],
        }]
    by_key = {t["key"]: t for t in targets}
    out = []
    for k in LIMIT_KEYS:
        t = by_key.get(k)
        if t and t["pct"] and t["pct"] > 100:
            out.append({
                "kind": "limit", "icon": "⚠️", "title": f"{t['label']} is over your limit",
                "body": f"{t['value']:g} {t['unit']} of a {t['target']:g} {t['unit']} limit. "
                + ("Pickles, papad, namkeen and restaurant gravies are common sources." if k == "sodium"
                   else "Sweets, sugary chai and cold drinks add up quickly." if k == "sugar"
                   else "Ghee, butter, cream-based gravies and fried snacks are common sources."),
                "foods": [],
            })
    gaps = sorted(
        (by_key[k] for k in TIP_PRIORITY if by_key.get(k) and by_key[k]["pct"] is not None and by_key[k]["pct"] < 60),
        key=lambda t: (t["pct"], TIP_PRIORITY.index(t["key"])),
    )
    for t in gaps:
        if len(out) >= max_tips:
            break
        meta = NUTRIENTS_BY_KEY[t["key"]]
        foods = list(rich_sources(t["key"], diet or "non-veg"))
        progress = f"{t['value']:g} of {t['target']:g} {t['unit']} so far ({round(t['pct'])}%)."
        if t["key"] == "vit_d":
            extra = " Few foods contain much - 15-20 minutes of midday sun and fortified milk or oil help."
        elif t["key"] == "vit_b12" and (diet or "") in ("veg", "vegan", "jain"):
            extra = " Vegetarians often run low - dairy helps, and many people need a supplement (ask your doctor)."
        else:
            extra = ""
        out.append({
            "kind": "gap", "icon": "💡", "nutrient": t["key"],
            "title": f"Low on {_nutrient_name(meta.label)}",
            "body": progress + extra + (" Good sources:" if foods else ""),
            "foods": foods,
            "unit": t["unit"],
        })
    if not out:
        out.append({"kind": "great", "icon": "🌟", "title": "Beautifully balanced",
                    "body": "You're on track for every key nutrient today. Keep it up!", "foods": []})
    return out[:max_tips]


def _nutrient_name(label: str) -> str:
    """'B12 (Cobalamin)' -> 'vitamin B12', 'Vitamin D' -> 'vitamin D', 'Iron' -> 'iron'."""
    name = label.split(" (")[0]
    if len(name) > 1 and name[0] == "B" and name[1].isdigit():
        return f"vitamin {name}"
    if name.startswith("Vitamin "):
        return "v" + name[1:]
    return name.lower()
