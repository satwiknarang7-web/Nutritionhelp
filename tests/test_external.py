from app import external_foods as ext

OFF_PRODUCT = {
    "code": "8901063092280",
    "product_name": "Aloo Bhujia",
    "brands": "Haldiram's",
    "serving_size": "30 g",
    "serving_quantity": 30,
    "quantity": "200 g",
    "nutriments": {
        "energy-kcal_100g": 579,
        "proteins_100g": 11.6,
        "carbohydrates_100g": 41.3,
        "fat_100g": 40.9,
        "sodium_100g": 0.82,
        "calcium_100g": 0.035,
        "iron_100g": 0.003,
        "vitamin-c_100g": 0.0012,
    },
}


def test_parse_off_product_converts_units():
    food = ext.parse_off_product(OFF_PRODUCT)
    assert food["id"] == "off:8901063092280"
    assert food["name"] == "Aloo Bhujia (Haldiram's)"
    p = food["per_100g"]
    assert p["energy"] == 579 and p["sodium"] == 820 and p["calcium"] == 35
    assert round(p["iron"], 2) == 3 and round(p["vit_c"], 2) == 1.2
    assert 5 <= p["water"] <= 7
    assert food["servings"][0] == {"label": "1 serving (30 g)", "grams": 30}
    assert food["servings"][1]["grams"] == 200


def test_parse_off_product_fallbacks():
    food = ext.parse_off_product({"code": "1", "product_name": "Salted", "nutriments": {"energy_100g": 418.4, "salt_100g": 1}})
    assert round(food["per_100g"]["energy"]) == 100 and food["per_100g"]["sodium"] == 400
    assert ext.parse_off_product({"code": "1", "product_name": "No data", "nutriments": {}}) is None


FDC_SEARCH_ITEM = {
    "fdcId": 174683,
    "description": "JACKFRUIT, RAW",
    "dataType": "SR Legacy",
    "foodNutrients": [
        {"nutrientNumber": "208", "unitName": "KCAL", "value": 95},
        {"nutrientNumber": "268", "unitName": "kJ", "value": 397},
        {"nutrientNumber": "203", "unitName": "G", "value": 1.72},
        {"nutrientNumber": "205", "unitName": "G", "value": 23.2},
        {"nutrientNumber": "204", "unitName": "G", "value": 0.64},
        {"nutrientNumber": "255", "unitName": "G", "value": 73.5},
        {"nutrientNumber": "417", "unitName": "UG", "value": 24},
        {"nutrientNumber": "306", "unitName": "MG", "value": 448},
    ],
}

FDC_DETAIL = {
    "fdcId": 2346404,
    "description": "Paneer",
    "dataType": "Foundation",
    "foodNutrients": [
        {"nutrient": {"number": "957", "unitName": "kcal"}, "amount": 321},
        {"nutrient": {"number": "203", "unitName": "g"}, "amount": 21},
    ],
    "foodPortions": [{"gramWeight": 28, "amount": 1, "measureUnit": {"name": "oz"}, "modifier": ""}],
}


def test_parse_fdc_shapes():
    jack = ext.parse_fdc_food(FDC_SEARCH_ITEM)
    assert jack["id"] == "usda:174683" and jack["name"] == "Jackfruit, raw"
    assert jack["per_100g"]["energy"] == 95 and jack["per_100g"]["water"] == 73.5
    assert jack["per_100g"]["folate"] == 24 and jack["per_100g"]["potassium"] == 448
    paneer = ext.parse_fdc_food(FDC_DETAIL)
    assert paneer["per_100g"]["energy"] == 321
    assert paneer["servings"] == [{"label": "1 oz", "grams": 28}]


def test_search_merges_and_warms_cache(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "enable_openfoodfacts", True)
    monkeypatch.setattr(get_settings(), "enable_usda", True)
    monkeypatch.setattr(ext, "off_search", lambda q, n: [ext.parse_off_product(OFF_PRODUCT)])
    monkeypatch.setattr(ext, "fdc_search", lambda q, n: [ext.parse_fdc_food(FDC_SEARCH_ITEM)])
    results = ext.search("bhujia")
    assert [r["source"] for r in results] == ["openfoodfacts", "usda"]

    monkeypatch.setattr(ext, "off_barcode", lambda code: (_ for _ in ()).throw(AssertionError("should be cached")))
    assert ext.get("off:8901063092280")["name"].startswith("Aloo Bhujia")


def test_network_errors_degrade_gracefully(monkeypatch):
    import httpx

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "enable_openfoodfacts", True)
    monkeypatch.setattr(get_settings(), "enable_usda", False)

    def boom(*a):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(ext, "off_search", boom)
    assert ext.search("maggi") == []


def test_barcode_validation():
    assert ext.off_barcode("12") is None
