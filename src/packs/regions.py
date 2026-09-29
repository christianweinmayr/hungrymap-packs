"""Compute the list of pack regions from Geofabrik's index-v1.json.

Granularity: every Geofabrik country is one pack, unless it has sub-regions,
in which case each *direct* child is a pack (germany -> 16 states, us -> 50+
states, ...). Deeper levels (bayern -> oberbayern) are not split further.

Region ids are path-like: "austria", "germany/bayern", "us/california".
Release asset names can't contain "/", so `flat_id` maps "/" -> "__".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from shapely import make_valid
from shapely.geometry import Polygon, shape

INDEX_URL = "https://download.geofabrik.de/index-v1.json"

# Geofabrik index entries that are convenience composites of other extracts
# (they would duplicate data and overlap other packs).
COMPOSITES = {
    "alps",
    "britain-and-ireland",
    "dach",
    "great-britain",  # = england + scotland + wales, which are children of united-kingdom
    "sea",  # south-east asia
    "south-africa-and-lesotho",
    "us-midwest",
    "us-northeast",
    "us-pacific",
    "us-south",
    "us-west",
}

# Country codes for entries where Geofabrik's index has none or a wrong one.
# The first code is the primary (used for holiday calendars).
COUNTRY_CODE_OVERRIDES = {
    "american-oceania": ["AS", "GU", "MP", "UM"],
    "azores": ["PT"],
    "canary-islands": ["ES"],
    "comores": ["KM"],
    "guernsey-jersey": ["GG", "JE"],
    "ile-de-clipperton": ["FR"],
    "isle-of-man": ["IM"],
    "israel-and-palestine": ["IL", "PS"],
    "kosovo": ["XK"],
    "malaysia-singapore-brunei": ["MY", "SG", "BN"],
    "pitcairn-islands": ["PN"],
    "polynesie-francaise": ["PF"],
    "tokelau": ["TK"],
    "wallis-et-futuna": ["WF"],
    "gcc-states": ["AE", "QA", "OM", "BH", "KW"],
    "ireland-and-northern-ireland": ["IE", "GB"],
    "haiti-and-domrep": ["DO", "HT"],
    "senegal-and-gambia": ["SN", "GM"],
    # Children whose extract belongs to a different country than their parent.
    "united-kingdom/bermuda": ["BM"],
    "united-kingdom/falklands": ["FK"],
    "australia/christmas-island": ["CX"],
    "australia/cocos-islands": ["CC"],
    "australia/norfolk-island": ["NF"],
    "australia/heard-mcdonald": ["HM"],
    "china/hong-kong": ["HK"],
    "china/macau": ["MO"],
    "norway/svalbard-janmayen": ["SJ"],
    "france/guadeloupe": ["GP"],
    "france/guyane": ["GF"],
    "france/martinique": ["MQ"],
    "france/mayotte": ["YT"],
    "france/reunion": ["RE"],
}

# ISO 3166-2 subdivision codes for Geofabrik sub-regions the index doesn't tag
# (it only has them for US states and Canadian provinces). Keyed by Geofabrik id;
# deeper Geofabrik children (nordrhein-westfalen -> koeln) inherit via parents.
SUBDIVISION_OVERRIDES = {
    # Germany
    "baden-wuerttemberg": "DE-BW",
    "bayern": "DE-BY",
    "berlin": "DE-BE",
    "brandenburg": "DE-BB",
    "bremen": "DE-HB",
    "hamburg": "DE-HH",
    "hessen": "DE-HE",
    "mecklenburg-vorpommern": "DE-MV",
    "niedersachsen": "DE-NI",
    "nordrhein-westfalen": "DE-NW",
    "rheinland-pfalz": "DE-RP",
    "saarland": "DE-SL",
    "sachsen": "DE-SN",
    "sachsen-anhalt": "DE-ST",
    "schleswig-holstein": "DE-SH",
    "thueringen": "DE-TH",
    # Spain (regional holidays differ per autonomous community)
    "andalucia": "ES-AN",
    "aragon": "ES-AR",
    "asturias": "ES-AS",
    "cantabria": "ES-CB",
    "castilla-la-mancha": "ES-CM",
    "castilla-y-leon": "ES-CL",
    "cataluna": "ES-CT",
    "ceuta": "ES-CE",
    "extremadura": "ES-EX",
    "galicia": "ES-GA",
    "islas-baleares": "ES-IB",
    "la-rioja": "ES-RI",
    "madrid": "ES-MD",
    "melilla": "ES-ML",
    "murcia": "ES-MC",
    "navarra": "ES-NC",
    "pais-vasco": "ES-PV",
    "valencia": "ES-VC",
    "canary-islands": "ES-CN",
    # Australia (state holidays)
    "act": "AU-ACT",
    "new-south-wales": "AU-NSW",
    "northern-territory": "AU-NT",
    "queensland": "AU-QLD",
    "south-australia": "AU-SA",
    "tasmania": "AU-TAS",
    "victoria": "AU-VIC",
    "western-australia": "AU-WA",
}

# Regions dropped on purpose.
EXCLUDED = {
    # Duplicates the ukraine extract's coverage.
    "crimean-fed-district",
    # Fully contained in the portugal extract.
    "azores",
}

SIMPLIFY_TOLERANCE = 0.005  # degrees (~500 m); raised per region to fit MAX_POLYGON_POINTS
MAX_POLYGON_POINTS = 300


@dataclass
class Region:
    id: str  # "austria", "germany/bayern"
    geofabrik_id: str  # "austria", "bayern"
    name: str
    parent: str | None  # country id for sub-regions, None for whole-country packs
    continent: str
    country_codes: list[str]
    pbf_url: str
    iso3166_2: str | None = None  # "DE-BY"; None for whole-country packs unless Geofabrik tags one
    geometry: object = field(default=None, repr=False)  # shapely geometry (full resolution)

    @property
    def flat_id(self) -> str:
        return flat_id(self.id)

    @property
    def country_code(self) -> str | None:
        return self.country_codes[0] if self.country_codes else None


def flat_id(region_id: str) -> str:
    return region_id.replace("/", "__")


def load_index(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def subdivision(props: dict[str, dict], gid: str) -> str | None:
    """First iso3166-2 of Geofabrik region gid or its nearest ancestor below the
    country (so nordrhein-westfalen/koeln -> DE-NW)."""
    g = gid
    while g in props:
        iso2 = SUBDIVISION_OVERRIDES.get(g) or (props[g].get("iso3166-2") or [None])[0]
        if iso2:
            return iso2
        if props[g].get("iso3166-1:alpha2"):
            return None  # reached the country
        g = props[g].get("parent")
    return None


def compute_regions(index: dict) -> list[Region]:
    feats = {f["properties"]["id"]: f for f in index["features"]}
    props = {k: f["properties"] for k, f in feats.items()}

    def kids(pid: str) -> list[str]:
        out = [k for k, p in props.items() if p.get("parent") == pid]
        if pid == "us":
            # US states live under north-america in the index with a "us/" id prefix.
            out += [k for k in props if k.startswith("us/")]
        return sorted(out)

    continents = sorted(k for k, p in props.items() if not p.get("parent"))

    def codes(p: dict) -> list[str]:
        return list(p.get("iso3166-1:alpha2") or [])

    regions: list[Region] = []

    def add(gid: str, rid: str, parent: str | None, continent: str, inherited: list[str]):
        p = props[gid]
        cc = COUNTRY_CODE_OVERRIDES.get(rid) or COUNTRY_CODE_OVERRIDES.get(gid) or codes(p) or inherited
        f = feats[gid]
        regions.append(
            Region(
                id=rid,
                geofabrik_id=gid,
                name=p["name"],
                parent=parent,
                continent=continent,
                country_codes=list(cc),
                pbf_url=p["urls"]["pbf"],
                # Whole-country packs: null (Geofabrik tags e.g. taiwan CN-TW, kosovo RS-KM),
                # except explicit overrides such as canary-islands ES-CN.
                iso3166_2=subdivision(props, gid) if parent else SUBDIVISION_OVERRIDES.get(gid),
                geometry=shape(f["geometry"]) if f.get("geometry") else None,
            )
        )

    def add_country(cid: str, continent: str):
        if cid in COMPOSITES or cid in EXCLUDED:
            return
        children = [c for c in kids(cid) if c not in COMPOSITES and c not in EXCLUDED]
        country_cc = COUNTRY_CODE_OVERRIDES.get(cid) or codes(props[cid])
        if not children:
            add(cid, cid, None, continent, country_cc)
            return
        for c in children:
            short = c.split("/", 1)[1] if c.startswith(cid + "/") else c
            add(c, f"{cid}/{short}", cid, continent, country_cc)

    for cont in continents:
        members = kids(cont)
        # A root with an ISO code (russia, antarctica) is itself a country.
        if codes(props[cont]):
            add_country(cont, cont)
            continue
        for cid in members:
            if cid.startswith("us/"):
                continue  # handled via "us"
            add_country(cid, cont)
        if cont == "north-america" and "us" not in members and "us" in props:
            add_country("us", cont)

    regions.sort(key=lambda r: r.id)
    return regions


def _polygons(geom) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    return [p for g in getattr(geom, "geoms", []) for p in _polygons(g)]


def _count_points(geom) -> int:
    return sum(len(p.exterior.coords) + sum(len(i.coords) for i in p.interiors) for p in _polygons(geom))


def simplify_geometry(geom, tolerance: float = SIMPLIFY_TOLERANCE, max_points: int = MAX_POLYGON_POINTS):
    """Douglas-Peucker (topology preserving), raising the tolerance until the
    result has <= max_points. No buffering: neighbouring regions should not
    overlap more than Geofabrik's own boundaries do."""
    geom = make_valid(geom)
    tol = tolerance
    while True:
        g = geom.simplify(tol, preserve_topology=True)
        if _count_points(g) <= max_points or tol > 1.0:
            return g
        tol *= 1.25


def polygon_rings(geom, ndigits: int = 3) -> list[list[list[float]]]:
    """All rings (outer and inner) as [[lon,lat],...]. Evaluate with the even-odd rule."""
    rings = []
    for p in _polygons(geom):
        if p.is_empty:
            continue
        for ring in [p.exterior, *p.interiors]:
            rings.append([[round(x, ndigits), round(y, ndigits)] for x, y in ring.coords])
    return rings


def bbox(geom, ndigits: int = 4) -> list[float]:
    minx, miny, maxx, maxy = geom.bounds
    return [round(minx, ndigits), round(miny, ndigits), round(maxx, ndigits), round(maxy, ndigits)]


def region_by_id(regions: list[Region], rid: str) -> Region:
    for r in regions:
        if r.id == rid or r.flat_id == rid:
            return r
    # Convenience: allow the bare Geofabrik id ("bayern").
    matches = [r for r in regions if r.geofabrik_id == rid]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"unknown region {rid!r}")
