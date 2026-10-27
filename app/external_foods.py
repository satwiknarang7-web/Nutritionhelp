"""External food databases, used when the curated Indian database has no match.

* Open Food Facts - open database of packaged foods with strong Indian coverage
  (Amul, Haldiram's, Britannia, Parle, MTR, ...). Text search + barcode lookup.
  No API key needed.
* USDA FoodData Central - generic ingredients and global branded foods with full
  micronutrient profiles. Free API key (DEMO_KEY works with low rate limits).

All lookups are cached: search results for a day, individual foods for 30 days.
Values are normalised to the app's per-100 g nutrient keys and units.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.cache import get_cache
from app.config import get_settings
from app.food_db import estimate_water
from app.nutrients import clean_nutrients

log = logging.getLogger(__name__)

SEARCH_TTL = 24 * 3600
FOOD_TTL = 30 * 24 * 3600

OFF_BASE = "https://world.openfoodfacts.org"
FDC_BASE = "https://api.nal.usda.gov/fdc/v1"

# Open Food Facts nutriment -> (our key, multiplier from OFF base unit).
# OFF stores minerals and vitamins in grams per 100 g.
OFF_MAP: dict[str, tuple[str, float]] = {
    "energy-kcal_100g": ("energy", 1),
    "proteins_100g": ("protein", 1),
    "carbohydrates_100g": ("carbs", 1),
    "fiber_100g": ("fiber", 1),
    "sugars_100g": ("sugar", 1),
    "fat_100g": ("fat", 1),
    "saturated-fat_100g": ("sat_fat", 1),
    "cholesterol_100g": ("cholesterol", 1000),
    "vitamin-a_100g": ("vit_a", 1_000_000),
    "vitamin-b1_100g": ("vit_b1", 1000),
    "vitamin-b2_100g": ("vit_b2", 1000),
    "vitamin-pp_100g": ("vit_b3", 1000),
    "vitamin-b6_100g": ("vit_b6", 1000),
    "vitamin-b9_100g": ("folate", 1_000_000),
    "folates_100g": ("folate", 1_000_000),
    "vitamin-b12_100g": ("vit_b12", 1_000_000),
    "vitamin-c_100g": ("vit_c", 1000),
    "vitamin-d_100g": ("vit_d", 1_000_000),
    "vitamin-e_100g": ("vit_e", 1000),
    "calcium_100g": ("calcium", 1000),
    "iron_100g": ("iron", 1000),
    "magnesium_100g": ("magnesium", 1000),
    "phosphorus_100g": ("phosphorus", 1000),
    "zinc_100g": ("zinc", 1000),
    "potassium_100g": ("potassium", 1000),
    "sodium_100g": ("sodium", 1000),
}

# USDA nutrient numbers -> our key (FDC reports these in the units we use).
FDC_MAP: dict[str, str] = {
    "208": "energy",
    "203": "protein",
    "205": "carbs",
    "291": "fiber",
    "269": "sugar",
    "204": "fat",
    "606": "sat_fat",
    "601": "cholesterol",
    "255": "water",
    "320": "vit_a",
    "404": "vit_b1",
    "405": "vit_b2",
    "406": "vit_b3",
    "415": "vit_b6",
    "435": "folate",
    "418": "vit_b12",
    "401": "vit_c",
    "328": "vit_d",
    "323": "vit_e",
    "301": "calcium",
    "303": "iron",
    "304": "magnesium",
    "305": "phosphorus",
    "309": "zinc",
    "306": "potassium",
    "307": "sodium",
}
FDC_ENERGY_FALLBACKS = ("957", "958")  # Atwater energy used by some Foundation foods
FDC_FOLATE_FALLBACK = "417"  # total folate when DFE is missing


class ExternalError(RuntimeError):
    pass


def _client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        timeout=settings.external_timeout,
        headers={"User-Agent": settings.off_user_agent},
        follow_redirects=True,
    )


def _get_json(url: str, params: dict[str, Any]) -> dict:
    with _client() as client:
        resp = client.get(url, params=params)
    if resp.status_code == 404:
        return {}
    if resp.status_code == 429:
        raise ExternalError("The food database is busy. Try again in a minute.")
    resp.raise_for_status()
    return resp.json()


def _num(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def _with_water(per_100g: dict[str, float], has_water: bool) -> dict[str, float]:
    if not has_water:
        per_100g["water"] = estimate_water(per_100g)
    return per_100g


# ---------------------------------------------------------------------------
# Open Food Facts
# ---------------------------------------------------------------------------
OFF_FIELDS = "code,product_name,product_name_en,brands,nutriments,serving_size,serving_quantity,quantity,categories_tags"


def parse_off_product(product: dict) -> dict | None:
    nutriments = product.get("nutriments") or {}
    per_100g: dict[str, float] = {}
    for field, (key, mult) in OFF_MAP.items():
        v = _num(nutriments.get(field))
        if v is not None and key not in per_100g:
            per_100g[key] = v * mult
    if "energy" not in per_100g:
        kj = _num(nutriments.get("energy_100g"))
        if kj is not None:
            per_100g["energy"] = kj / 4.184
    if "sodium" not in per_100g:
        salt = _num(nutriments.get("salt_100g"))
        if salt is not None:
            per_100g["sodium"] = salt * 400  # g salt -> mg sodium
    if "energy" not in per_100g:
        return None  # no usable nutrition data

    name = (product.get("product_name_en") or product.get("product_name") or "").strip()
    if not name:
        return None
    brand = (product.get("brands") or "").split(",")[0].strip()
    servings = []
    sq = _num(product.get("serving_quantity"))
    if sq:
        label = (product.get("serving_size") or f"{sq:g} g").strip()
        servings.append({"label": f"1 serving ({label})", "grams": sq})
    pack = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(g|ml)\b", (product.get("quantity") or "").lower())
    if pack:
        servings.append({"label": f"1 pack ({pack.group(1)} {pack.group(2)})", "grams": float(pack.group(1))})

    code = str(product.get("code") or "").strip()
    return {
        "id": f"off:{code}",
        "name": f"{name} ({brand})" if brand and brand.lower() not in name.lower() else name,
        "category": "Packaged",
        "diet": None,
        "per_100g": _with_water(clean_nutrients(per_100g), False),
        "servings": servings,
        "source": "openfoodfacts",
        "barcode": code,
    }


def off_search(query: str, limit: int = 15) -> list[dict]:
    """Packaged-food search, preferring products sold in India."""
    settings = get_settings()
    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": limit * 2,
        "fields": OFF_FIELDS,
        "sort_by": "unique_scans_n",
    }
    india_params = {**params, "tagtype_0": "countries", "tag_contains_0": "contains", "tag_0": settings.off_country}
    results: list[dict] = []
    seen: set[str] = set()
    for p in (india_params, params):
        data = _get_json(f"{OFF_BASE}/cgi/search.pl", p)
        for product in data.get("products", []):
            food = parse_off_product(product)
            if food and food["id"] not in seen:
                seen.add(food["id"])
                results.append(food)
        if len(results) >= limit:
            break
    return results[:limit]


def off_barcode(barcode: str) -> dict | None:
    code = re.sub(r"\D", "", barcode)
    if not 6 <= len(code) <= 14:
        return None
    data = _get_json(f"{OFF_BASE}/api/v2/product/{code}.json", {"fields": OFF_FIELDS})
    if data.get("status") != 1 or not data.get("product"):
        return None
    product = {**data["product"], "code": data["product"].get("code") or code}
    return parse_off_product(product)


# ---------------------------------------------------------------------------
# USDA FoodData Central
# ---------------------------------------------------------------------------
def _fdc_nutrients(items: list[dict]) -> dict[str, float]:
    """Accepts both search-result and food-detail nutrient shapes."""
    by_number: dict[str, float] = {}
    for item in items:
        nutrient = item.get("nutrient") or {}
        number = str(item.get("nutrientNumber") or nutrient.get("number") or "")
        unit = (item.get("unitName") or nutrient.get("unitName") or "").upper()
        value = _num(item.get("value", item.get("amount")))
        if not number or value is None:
            continue
        if number in ("208", *FDC_ENERGY_FALLBACKS) and unit and unit != "KCAL":
            continue
        by_number.setdefault(number, value)

    per_100g: dict[str, float] = {}
    for number, key in FDC_MAP.items():
        if number in by_number:
            per_100g[key] = by_number[number]
    if "energy" not in per_100g:
        for alt in FDC_ENERGY_FALLBACKS:
            if alt in by_number:
                per_100g["energy"] = by_number[alt]
                break
    if "folate" not in per_100g and FDC_FOLATE_FALLBACK in by_number:
        per_100g["folate"] = by_number[FDC_FOLATE_FALLBACK]
    return per_100g


def _fdc_name(food: dict) -> str:
    name = (food.get("description") or "").strip()
    name = name[:1].upper() + name[1:].lower() if name.isupper() else name
    brand = (food.get("brandName") or food.get("brandOwner") or "").strip()
    return f"{name} ({brand.title()})" if brand else name


def _fdc_servings(food: dict) -> list[dict]:
    servings = []
    size, unit = _num(food.get("servingSize")), (food.get("servingSizeUnit") or "").lower()
    if size and unit in ("g", "grm", "ml", "mlt"):
        household = (food.get("householdServingFullText") or "").strip()
        label = f"1 serving ({household})" if household else f"1 serving ({size:g} {unit[0] if unit else 'g'})"
        servings.append({"label": label, "grams": size})
    for portion in food.get("foodPortions") or []:
        grams = _num(portion.get("gramWeight"))
        if not grams:
            continue
        amount = _num(portion.get("amount")) or 1
        desc = (
            portion.get("portionDescription")
            or " ".join(filter(None, [f"{amount:g}", (portion.get("measureUnit") or {}).get("name"), portion.get("modifier")]))
        ).strip()
        if desc and desc.lower() != "quantity not specified":
            servings.append({"label": desc, "grams": grams})
    return servings[:8]


def parse_fdc_food(food: dict) -> dict | None:
    per_100g = _fdc_nutrients(food.get("foodNutrients") or [])
    if "energy" not in per_100g or not food.get("fdcId"):
        return None
    return {
        "id": f"usda:{food['fdcId']}",
        "name": _fdc_name(food),
        "category": "Branded" if food.get("dataType") == "Branded" else "Generic (USDA)",
        "diet": None,
        "per_100g": _with_water(clean_nutrients(per_100g), "water" in per_100g),
        "servings": _fdc_servings(food),
        "source": "usda",
    }


def fdc_search(query: str, limit: int = 15) -> list[dict]:
    settings = get_settings()
    data = _get_json(
        f"{FDC_BASE}/foods/search",
        {
            "api_key": settings.usda_api_key,
            "query": query,
            "pageSize": limit,
            "dataType": "Foundation,SR Legacy,Survey (FNDDS),Branded",
        },
    )
    out = []
    for food in data.get("foods", []):
        parsed = parse_fdc_food(food)
        if parsed:
            out.append(parsed)
    return out


def fdc_food(fdc_id: str) -> dict | None:
    if not fdc_id.isdigit():
        return None
    data = _get_json(f"{FDC_BASE}/food/{fdc_id}", {"api_key": get_settings().usda_api_key})
    return parse_fdc_food(data) if data else None


# ---------------------------------------------------------------------------
# Public, cached entry points
# ---------------------------------------------------------------------------
def _safe(fn, *args) -> Any:
    try:
        return fn(*args)
    except ExternalError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("external food lookup failed: %s", exc)
        return None


def search(query: str, limit: int = 20) -> list[dict]:
    """Search Open Food Facts and USDA, interleaving results, cached for a day."""
    q = " ".join(query.lower().split())[:80]
    if len(q) < 2:
        return []
    settings = get_settings()
    cache = get_cache()

    def produce() -> list[dict]:
        off = _safe(off_search, q, limit) if settings.enable_openfoodfacts else []
        usda = _safe(fdc_search, q, limit) if settings.enable_usda else []
        merged: list[dict] = []
        for pair in zip(off or [], usda or [], strict=False):
            merged.extend(pair)
        merged.extend((off or [])[len(usda or []):])
        merged.extend((usda or [])[len(off or []):])
        for food in merged:  # warm the per-food cache so logging needs no second request
            cache.set(f"ext:food:{food['id']}", food, FOOD_TTL)
        return merged[:limit]

    return cache.get_or_set(f"ext:search:{q}", SEARCH_TTL, produce) or []


def get(food_ref: str) -> dict | None:
    """Resolve an `off:<barcode>` or `usda:<fdcId>` reference."""
    source, _, ident = food_ref.partition(":")
    if source not in ("off", "usda") or not ident:
        return None
    settings = get_settings()

    def produce():
        if source == "off" and settings.enable_openfoodfacts:
            return _safe(off_barcode, ident)
        if source == "usda" and settings.enable_usda:
            return _safe(fdc_food, ident)
        return None

    return get_cache().get_or_set(f"ext:food:{food_ref}", FOOD_TTL, produce)


def barcode(code: str) -> dict | None:
    digits = re.sub(r"\D", "", code)
    return get(f"off:{digits}") if digits else None
