from shapely.geometry import box

from packs.regions import compute_regions, flat_id, polygon_rings, region_by_id, simplify_geometry, subdivision


def feat(id, parent=None, iso=None, iso2=None, geom=(0, 0, 1, 1)):
    props = {"id": id, "name": id.title(), "urls": {"pbf": f"https://x/{id}-latest.osm.pbf"}}
    if parent:
        props["parent"] = parent
    if iso:
        props["iso3166-1:alpha2"] = iso
    if iso2:
        props["iso3166-2"] = iso2
    minx, miny, maxx, maxy = geom
    ring = [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]}}


INDEX = {
    "features": [
        feat("europe"),
        feat("asia"),
        feat("africa"),
        feat("north-america"),
        feat("russia", iso=["RU"]),
        feat("antarctica", iso=["AQ"]),
        feat("austria", "europe", ["AT"]),
        feat("germany", "europe", ["DE"]),
        feat("bayern", "germany"),
        feat("oberbayern", "bayern"),
        feat("nordrhein-westfalen", "germany"),
        feat("koeln", "nordrhein-westfalen"),
        feat("taiwan", "asia", ["TW"], ["CN-TW"]),
        feat("berlin", "germany"),
        feat("dach", "europe"),
        feat("canary-islands", "africa"),
        feat("us", "north-america", ["US"]),
        feat("us/texas", "north-america", iso2=["US-TX"]),
        feat("us/puerto-rico", "north-america", ["PR"], ["US-PR"]),
        feat("us-west", "north-america"),
        feat("kaliningrad", "russia"),
        feat("crimean-fed-district", "russia", iso2=["UA-43"]),
    ]
}


def ids():
    return {r.id: r for r in compute_regions(INDEX)}


def test_country_without_children_is_one_pack():
    r = ids()["austria"]
    assert r.parent is None and r.country_code == "AT" and r.continent == "europe"


def test_country_with_children_is_split_one_level():
    rs = ids()
    assert "germany" not in rs
    assert rs["germany/bayern"].parent == "germany"
    assert rs["germany/bayern"].country_code == "DE"  # inherited
    assert "germany/bayern/oberbayern" not in rs and "germany/oberbayern" not in rs
    assert "germany/berlin" in rs


def test_us_states_and_composites():
    rs = ids()
    assert "us" not in rs and "us-west" not in rs and "dach" not in rs
    assert rs["us/texas"].parent == "us" and rs["us/texas"].country_code == "US"
    assert rs["us/puerto-rico"].country_code == "PR"


def test_iso3166_2():
    rs = ids()
    assert rs["germany/bayern"].iso3166_2 == "DE-BY"  # manual map
    assert rs["us/texas"].iso3166_2 == "US-TX"  # from Geofabrik index
    assert rs["austria"].iso3166_2 is None
    assert rs["taiwan"].iso3166_2 is None  # whole-country pack
    assert rs["canary-islands"].iso3166_2 == "ES-CN"
    # A deeper Geofabrik child inherits its state's code.
    props = {f["properties"]["id"]: f["properties"] for f in INDEX["features"]}
    assert subdivision(props, "koeln") == "DE-NW"
    assert subdivision(props, "germany") is None


def test_root_countries():
    rs = ids()
    assert rs["antarctica"].country_code == "AQ"
    assert rs["russia/kaliningrad"].country_code == "RU"
    assert "russia/crimean-fed-district" not in rs


def test_flat_id_and_lookup():
    regions = compute_regions(INDEX)
    assert flat_id("germany/bayern") == "germany__bayern"
    assert region_by_id(regions, "germany__bayern").id == "germany/bayern"
    assert region_by_id(regions, "bayern").id == "germany/bayern"


def test_simplified_polygon_is_small_and_close():
    import shapely

    wiggly = shapely.Polygon([(i / 100, (i % 2) * 0.02) for i in range(1000)] + [(10, 5), (0, 5)])
    s = simplify_geometry(wiggly, max_points=100)
    rings = polygon_rings(s)
    assert sum(len(r) for r in rings) <= 100
    assert s.symmetric_difference(wiggly).area < 0.02 * wiggly.area
    assert rings[0][0] == rings[0][-1]
    assert polygon_rings(box(0, 0, 1, 1).buffer(0))[0][0] in ([0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0])
