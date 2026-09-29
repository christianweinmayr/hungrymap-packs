"""Pure OSM-tag -> pack-row mapping (no I/O, unit tested)."""

from __future__ import annotations

AMENITIES = (
    "restaurant",
    "cafe",
    "fast_food",
    "bar",
    "pub",
    "biergarten",
    "food_court",
    "ice_cream",
)

COLUMNS = (
    "id",
    "name",
    "lat",
    "lon",
    "amenity",
    "cuisine",
    "opening_hours",
    "opening_hours_kitchen",
    "diet",
    "website",
    "menu_url",
    "phone",
    "brand",
    "brand_wikidata",
    "takeaway",
    "outdoor_seating",
    "name_en",
)


def encode_id(osm_type: str, osm_id: int) -> str:
    """("node", 123) -> "n123"; accepts "n"/"w"/"r" or full type names."""
    t = osm_type[0].lower()
    if t not in "nwr":
        raise ValueError(f"bad OSM type {osm_type!r}")
    return f"{t}{int(osm_id)}"


def decode_export_id(fid: str) -> str:
    """Map an `osmium export --add-unique-id=type_id` id to our id.

    Areas come out as "a<n>" with n = 2*way_id (ways) or 2*rel_id+1 (relations).
    """
    kind, num = fid[0], int(fid[1:])
    if kind == "a":
        return encode_id("r", (num - 1) // 2) if num % 2 else encode_id("w", num // 2)
    return encode_id(kind, num)


def compact_diet(tags: dict) -> str | None:
    """diet:*=yes|only -> "gluten_free,vegan:only,vegetarian" (sorted by key)."""
    out = []
    for k in sorted(tags):
        if not k.startswith("diet:"):
            continue
        key = k[5:]
        v = str(tags[k]).strip().lower()
        if not key:
            continue
        if v == "yes":
            out.append(key)
        elif v == "only":
            out.append(f"{key}:only")
    return ",".join(out) or None


def first(tags: dict, *keys: str) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def keep(tags: dict) -> bool:
    if tags.get("amenity") not in AMENITIES:
        return False
    # Unnamed places are only useful if they carry hours.
    return bool(first(tags, "name") or first(tags, "opening_hours"))


def to_row(pid: str, lat: float, lon: float, tags: dict) -> dict:
    return {
        "id": pid,
        "name": first(tags, "name"),
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "amenity": tags.get("amenity"),
        "cuisine": first(tags, "cuisine"),
        "opening_hours": first(tags, "opening_hours"),
        "opening_hours_kitchen": first(tags, "opening_hours:kitchen"),
        "diet": compact_diet(tags),
        "website": first(tags, "website", "contact:website"),
        "menu_url": first(tags, "website:menu", "menu:url"),
        "phone": first(tags, "phone", "contact:phone"),
        "brand": first(tags, "brand"),
        "brand_wikidata": first(tags, "brand:wikidata"),
        "takeaway": first(tags, "takeaway"),
        "outdoor_seating": first(tags, "outdoor_seating"),
        "name_en": first(tags, "name:en"),
    }
