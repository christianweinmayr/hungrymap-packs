"""Assemble manifest.json from the region list and per-pack build results."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .build import SCHEMA_VERSION, USER_AGENT, now_iso
from .regions import Region, bbox, polygon_rings, simplify_geometry

MANIFEST_MAX_BYTES = 3_000_000
BUILD_FIELDS = ("file", "url", "bytes", "sqlite_bytes", "sha256", "count", "built_at", "osm_timestamp")


def load_previous(src: str | None) -> dict[str, dict]:
    """Previous manifest (path or URL) -> {region id: entry}. Missing -> {}."""
    if not src:
        return {}
    try:
        if src.startswith(("http://", "https://")):
            req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
        else:
            data = json.loads(Path(src).read_text())
    except Exception:
        return {}
    return {r["id"]: r for r in data.get("regions", [])}


def load_builds(out_dir: Path) -> dict[str, dict]:
    builds = {}
    for p in sorted(out_dir.glob("*.json")):
        if p.name in ("manifest.json",):
            continue
        d = json.loads(p.read_text())
        if "sha256" in d and "id" in d:
            builds[d["id"]] = d
    return builds


def region_entry(r: Region) -> dict:
    simple = simplify_geometry(r.geometry)
    return {
        "id": r.id,
        "name": r.name,
        "parent": r.parent,
        "continent": r.continent,
        "country_code": r.country_code,
        "country_codes": r.country_codes,
        "iso3166_2": r.iso3166_2,
        "bbox": bbox(simple),
        "polygon": polygon_rings(simple),
        "pbf_url": r.pbf_url,
    }


def make_manifest(regions: list[Region], out_dir: Path, base_url: str, previous: dict[str, dict]) -> dict:
    builds = load_builds(out_dir)
    base_url = base_url.rstrip("/")
    entries = []
    for r in regions:
        e = region_entry(r)
        b = builds.get(r.id)
        if b:
            e.update({k: b.get(k) for k in BUILD_FIELDS if k != "url"})
            e["url"] = f"{base_url}/{b['file']}"
        elif r.id in previous and previous[r.id].get("url"):
            e.update({k: previous[r.id].get(k) for k in BUILD_FIELDS})
        entries.append(e)
    return {"schema_version": SCHEMA_VERSION, "generated_at": now_iso(), "base_url": base_url, "regions": entries}


def write_manifest(manifest: dict, out_dir: Path) -> list[Path]:
    """Write manifest.json; if it exceeds MANIFEST_MAX_BYTES, move polygons to
    regions.geojson (referenced by `polygons_url`) and keep bbox inline."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    text = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode()) <= MANIFEST_MAX_BYTES:
        path.write_text(text)
        return [path]
    geo = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": e["id"],
                "properties": {"id": e["id"]},
                "geometry": {"type": "MultiLineString", "coordinates": e.pop("polygon")},
            }
            for e in manifest["regions"]
        ],
    }
    manifest["polygons_url"] = f"{manifest['base_url']}/regions.geojson"
    gpath = out_dir / "regions.geojson"
    gpath.write_text(json.dumps(geo, separators=(",", ":")))
    path.write_text(json.dumps(manifest, separators=(",", ":"), ensure_ascii=False))
    return [path, gpath]
