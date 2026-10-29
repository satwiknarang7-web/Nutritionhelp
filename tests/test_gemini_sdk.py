"""Exercise the real google-genai SDK against a local stand-in for the Gemini REST API."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from google import genai

from app import ai

GOOD_KEY = "AIzaGOODKEY_good_good_good_good_12345"
ANALYSIS = {
    "is_food": True,
    "notes": "Assumed home-style dal",
    "items": [{
        "name": "Dal tadka", "catalog_id": "dal_tadka", "quantity": 1, "unit": "katori",
        "estimated_grams": 150, "confidence": "high",
        "nutrients": {k: 1.0 for k in ai.NutrientEstimate.model_fields},
    }],
}


class FakeGemini(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        if self.headers.get("x-goog-api-key") == GOOD_KEY or f"key={GOOD_KEY}" in self.path:
            return True
        self._send(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                   "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
        return False

    def do_GET(self):  # noqa: N802
        if self._authorised():
            self._send(200, {"models": [
                {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            ]})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not self._authorised():
            return
        if "gemini-3.5-flash:generateContent" not in self.path:
            self._send(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
            return
        self._send(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(ANALYSIS)}]},
                                         "finishReason": "STOP"}]})


@pytest.fixture
def gemini_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGemini)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real_client = genai.Client
    url = f"http://127.0.0.1:{server.server_port}"
    def client(api_key, http_options=None):
        options = http_options.model_dump(exclude_none=True) if http_options else {}
        return real_client(api_key=api_key, http_options={**options, "base_url": url})

    monkeypatch.setattr(genai, "Client", client)
    yield
    server.shutdown()


def test_real_sdk_accepts_good_key(gemini_server):
    assert ai.validate_gemini_key(GOOD_KEY) is True


def test_real_sdk_rejects_bad_key_with_reason(gemini_server):
    with pytest.raises(ai.GeminiKeyInvalid, match="API key not valid"):
        ai.validate_gemini_key("AIzaBADKEY_bad_bad_bad_bad_bad_12345")


def test_real_sdk_full_analysis(gemini_server):
    result = ai.analyze_text("1 katori dal", ai.Provider("gemini", GOOD_KEY, "user"), "user-1")
    assert result["items"][0]["food_ref"] == "dal_tadka"
    assert result["items"][0]["basis"] == "database"
