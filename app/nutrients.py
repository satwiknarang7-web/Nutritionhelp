"""Nutrient registry, default targets and goal suggestions.

Default targets follow ICMR-NIN "Nutrient Requirements for Indians" (2020) for a
sedentary adult, with WHO limits for sodium, sugar and saturated fat. Water is
total water (plain water + drinks + moisture in food), as in Cronometer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

GoalKind = Literal["min", "max"]


@dataclass(frozen=True)
class Nutrient:
    key: str
    label: str
    unit: str
    category: str
    kind: GoalKind  # "min": aim to reach it, "max": stay under it


NUTRIENTS: list[Nutrient] = [
    Nutrient("energy", "Energy", "kcal", "General", "min"),
    Nutrient("water", "Water", "ml", "General", "min"),
    Nutrient("protein", "Protein", "g", "Macronutrients", "min"),
    Nutrient("carbs", "Carbohydrates", "g", "Macronutrients", "min"),
    Nutrient("fiber", "Fiber", "g", "Macronutrients", "min"),
    Nutrient("sugar", "Sugars", "g", "Macronutrients", "max"),
    Nutrient("fat", "Fat", "g", "Lipids", "min"),
    Nutrient("sat_fat", "Saturated fat", "g", "Lipids", "max"),
    Nutrient("cholesterol", "Cholesterol", "mg", "Lipids", "max"),
    Nutrient("vit_a", "Vitamin A", "µg", "Vitamins", "min"),
    Nutrient("vit_b1", "B1 (Thiamine)", "mg", "Vitamins", "min"),
    Nutrient("vit_b2", "B2 (Riboflavin)", "mg", "Vitamins", "min"),
    Nutrient("vit_b3", "B3 (Niacin)", "mg", "Vitamins", "min"),
    Nutrient("vit_b6", "B6 (Pyridoxine)", "mg", "Vitamins", "min"),
    Nutrient("folate", "Folate (B9)", "µg", "Vitamins", "min"),
    Nutrient("vit_b12", "B12 (Cobalamin)", "µg", "Vitamins", "min"),
    Nutrient("vit_c", "Vitamin C", "mg", "Vitamins", "min"),
    Nutrient("vit_d", "Vitamin D", "µg", "Vitamins", "min"),
    Nutrient("vit_e", "Vitamin E", "mg", "Vitamins", "min"),
    Nutrient("calcium", "Calcium", "mg", "Minerals", "min"),
    Nutrient("iron", "Iron", "mg", "Minerals", "min"),
    Nutrient("magnesium", "Magnesium", "mg", "Minerals", "min"),
    Nutrient("phosphorus", "Phosphorus", "mg", "Minerals", "min"),
    Nutrient("zinc", "Zinc", "mg", "Minerals", "min"),
    Nutrient("potassium", "Potassium", "mg", "Minerals", "min"),
    Nutrient("sodium", "Sodium", "mg", "Minerals", "max"),
]

NUTRIENT_KEYS: list[str] = [n.key for n in NUTRIENTS]
NUTRIENTS_BY_KEY: dict[str, Nutrient] = {n.key: n for n in NUTRIENTS}
CATEGORIES: list[str] = list(dict.fromkeys(n.category for n in NUTRIENTS))

# kcal per gram, used for macro split
KCAL_PER_G = {"protein": 4.0, "carbs": 4.0, "fat": 9.0}

DEFAULT_GOALS: dict[str, float] = {
    "energy": 2000,
    "water": 2500,
    "protein": 54,
    "carbs": 260,
    "fiber": 30,
    "sugar": 50,
    "fat": 60,
    "sat_fat": 20,
    "cholesterol": 300,
    "vit_a": 1000,
    "vit_b1": 1.4,
    "vit_b2": 2.0,
    "vit_b3": 14,
    "vit_b6": 1.9,
    "folate": 300,
    "vit_b12": 2.5,
    "vit_c": 80,
    "vit_d": 15,
    "vit_e": 10,
    "calcium": 1000,
    "iron": 19,
    "magnesium": 440,
    "phosphorus": 1000,
    "zinc": 17,
    "potassium": 3500,
    "sodium": 2000,
}

# ICMR-NIN 2020 RDA differences for adult women (sedentary)
FEMALE_GOALS: dict[str, float] = {
    "energy": 1660,
    "water": 2000,
    "protein": 46,
    "fiber": 25,
    "vit_a": 840,
    "vit_b2": 1.9,
    "vit_b3": 11,
    "folate": 220,
    "vit_c": 65,
    "vit_e": 7.5,
    "iron": 29,
    "magnesium": 370,
    "zinc": 13.2,
}

ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

GOAL_ADJUSTMENT = {"lose": -500, "maintain": 0, "gain": 300}


def empty_totals() -> dict[str, float]:
    return {k: 0.0 for k in NUTRIENT_KEYS}


def clean_nutrients(raw: dict | None) -> dict[str, float]:
    """Keep only known nutrient keys with non-negative numeric values."""
    out = empty_totals()
    for key, value in (raw or {}).items():
        if key in out and value is not None:
            try:
                out[key] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return out


def scale(per_100g: dict, grams: float) -> dict[str, float]:
    factor = max(0.0, grams) / 100.0
    return {k: round(v * factor, 3) for k, v in clean_nutrients(per_100g).items()}


def add_into(totals: dict[str, float], nutrients: dict) -> None:
    for k, v in clean_nutrients(nutrients).items():
        totals[k] = totals.get(k, 0.0) + v


def age_on(dob: date | None, today: date | None = None) -> int | None:
    if not dob:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def suggest_goals(
    *,
    sex: str | None,
    age: int | None,
    height_cm: float | None,
    weight_kg: float | None,
    activity_level: str | None,
    goal: str | None,
) -> dict[str, float]:
    """Suggest daily targets from a profile.

    Energy uses Mifflin-St Jeor BMR x activity factor, adjusted for the weight
    goal. Protein uses ICMR's 0.83 g/kg (raised for weight loss/gain). Micronutrient
    targets use ICMR-NIN 2020 RDAs by sex.
    """
    goals = dict(DEFAULT_GOALS)
    female = (sex or "").lower() == "female"

    if female:
        goals.update(FEMALE_GOALS)

    if weight_kg and height_cm and age:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (-161 if female else 5)
        tdee = bmr * ACTIVITY_FACTORS.get(activity_level or "sedentary", 1.2)
        energy = max(1200.0, tdee + GOAL_ADJUSTMENT.get(goal or "maintain", 0))
        goals["energy"] = round(energy / 10) * 10

    if weight_kg:
        # Total water (drinks + food moisture): ~35 ml per kg body weight
        goals["water"] = round(weight_kg * 35 / 100) * 100
        per_kg = 0.83 if (goal or "maintain") == "maintain" else 1.2
        goals["protein"] = round(weight_kg * per_kg)

    energy = goals["energy"]
    # ~30% energy from fat, <10% from saturated fat, rest carbs after protein
    goals["fat"] = round(energy * 0.30 / 9)
    goals["sat_fat"] = round(energy * 0.10 / 9)
    goals["carbs"] = round(max(0.0, energy - goals["protein"] * 4 - goals["fat"] * 9) / 4)
    goals["sugar"] = round(energy * 0.10 / 4)
    goals["fiber"] = max(goals["fiber"], round(energy / 1000 * 14))
    return goals
