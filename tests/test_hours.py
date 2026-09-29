from packs.hours import Rule, build_osm, hours_snippets, rules_from_jsonld


def test_build_osm_groups_days():
    rules = [Rule(d, "11:00", "22:00") for d in ["Mo", "Tu", "We", "Th", "Fr"]] + [Rule("Sa", "12:00", "23:00")]
    assert build_osm(rules) == "Mo-Fr 11:00-22:00; Sa 12:00-23:00"


def test_build_osm_split_shifts_and_pairs():
    rules = [Rule(d, t[0], t[1]) for d in ["Tu", "We"] for t in [("17:30", "22:00"), ("11:30", "14:30")]]
    assert build_osm(rules) == "Tu,We 11:30-14:30,17:30-22:00"


def test_build_osm_midnight_and_invalid():
    assert build_osm([Rule("Fr", "18:00", "00:00")]) == "Fr 18:00-24:00"
    assert build_osm([Rule("Fr", "25:00", "02:00")]) is None
    assert build_osm([]) is None


def test_jsonld_specification():
    page = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"Restaurant",
    "openingHoursSpecification":[{"@type":"OpeningHoursSpecification","dayOfWeek":["https://schema.org/Monday","Tuesday"],
    "opens":"11:00","closes":"22:00"},{"dayOfWeek":"Saturday","opens":"12:00:00","closes":"23:00:00"}]}</script>"""
    assert build_osm(rules_from_jsonld(page)) == "Mo,Tu 11:00-22:00; Sa 12:00-23:00"


def test_jsonld_short_strings_and_graph():
    page = """<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":"Restaurant",
    "openingHours":["Mo-Fr 11:00-14:00","Mo-Fr 17:00-22:00","Su 10:00-15:00"]}]}</script>"""
    assert build_osm(rules_from_jsonld(page)) == "Mo-Fr 11:00-14:00,17:00-22:00; Su 10:00-15:00"


def test_snippets_find_keywords():
    text = "Willkommen\n" + "x" * 3000 + "\nÖffnungszeiten\nMo–Fr 11–22 Uhr\nSa Ruhetag"
    s = hours_snippets(text)
    assert "Mo–Fr 11–22" in s and len(s) < 1200
