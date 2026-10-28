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
import re
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


# ---------------------------------------------------------------------------
# Gemini model selection and key validation
# ---------------------------------------------------------------------------
GEMINI_FALLBACK_MODEL = "gemini-flash-latest"  # Google's alias for the current Flash model
_EXCLUDED_MODEL_WORDS = ("lite", "preview", "exp", "tts", "image", "live", "audio", "embedding", "thinking", "learnlm", "nano")
_MODEL_CACHE_KEY = "gemini:model"


class GeminiKeyInvalid(AIError):
    pass


def _is_key_error(exc) -> bool:
    text = f"{getattr(exc, 'status', '')} {getattr(exc, 'message', '')} {exc}".upper()
    return getattr(exc, "code", None) in (401, 403) or "API_KEY_INVALID" in text or "API KEY NOT VALID" in text


def pick_gemini_model(model_names: list[str]) -> str | None:
    """Newest stable 'gemini-X.Y-flash' model from a list of generateContent-capable model names."""
    best: tuple[tuple[int, ...], str] | None = None
    for full in model_names:
        name = full.removeprefix("models/")
        if any(w in name for w in _EXCLUDED_MODEL_WORDS):
            continue
        m = re.fullmatch(r"gemini-(\d+(?:\.\d+)*)-flash(?:-\d{3})?", name)
        if not m:
            continue
        version = tuple(int(x) for x in m.group(1).split("."))
        if best is None or version > best[0] or (version == best[0] and len(name) < len(best[1])):
            best = (version, name)
    return best[1] if best else None


def _list_generate_models(client) -> list[str]:
    names = []
    for model in client.models.list(config={"page_size": 200}):
        actions = [a.lower() for a in (model.supported_actions or [])]
        if not actions or "generatecontent" in actions:
            names.append(model.name or "")
    return names


def gemini_model(client, refresh: bool = False) -> str:
    """The configured GEMINI_MODEL, otherwise the newest stable Flash model this key can use (cached 6 h)."""
    configured = get_settings().gemini_model.strip()
    if configured:
        return configured
    cache = get_cache()
    if not refresh:
        cached = cache.get(_MODEL_CACHE_KEY)
        if cached:
            return cached
    try:
        model = pick_gemini_model(_list_generate_models(client)) or GEMINI_FALLBACK_MODEL
    except Exception:  # noqa: BLE001 - fall back to Google's alias
        log.warning("could not list Gemini models", exc_info=True)
        model = GEMINI_FALLBACK_MODEL
    cache.set(_MODEL_CACHE_KEY, model, 6 * 3600)
    return model


def validate_gemini_key(api_key: str) -> bool:
    """True if Gemini accepts the key. Raises AIError if Gemini can't be reached to check."""
    from google import genai
    from google.genai import errors as genai_errors

    try:
        next(iter(genai.Client(api_key=api_key).models.list(config={"page_size": 1})), None)
        return True
    except genai_errors.ClientError as exc:
        if _is_key_error(exc) or exc.code == 400:
            log.info("Gemini rejected a user key: %s %s", exc.code, exc.message)
            return False
        log.warning("Gemini key check failed: %s %s", exc.code, exc.message)
        raise AIError(f"Couldn't verify the key with Gemini ({exc.code}). Please try again.") from exc
    except Exception as exc:  # noqa: BLE001 - network, timeouts, 5xx
        log.warning("Gemini key check failed", exc_info=True)
        raise AIError("Couldn't reach Gemini to verify the key. Please try again in a moment.") from exc


def _call_gemini(provider: Provider, image: tuple[bytes, str] | None, text: str) -> FoodAnalysis:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    client = genai.Client(api_key=provider.api_key)
    contents: list = []
    if image:
        contents.append(types.Part.from_bytes(data=image[0], mime_type=image[1]))
    contents.append(text)
    config = types.GenerateContentConfig(
        system_instruction=_system_text(),
        response_mime_type="application/json",
        response_schema=FoodAnalysis,
    )
    model = gemini_model(client)
    for attempt in range(2):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            break
        except genai_errors.ClientError as exc:
            if exc.code == 404 and attempt == 0 and not get_settings().gemini_model:
                # The cached model was retired - pick a current one and retry once
                log.warning("Gemini model %s unavailable, re-selecting", model)
                model = gemini_model(client, refresh=True)
                continue
            log.warning("Gemini request failed: %s %s", exc.code, exc.message)
            if _is_key_error(exc):
                raise AIError("Your Gemini API key was rejected. Check it in Profile.") from exc
            if exc.code == 429:
                raise AIError("Gemini rate limit or quota reached. Try again shortly.") from exc
            if exc.code == 404:
                raise AIError(f"The Gemini model '{model}' isn't available. Check GEMINI_MODEL.") from exc
            raise AIError(f"Gemini request failed: {exc.message or exc}") from exc
        except genai_errors.APIError as exc:
            log.warning("Gemini server error: %s", exc)
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


def prepare_image(data: bytes, max_side: int = MAX_IMAGE_SIDE, quality: int = 85) -> tuple[bytes, str]:
    """Validate the upload, fix orientation, downscale, and re-encode as JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError) as exc:
        raise AIError("That file doesn't look like a supported image.") from exc
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
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


