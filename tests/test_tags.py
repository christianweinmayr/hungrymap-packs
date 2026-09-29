import pytest

from packs.tags import compact_diet, decode_export_id, encode_id, keep, to_row


def test_encode_id():
    assert encode_id("node", 123) == "n123"
    assert encode_id("w", 456) == "w456"
    assert encode_id("relation", "789") == "r789"
    with pytest.raises(ValueError):
        encode_id("x", 1)


def test_decode_export_id_areas():
    assert decode_export_id("n20825418") == "n20825418"
    assert decode_export_id("w42") == "w42"
    assert decode_export_id("a84") == "w42"  # area from way 42
    assert decode_export_id("a85") == "r42"  # area from relation 42


def test_compact_diet():
    tags = {
        "diet:vegetarian": "yes",
        "diet:vegan": "only",
        "diet:gluten_free": "Yes",
        "diet:halal": "no",
        "diet:kosher": "limited",
        "cuisine": "pizza",
    }
    assert compact_diet(tags) == "gluten_free,vegan:only,vegetarian"
    assert compact_diet({"cuisine": "pizza"}) is None


def test_website_and_menu_fallback():
    r = to_row("n1", 1, 2, {"amenity": "cafe", "contact:website": "https://a.example", "menu:url": "https://m.example"})
    assert r["website"] == "https://a.example"
    assert r["menu_url"] == "https://m.example"
    r = to_row(
        "n1",
        1,
        2,
        {
            "amenity": "cafe",
            "website": "https://w.example",
            "contact:website": "https://a.example",
            "website:menu": "https://wm.example",
            "menu:url": "https://m.example",
        },
    )
    assert r["website"] == "https://w.example"
    assert r["menu_url"] == "https://wm.example"


def test_row_fields():
    r = to_row(
        "w5",
        48.1,
        11.5,
        {
            "amenity": "restaurant",
            "name": "Zum Wirt",
            "name:en": "At the Innkeeper",
            "opening_hours": "Mo-Su 11:00-23:00",
            "opening_hours:kitchen": "Mo-Su 11:30-21:00",
            "brand:wikidata": "Q1",
        },
    )
    assert r["opening_hours_kitchen"] == "Mo-Su 11:30-21:00"
    assert r["name_en"] == "At the Innkeeper"
    assert r["brand_wikidata"] == "Q1"
    assert r["diet"] is None


def test_keep():
    assert keep({"amenity": "cafe", "name": "X"})
    assert keep({"amenity": "cafe", "opening_hours": "24/7"})
    assert not keep({"amenity": "cafe", "cuisine": "coffee_shop"})
    assert not keep({"amenity": "bank", "name": "X"})
