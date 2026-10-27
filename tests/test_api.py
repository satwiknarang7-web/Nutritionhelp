import io

from PIL import Image

from app import ai
from app.nutrients import DEFAULT_GOALS

DAY = "2026-10-27"


def add(client, **kw):
    body = {"logged_on": DAY, "meal": "lunch", "food_name": "x", "quantity": 1, "unit": "1 roti", "grams": 40, **kw}
    return client.post("/api/logs", json=body)


def test_pages_require_login(anon_client):
    r = anon_client.get("/diary", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert anon_client.get("/api/goals", headers={"X-Requested-With": "fetch"}).status_code == 401
    assert anon_client.get("/login").status_code == 200


def test_mutations_require_csrf_header(client):
    r = client.post("/api/water", json={"logged_on": DAY, "ml": 250}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_log_builtin_food_computes_nutrients_server_side(client, repo):
    r = add(client, food_ref="roti", food_name="anything", per_100g={"energy": 9999})
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["food_name"] == "Roti / Chapati (plain)"
    assert round(entry["nutrients"]["energy"], 1) == round(264 * 0.4, 1)
    assert entry["nutrients"]["water"] > 0


def test_log_ai_item_without_ref_uses_supplied_per_100g(client):
    r = add(client, food_name="Mystery curry", per_100g={"energy": 150, "protein": 5}, grams=200, source="text_ai")
    assert r.json()["nutrients"]["energy"] == 300
    assert add(client, food_name="Unknown").status_code == 422


def test_diary_water_edit_delete_flow(client):
    roti = add(client, food_ref="roti").json()
    client.post("/api/water", json={"logged_on": DAY, "ml": 500})
    d = client.get(f"/api/diary?day={DAY}").json()
    assert d["plain_water_ml"] == 500
    assert d["totals"]["water"] > 500  # roti moisture counts too
    assert len(d["meals"]["lunch"]["entries"]) == 1

    patched = client.patch(f"/api/logs/{roti['id']}", json={"grams": 80, "meal": "dinner"}).json()
    assert patched["meal"] == "dinner"
    assert round(patched["nutrients"]["energy"], 1) == round(roti["nutrients"]["energy"] * 2, 1)
    d = client.get(f"/api/diary?day={DAY}").json()  # cache must be invalidated
    assert len(d["meals"]["dinner"]["entries"]) == 1

    assert client.delete(f"/api/logs/{roti['id']}").status_code == 200
    assert client.delete(f"/api/logs/{roti['id']}").status_code == 404
    assert client.delete("/api/logs/not-a-uuid").status_code == 404


def test_copy_previous_day(client):
    add(client, food_ref="idli", logged_on="2026-10-26", meal="breakfast")
    client.post("/api/water", json={"logged_on": "2026-10-26", "ml": 250})
    r = client.post("/api/logs/copy", json={"from_day": "2026-10-26", "to_day": DAY})
    assert r.json() == {"copied": 1}  # water is not copied


def test_custom_food_and_recipe(client):
    food = client.post(
        "/api/foods/custom",
        json={"name": "Protein bar", "serving_label": "1 bar", "serving_grams": 50,
              "nutrients_per_serving": {"energy": 200, "protein": 20}},
    ).json()
    assert food["per_100g"]["energy"] == 400 and food["source"] == "custom"
    assert client.get("/api/foods/search?q=protein bar").json()[0]["id"] == food["id"]
    assert add(client, food_ref=food["id"], grams=50).json()["nutrients"]["protein"] == 20

    recipe = client.post(
        "/api/foods/recipe",
        json={"name": "Dal chawal", "servings": 2, "cooked_weight_g": 600,
              "ingredients": [{"food_ref": "toor_dal_raw", "grams": 100}, {"food_ref": "rice_raw", "grams": 200}]},
    ).json()
    total_kcal = 343 + 360 * 2
    assert abs(recipe["per_100g"]["energy"] * 6 - total_kcal) < 1
    assert recipe["servings"][0]["grams"] == 300
    assert recipe["category"] == "Recipes"

    bad = client.post("/api/foods/recipe", json={"name": "x", "ingredients": [{"food_ref": "nope", "grams": 1}]})
    assert bad.status_code == 422


def test_stats_endpoint(client):
    add(client, food_ref="roti", grams=120)
    r = client.get(f"/api/stats?period=week&anchor={DAY}").json()
    assert r["logged_days"] == 1 and r["days"] == 7
    assert client.get("/api/stats?period=custom").status_code == 422


def test_goals_and_profile(client):
    assert client.get("/api/goals").json()["energy"] == DEFAULT_GOALS["energy"]
    g = client.put("/api/goals", json={"energy": 1800, "bogus": 5}).json()
    assert g["energy"] == 1800 and "bogus" not in g
    client.put("/api/profile", json={"sex": "male", "date_of_birth": "1996-01-01", "height_cm": 175,
                                     "weight_kg": 70, "activity_level": "light", "weight_goal": "lose"})
    s = client.get("/api/goals/suggested").json()
    assert s["protein"] == 84 and s["energy"] < 2200


def test_ai_unavailable_without_keys(client, monkeypatch):
    monkeypatch.setattr(ai, "resolve_provider", lambda key: None)
    r = client.post("/api/analyze/text", json={"description": "2 rotis and dal"})
    assert r.status_code == 409 and "Gemini" in r.json()["detail"]


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def test_photo_analysis_links_catalog_and_caches(client, repo, monkeypatch):
    calls = []

    def fake_run(provider, image, text):
        calls.append(text)
        return ai.FoodAnalysis(
            is_food=True,
            notes="Assumed 1 tsp ghee",
            items=[
                ai.FoodItem(name="Masala dosa", catalog_id="masala_dosa", quantity=1, unit="dosa",
                            estimated_grams=180, confidence="high", nutrients=ai.NutrientEstimate(**{k: 1 for k in ai.NutrientEstimate.model_fields})),
                ai.FoodItem(name="Grandma's special chutney", catalog_id=None, quantity=2, unit="tbsp",
                            estimated_grams=40, confidence="low", nutrients=ai.NutrientEstimate(**{k: 2 for k in ai.NutrientEstimate.model_fields})),
            ],
        )

    monkeypatch.setattr(ai, "_run", fake_run)
    monkeypatch.setattr(ai, "resolve_provider", lambda key: ai.Provider("gemini", "k", "user"))
    files = {"photo": ("meal.jpg", _jpeg(), "image/jpeg")}
    r = client.post("/api/analyze/photo", files=files, data={"note": ""})
    assert r.status_code == 200, r.text
    body = r.json()
    dosa, chutney = body["items"]
    assert dosa["basis"] == "database" and dosa["food_ref"] == "masala_dosa"
    assert round(dosa["nutrients"]["energy"]) == round(165 * 1.8)
    assert chutney["basis"] == "ai_estimate" and chutney["nutrients"]["energy"] == 2
    assert chutney["per_100g"]["energy"] == 5
    assert body["photo_path"].startswith(repo.user_id)

    client.post("/api/analyze/photo", files=files, data={"note": ""})
    assert len(calls) == 1  # identical photo served from cache

    assert client.post("/api/analyze/photo", files={"photo": ("x.txt", b"hello", "text/plain")}).status_code == 415


def test_gemini_key_is_encrypted(client, repo, monkeypatch):
    monkeypatch.setattr(ai, "validate_gemini_key", lambda k: True)
    key = "AIzaSyTESTKEY_1234567890abcd"
    r = client.put("/api/settings/ai/gemini", json={"api_key": key}).json()
    assert r["has_user_key"] and r["key_hint"] == "…abcd"
    stored = repo.keys["gemini"]["encrypted_key"]
    assert key not in stored
    assert "Gemini (your key)" == r["active_provider"]
    monkeypatch.setattr(ai, "validate_gemini_key", lambda k: False)
    assert client.put("/api/settings/ai/gemini", json={"api_key": key}).status_code == 400
    assert client.delete("/api/settings/ai/gemini").json()["has_user_key"] is False


def test_pages_render(client):
    for path in ["/diary", f"/diary?d={DAY}", "/trends", "/goals", "/profile", "/foods"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "NutriTrack" in r.text


def test_login_sets_session_cookies_even_with_stale_cookie(anon_client, monkeypatch):
    from app import auth

    tokens = auth.SessionTokens("new-access", "new-refresh", 3600, auth.AuthUser("u", "a@b.c"))
    monkeypatch.setattr(auth, "sign_in", lambda e, p: tokens)
    monkeypatch.setattr(auth, "verify_access_token", lambda t: None)
    monkeypatch.setattr(auth, "refresh", lambda t: None)
    anon_client.cookies.set(auth.ACCESS_COOKIE, "stale")
    r = anon_client.post("/login", data={"email": "a@b.c", "password": "secret123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/diary"
    cookies = r.headers.get_list("set-cookie")
    assert any(c.startswith(f"{auth.ACCESS_COOKIE}=new-access") and "HttpOnly" in c for c in cookies)
    assert not any(c.startswith(f'{auth.ACCESS_COOKIE}=""') for c in cookies)


def test_login_error_is_shown(anon_client, monkeypatch):
    from app import auth

    def fail(e, p):
        raise auth.AuthError("Invalid login credentials")

    monkeypatch.setattr(auth, "sign_in", fail)
    r = anon_client.post("/login", data={"email": "a@b.c", "password": "x"})
    assert "Invalid login credentials" in r.text


def test_expired_session_is_refreshed(anon_client, monkeypatch):
    from app import auth

    user = auth.AuthUser("00000000-0000-0000-0000-000000000001", "a@b.c")
    monkeypatch.setattr(auth, "verify_access_token", lambda t: None)
    monkeypatch.setattr(auth, "refresh", lambda t: auth.SessionTokens("fresh", "r2", 3600, user) if t == "r1" else None)
    anon_client.cookies.set(auth.REFRESH_COOKIE, "r1")
    r = anon_client.get("/api/nutrients", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert any(c.startswith(f"{auth.ACCESS_COOKIE}=fresh") for c in r.headers.get_list("set-cookie"))
