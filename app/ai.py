"""Computer-vision food recognition and free-text nutrition estimation.

Provider order for each request:
  1. the user's own Gemini API key (saved, encrypted, in Settings)
  2. the server's GEMINI_API_KEY
  3. the server's ANTHROPIC_API_KEY (Claude)
If none is available, AI features are disabled and manual entry still works.

The model is given the built-in food catalog so it can link recognised dishes to
curated nutrition data; anything not in the catalog gets a model estimate.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app import food_db
from app.cache import get_cache, hash_key
from app.config import get_settings
from app.nutrients import NUTRIENTS, clean_nutrients, scale

log = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    pass


class AIError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------
# One float field per tracked nutrient, generated from the registry so the AI
# schema always matches what the app tracks.
NutrientEstimate = create_model(
    "NutrientEstimate",
    **{n.key: (float, Field(description=f"{n.label} in {n.unit}")) for n in NUTRIENTS},
)


class FoodItem(BaseModel):
    name: str = Field(description="Common name of the dish or drink, e.g. 'Masala dosa'")
    catalog_id: str | None = Field(
        description="id from the food catalog if the item clearly matches one, otherwise null"
    )
    quantity: float = Field(description="How many units, e.g. 2 for '2 rotis'")
    unit: str = Field(description="Household unit: roti, katori, glass, piece, plate, cup, tbsp, g, ml")
    estimated_grams: float = Field(description="Total edible weight in grams (ml for drinks)")
    confidence: Literal["high", "medium", "low"]
    nutrients: NutrientEstimate = Field(description="Totals for the whole estimated portion")


class FoodAnalysis(BaseModel):
    is_food: bool = Field(description="False if the image/text contains no food or drink")
    items: list[FoodItem]
    notes: str = Field(description="One short sentence on assumptions, e.g. oil/ghee or portion size")


SYSTEM_PROMPT = """You are a nutrition assistant for an Indian food-tracking app.
Identify every food and drink the user ate from a meal photo or description and estimate portions.

Guidelines:
- Users are mostly in India. Recognise regional Indian dishes (North, South, East, West,
  North-East), street food, sweets, home-style sabzis, dals, rice dishes, breads, chutneys,
  pickles and beverages, as well as international foods.
- List each component of a thali or plate separately (e.g. roti, dal, sabzi, rice, salad, raita).
- Use Indian household units where natural: roti/paratha/puri count, katori (~150 g of dal or
  sabzi), plate, glass (~250 ml), cup (~120 ml tea), piece, tbsp, tsp.
- Estimate portion size from visual cues (plate ~25-28 cm, steel katori ~150 ml, hands, cutlery).
- Account for typical home cooking oil/ghee. Mention the assumption in notes.
- If an item matches an entry in the catalog below, set catalog_id to that id; otherwise null.
- Nutrient values are totals for the estimated portion, not per 100 g. Water is the
  total moisture in the portion in ml (drinks are mostly water).
- If there is no food or drink, set is_food to false and return no items.

