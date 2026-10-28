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

    def list(self, config=None):
        if self.list_error:
            raise self.list_error
        return iter(MODELS)

    def generate_content(self, model, contents, config):
        self.used_models.append(model)
        if self.generate_errors:
            raise self.generate_errors.pop(0)
        return SimpleNamespace(parsed=ai.FoodAnalysis(is_food=False, items=[], notes=""), text="{}")


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(**kw):
        holder["models"] = FakeModels(**kw)
        monkeypatch.setattr("google.genai.Client", lambda api_key: SimpleNamespace(models=holder["models"]))
        return holder["models"]

    return install


def test_valid_key(fake):
    fake()
    assert ai.validate_gemini_key("AIzaGoodKey") is True


def test_invalid_key_is_rejected(fake):
    fake(list_error=KEY_ERROR)
    assert ai.validate_gemini_key("AIzaBadKey") is False


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

    get_cache().set(ai._MODEL_CACHE_KEY, "gemini-2.5-flash", 3600)  # stale choice from before retirement
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
    assert r.status_code == 503 and "try again" in r.json()["detail"]
