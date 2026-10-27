from app import food_db
from app.nutrients import NUTRIENT_KEYS


def test_database_is_large_and_complete():
    foods = food_db.load_foods()
    assert len(foods) >= 550
    for food in foods.values():
        assert set(food.per_100g) == set(NUTRIENT_KEYS), food.id
        assert food.servings, food.id
        assert all(s["grams"] > 0 for s in food.servings)


def test_regional_coverage():
    names = " ".join(f.name.lower() for f in food_db.load_foods().values())
    for dish in ["dosa", "litti", "dhansak", "shorshe ilish", "thukpa", "ker sangri", "appam", "pesarattu", "vindaloo"]:
        assert dish in names, dish


def test_search_handles_aliases_and_typos():
    assert food_db.search("chapati")[0].id == "roti"
    assert food_db.search("idly")[0].id == "idli"
    assert any(f.id == "chicken_biryani" for f in food_db.search("biriyani"))
    assert food_db.search("panir tika")[0].id == "paneer_tikka"
    assert food_db.search("") == []


def test_best_match_threshold():
    assert food_db.best_match("Paneer Butter Masala").id == "paneer_butter_masala"
    assert food_db.best_match("quantum soufflé") is None


def test_water_estimate_includes_alcohol_and_drinks():
    foods = food_db.load_foods()
    assert foods["water"].per_100g["water"] == 100
    assert 85 < foods["masala_chai"].per_100g["water"] < 90
    assert foods["ghee"].per_100g["water"] == 0
    assert 55 < foods["whisky"].per_100g["water"] < 70


def test_parse_servings():
    assert food_db.parse_servings("1 roti=40|1 large=55") == [
        {"label": "1 roti", "grams": 40.0},
        {"label": "1 large", "grams": 55.0},
    ]