Food catalog (id: name):
"""


def _system_text() -> str:
    return SYSTEM_PROMPT + food_db.catalog_for_prompt()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
@dataclass
class Provider:
    kind: Literal["gemini", "claude"]
    api_key: str
    source: Literal["user", "server"]

    @property
    def label(self) -> str:
        name = "Gemini" if self.kind == "gemini" else "Claude"
        return f"{name} ({'your key' if self.source == 'user' else 'app key'})"


def resolve_provider(user_gemini_key: str | None) -> Provider | None:
    settings = get_settings()
    if user_gemini_key:
        return Provider("gemini", user_gemini_key, "user")
    if settings.gemini_api_key:
        return Provider("gemini", settings.gemini_api_key, "server")
    if settings.anthropic_api_key:
        return Provider("claude", settings.anthropic_api_key, "server")
    return None


def _call_gemini(provider: Provider, image: tuple[bytes, str] | None, text: str) -> FoodAnalysis:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    client = genai.Client(api_key=provider.api_key)
    contents: list = []
    if image:
        contents.append(types.Part.from_bytes(data=image[0], mime_type=image[1]))
    contents.append(text)
    try:
        response = client.models.generate_content(
            model=get_settings().gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_system_text(),
                response_mime_type="application/json",
                response_schema=FoodAnalysis,
            ),
        )
    except genai_errors.ClientError as exc:
        if exc.code in (401, 403) or "API key" in str(exc):
            raise AIError("Your Gemini API key was rejected. Check it in Settings.") from exc
        if exc.code == 429:
            raise AIError("Gemini rate limit or quota reached. Try again shortly.") from exc
        raise AIError(f"Gemini request failed: {exc.message or exc}") from exc
    except genai_errors.APIError as exc:
        raise AIError("Gemini is temporarily unavailable. Try again shortly.") from exc

    parsed = response.parsed
    if isinstance(parsed, FoodAnalysis):
        return parsed
    if not response.text:
        raise AIError("Gemini returned no result (the request may have been blocked).")
    return FoodAnalysis.model_validate_json(response.text)


def _call_claude(provider: Provider, image: tuple[bytes, str] | None, text: str) -> FoodAnalysis:
    import anthropic

    client = anthropic.Anthropic(api_key=provider.api_key)
    content: list[dict] = []
    if image:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image[1],
                    "data": base64.standard_b64encode(image[0]).decode(),
                },
            }
        )
    content.append({"type": "text", "text": text})
    try:
        response = client.beta.messages.parse(
            model=get_settings().claude_model,
            max_tokens=16000,
            # Stable system prompt + catalog is cached across requests
            system=[{"type": "text", "text": _system_text(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=FoodAnalysis,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("The server's Claude API key is invalid.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("AI rate limit reached. Try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"AI request failed ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Could not reach the AI service.") from exc

    if response.stop_reason == "refusal":
        raise AIError("The AI declined to analyse this input.")
    if response.parsed_output is None:
        raise AIError("The AI returned an unreadable result. Please try again.")
    return response.parsed_output


def _run(provider: Provider, image: tuple[bytes, str] | None, text: str) -> FoodAnalysis:
    if provider.kind == "gemini":
        return _call_gemini(provider, image, text)
    return _call_claude(provider, image, text)


# ---------------------------------------------------------------------------
# Post-processing: link to curated data, produce loggable items
# ---------------------------------------------------------------------------
def _to_loggable(analysis: FoodAnalysis) -> dict:
    items = []
    for item in analysis.items:
        grams = max(1.0, float(item.estimated_grams or 0))
        food = food_db.get_food(item.catalog_id) if item.catalog_id else None
        if food is None:
            food = food_db.best_match(item.name, min_score=80)
        if food is not None:
            per_100g = clean_nutrients(food.per_100g)
            nutrients = scale(per_100g, grams)
            basis = "database"
        else:
            nutrients = clean_nutrients(item.nutrients.model_dump())
            per_100g = {k: round(v * 100 / grams, 3) for k, v in nutrients.items()}
            basis = "ai_estimate"
        items.append(
            {
                "name": food.name if food else item.name,
                "detected_as": item.name,
                "food_ref": food.id if food else None,
                "quantity": item.quantity or 1,
                "unit": item.unit or "serving",
                "grams": round(grams, 1),
                "confidence": item.confidence,
                "basis": basis,
                "per_100g": per_100g,
                "nutrients": nutrients,
            }
        )
    return {"is_food": analysis.is_food, "items": items, "notes": analysis.notes}


MAX_IMAGE_SIDE = 1568


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Validate the upload, fix orientation, downscale, and re-encode as JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError) as exc:
        raise AIError("That file doesn't look like a supported image.") from exc
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue(), "image/jpeg"


def analyze_photo(
    image_bytes: bytes, note: str, provider: Provider | None, user_id: str
) -> dict:
    if provider is None:
        raise AIUnavailable("AI photo recognition is not set up. Add your Gemini API key in Settings.")
    jpeg, mime = prepare_image(image_bytes)
    note = (note or "").strip()[:300]
    prompt = "Identify the foods and drinks in this photo and estimate each portion."
    if note:
        prompt += f"\nUser note: {note}"

    settings = get_settings()
    key = f"ai:photo:{hash_key(user_id, jpeg, note, provider.kind)}"
    return get_cache().get_or_set(
        key, settings.ai_cache_ttl, lambda: {**_to_loggable(_run(provider, (jpeg, mime), prompt)), "provider": provider.label}
    )


def analyze_text(description: str, provider: Provider | None, user_id: str) -> dict:
    if provider is None:
        raise AIUnavailable("AI estimation is not set up. Add your Gemini API key in Settings.")
    description = (description or "").strip()[:1000]
    if not description:
        raise AIError("Describe what you ate first.")
    prompt = f"The user describes what they ate or drank:\n{description}"
    settings = get_settings()
    key = f"ai:text:{hash_key(user_id, description.lower(), provider.kind)}"
    return get_cache().get_or_set(
        key, settings.ai_cache_ttl, lambda: {**_to_loggable(_run(provider, None, prompt)), "provider": provider.label}
    )


def validate_gemini_key(api_key: str) -> bool:
    """Cheap check that a Gemini key works (model metadata lookup, no token spend)."""
    from google import genai

    try:
        genai.Client(api_key=api_key).models.get(model=get_settings().gemini_model)
        return True
    except Exception:  # noqa: BLE001
        return False

