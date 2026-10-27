"""Built-in food database (Indian foods first) with fast in-memory search."""

from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.nutrients import NUTRIENT_KEYS

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "indian_foods.csv"
MICROS_FILE = DATA_DIR / "indian_foods_micros.csv"

# CSV column -> nutrient key
_CSV_TO_NUTRIENT = {"kcal": "energy", **{k: k for k in NUTRIENT_KEYS if k not in ("energy", "water")}}

DIET_LABELS = {"V": "veg", "E": "egg", "N": "non-veg"}


@dataclass
class Food:
    id: str
    name: str
    category: str
    diet: str
    per_100g: dict[str, float]
    servings: list[dict] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    source: str = "builtin"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "diet": self.diet,
            "per_100g": self.per_100g,
            "servings": self.servings,
            "aliases": self.aliases,
            "source": self.source,
        }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def parse_servings(raw: str) -> list[dict]:
    servings = []
    for part in filter(None, (raw or "").split("|")):
        label, _, grams = part.rpartition("=")
        try:
            servings.append({"label": label.strip(), "grams": float(grams)})
        except ValueError:
            continue
    return servings


def estimate_water(per_100g: dict[str, float]) -> float:
    """Moisture per 100 g ≈ 100 − protein − carbohydrate − fat − ~1 g ash.

    Good enough for cooked Indian dishes and drinks (e.g. dal ≈ 78 g, chai ≈ 87 g),
    and lets drinks and food count toward total water intake like Cronometer.
    """
    protein, carbs, fat = per_100g.get("protein", 0), per_100g.get("carbs", 0), per_100g.get("fat", 0)
    # Energy not explained by macros comes from alcohol (7 kcal/g) in drinks like beer and spirits
    unexplained = per_100g.get("energy", 0) - (protein * 4 + carbs * 4 + fat * 9)
    alcohol = unexplained / 7 if unexplained > 20 else 0.0
    solids = protein + carbs + fat + alcohol + 1.0
    return round(max(0.0, 100.0 - solids), 1)


def _read_micros() -> dict[str, dict[str, str]]:
    if not MICROS_FILE.exists():
        return {}
    with MICROS_FILE.open(newline="", encoding="utf-8") as fh:
        return {row["id"]: row for row in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def load_foods() -> dict[str, Food]:
    foods: dict[str, Food] = {}
    micros = _read_micros()
    with DATA_FILE.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            row = {**micros.get(row["id"], {}), **row}
            per_100g = {}
            for col, key in _CSV_TO_NUTRIENT.items():
                value = (row.get(col) or "").strip()
                per_100g[key] = float(value) if value else 0.0
            per_100g["water"] = 100.0 if row["id"] == "water" else estimate_water(per_100g)
            foods[row["id"]] = Food(
                id=row["id"],
                name=row["name"],
                category=row["category"],
                diet=DIET_LABELS.get(row["diet"], "veg"),
                per_100g=per_100g,
                servings=parse_servings(row["servings"]),
                aliases=[a.strip() for a in row["aliases"].split("|") if a.strip()],
            )
    return foods


@lru_cache(maxsize=1)
def _index() -> list[tuple[str, str]]:
    """(normalized term, food id) pairs for names and aliases."""
    pairs = []
    for food in load_foods().values():
        for term in [food.name, *food.aliases]:
            pairs.append((_norm(term), food.id))
    return pairs


def get_food(food_id: str) -> Food | None:
    return load_foods().get(food_id)


def _score(query: str, term: str) -> float:
    if term == query:
        return 100
    if term.startswith(query):
        return 80
    words = term.split()
    if any(w.startswith(query) for w in words):
        return 70
    if query in term:
        return 60
    q_words = query.split()
    if q_words and all(any(w.startswith(q) for w in words) for q in q_words):
        return 55
    ratio = difflib.SequenceMatcher(None, query, term).ratio()
    if ratio > 0.72:
        return ratio * 50
    # word-level typo tolerance: "biriyani" ~ "biryani", "panir tika" ~ "paneer tikka"
    if q_words and all(
        any(difflib.SequenceMatcher(None, q, w).ratio() >= 0.75 for w in words) for q in q_words
    ):
        return 40
    return 0


def search(query: str, limit: int = 20) -> list[Food]:
    q = _norm(query)
    if not q:
        return []
    best: dict[str, float] = {}
    for term, food_id in _index():
        s = _score(q, term)
        if s > best.get(food_id, 0):
            best[food_id] = s
    foods = load_foods()
    # Prefer prepared dishes over raw ingredients when the match is equally good
    ranked = sorted(
        best.items(),
        key=lambda kv: (-(kv[1] - (3 if foods[kv[0]].category == "Ingredients" else 0)), foods[kv[0]].name),
    )
    return [foods[fid] for fid, _ in ranked[:limit]]


def best_match(name: str, min_score: float = 60) -> Food | None:
    """Best single match for an AI-recognised dish name, or None."""
    q = _norm(name)
    if not q:
        return None
    top_id, top = None, 0.0
    for term, food_id in _index():
        s = _score(q, term)
        if s > top:
            top_id, top = food_id, s
    return load_foods()[top_id] if top_id and top >= min_score else None


def catalog_for_prompt() -> str:
    """Compact `id: name` list given to the vision model so it can link dishes."""
    return "\n".join(f"{f.id}: {f.name}" for f in load_foods().values())
