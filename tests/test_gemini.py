from types import SimpleNamespace

import pytest
from google.genai import errors

from app import ai

KEY_ERROR = errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                               "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
NOT_FOUND = errors.ClientError(404, {"error": {"code": 404, "message": "models/gemini-2.5-flash is not found", "status": "NOT_FOUND"}})
MODELS = [
    SimpleNamespace(name="models/gemini-2.5-flash", supported_actions=["generateContent"]),
    SimpleNamespace(name="models/gemini-3.5-flash", supported_actions=["generateContent", "countTokens"]),
    SimpleNamespace(name="models/gemini-3.5-flash-lite", supported_actions=["generateContent"]),
    SimpleNamespace(name="models/gemini-3.8-flash-preview", supported_actions=["generateContent"]),
    SimpleNamespace(name="models/text-embedding-005", supported_actions=["embedContent"]),
]


class FakeModels:
    def __init__(self, list_error=None, generate_errors=()):
        self.list_error = list_error
        self.generate_errors = list(generate_errors)
        self.used_models = []
        self.configs = []

    def list(self, config=None):
        if self.list_error:
            raise self.list_error
        return iter(MODELS)

    def generate_content(self, model, contents, config):
        self.used_models.append(model)
        self.configs.append(config)
        if self.generate_errors:
            raise self.generate_errors.pop(0)
        return SimpleNamespace(parsed=ai.FoodAnalysis(is_food=False, items=[], notes=""), text="{}")


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(**kw):
        holder["models"] = FakeModels(**kw)
        def client(api_key, http_options=None):
            holder["http_options"] = http_options
            return SimpleNamespace(models=holder["models"])

        monkeypatch.setattr("google.genai.Client", client)
        holder["models"].holder = holder
        return holder["models"]

    return install


def test_valid_key(fake):
    fake()
    assert ai.validate_gemini_key("AIzaGoodKey") is True


def test_invalid_key_is_rejected_with_googles_reason(fake):
    fake(list_error=KEY_ERROR)
    with pytest.raises(ai.GeminiKeyInvalid, match="API key not valid"):
        ai.validate_gemini_key("AIzaBadKey")


def test_other_google_errors_are_not_called_invalid_keys(fake):
    region = errors.ClientError(400, {"error": {"code": 400, "message": "User location is not supported for the API use.",
                                                "status": "FAILED_PRECONDITION"}})
    fake(list_error=region)
    with pytest.raises(ai.AIError, match="User location is not supported") as info:
        ai.validate_gemini_key("AIzaGoodKey")
    assert not isinstance(info.value, ai.GeminiKeyInvalid)
    disabled = errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                  "message": "Generative Language API has not been used in project 123 before or it is disabled."}})
    fake(list_error=disabled)
    with pytest.raises(ai.AIError, match="has not been used in project"):
        ai.validate_gemini_key("AIzaGoodKey")


def test_network_problem_is_not_reported_as_bad_key(fake):
    fake(list_error=ConnectionError("dns failure"))
    with pytest.raises(ai.AIError, match="Couldn't reach Gemini"):
        ai.validate_gemini_key("AIzaGoodKey")


def test_picks_newest_stable_flash_model(fake):
    models = fake()
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "2 rotis")
    assert models.used_models == ["gemini-3.5-flash"]


def test_retired_model_is_replaced_automatically(fake):
    from app.cache import get_cache

    get_cache().set(ai._MODEL_CACHE_KEY, ["gemini-2.5-flash", None], 3600)  # stale choice from before retirement
    models = fake(generate_errors=[NOT_FOUND])
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "2 rotis")
    assert models.used_models == ["gemini-2.5-flash", "gemini-3.5-flash"]


def test_explicit_model_setting_wins(fake, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_model", "gemini-custom-flash")
    models = fake()
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "dal")
    assert models.used_models == ["gemini-custom-flash"]


def test_bad_key_during_analysis(fake):
    fake(generate_errors=[KEY_ERROR])
    with pytest.raises(ai.AIError, match="rejected"):
        ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "dal")


def test_save_key_endpoint_distinguishes_outage(client, monkeypatch):
    def outage(key):
        raise ai.AIError("Couldn't reach Gemini to verify the key. Please try again in a moment.")

    monkeypatch.setattr(ai, "validate_gemini_key", outage)
    r = client.put("/api/settings/ai/gemini", json={"api_key": "AIzaSyTESTKEY_1234567890abcd"})
    assert r.status_code == 502 and "try again" in r.json()["detail"]


def test_analysis_uses_low_thinking_and_a_timeout(fake):
    models = fake()
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "2 rotis")
    config = models.configs[0]
    assert config.thinking_config.thinking_level.name == "LOW"
    options = models.holder["http_options"]
    assert options.timeout == ai.GEMINI_PRIMARY_TIMEOUT_S * 1000 and options.retry_options.attempts == 1
    assert config.max_output_tokens == ai.GEMINI_MAX_OUTPUT_TOKENS
    assert config.media_resolution.name == "MEDIA_RESOLUTION_MEDIUM"


def test_thinking_setting_matches_model_generation():
    from google.genai import types

    assert ai._gemini_thinking("gemini-3.5-flash", types).thinking_level.name == "LOW"
    assert ai._gemini_thinking("gemini-2.5-flash", types).thinking_budget == 0
    assert ai._gemini_thinking("gemini-2.0-flash", types) is None


def test_retries_without_thinking_if_model_rejects_it(fake):
    rejected = errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                  "message": "Thinking level is not supported for this model."}})
    models = fake(generate_errors=[rejected])
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "dal")
    assert models.configs[0].thinking_config is not None and models.configs[1].thinking_config is None


def test_slow_flash_falls_back_to_flash_lite(fake):
    import httpx

    models = fake(generate_errors=[httpx.ReadTimeout("The read operation timed out")])
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "dal")
    assert models.used_models == ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    assert models.holder["http_options"].timeout == ai.GEMINI_FALLBACK_TIMEOUT_S * 1000


def test_server_deadline_also_triggers_fallback(fake):
    deadline = errors.ServerError(504, {"error": {"code": 504, "message": "Deadline expired", "status": "DEADLINE_EXCEEDED"}})
    models = fake(generate_errors=[deadline])
    ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "dal")
    assert models.used_models == ["gemini-3.5-flash", "gemini-3.5-flash-lite"]


def test_both_models_too_slow_gives_a_friendly_error(fake):
    import httpx

    fake(generate_errors=[httpx.ReadTimeout("timed out"), httpx.ReadTimeout("timed out")])
    with pytest.raises(ai.AIError, match="responding slowly"):
        ai._call_gemini(ai.Provider("gemini", "k", "user"), None, "dal")


def test_picks_lite_model_for_fallback():
    names = ["models/gemini-3.5-flash", "models/gemini-3.5-flash-lite", "models/gemini-2.5-flash-lite", "models/gemini-3.8-flash-lite-preview"]
    assert ai.pick_gemini_model(names) == "gemini-3.5-flash"
    assert ai.pick_gemini_model(names, lite=True) == "gemini-3.5-flash-lite"


def test_catalog_items_without_nutrients_use_database_values():
    analysis = ai.FoodAnalysis(is_food=True, notes="", items=[
        ai.FoodItem(name="Mutton curry", catalog_id="mutton_curry", quantity=1, unit="katori",
                    estimated_grams=150, confidence="high", nutrients=None),
        ai.FoodItem(name="Kosha mangsho", catalog_id=None, quantity=1, unit="katori",
                    estimated_grams=150, confidence="medium", nutrients=None),
    ])
    items = ai._to_loggable(analysis)["items"]
    assert items[0]["basis"] == "database" and round(items[0]["nutrients"]["energy"]) == 300
    assert items[1]["food_ref"] == "kosha_mangsho"  # looser name match when the model gave no numbers
