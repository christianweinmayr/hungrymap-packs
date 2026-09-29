"""Download a Geofabrik extract and turn it into a gzipped SQLite pack."""

from __future__ import annotations

import email.utils
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape

from .regions import Region
from .tags import AMENITIES, COLUMNS, decode_export_id, keep, to_row

SCHEMA_VERSION = 1
USER_AGENT = "hungry-map-packs/1 (+https://github.com)"

SCHEMA = """
CREATE TABLE places (
  id TEXT PRIMARY KEY,
  name TEXT,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  amenity TEXT,
  cuisine TEXT,
  opening_hours TEXT,
  opening_hours_kitchen TEXT,
  diet TEXT,
  website TEXT,
  menu_url TEXT,
  phone TEXT,
  brand TEXT,
  brand_wikidata TEXT,
  takeaway TEXT,
  outdoor_seating TEXT,
  name_en TEXT
);
CREATE INDEX places_lat_lon ON places (lat, lon);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request(url: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=60)


def fetch(url: str, dest: Path) -> Path:
    """Download url to dest unless the cached copy is at least as new as the
    server's Last-Modified. The file's mtime is set to Last-Modified."""
    remote_mtime = remote_size = None
    try:
        with _request(url, "HEAD") as r:  # follows Geofabrik's -latest -> -YYMMDD redirect
            lm = r.headers.get("Last-Modified")
            remote_mtime = email.utils.parsedate_to_datetime(lm).timestamp() if lm else None
            remote_size = int(r.headers["Content-Length"]) if r.headers.get("Content-Length") else None
    except Exception as e:  # offline: fall back to cache
        if dest.exists():
            log(f"HEAD failed ({e}); using cached {dest.name}")
            return dest
        raise
    if (
        dest.exists()
        and remote_mtime is not None
        and dest.stat().st_mtime >= remote_mtime
        and (remote_size is None or dest.stat().st_size == remote_size)
    ):
        log(f"cache hit {dest.name}")
        return dest
    log(f"downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with _request(url) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    part.replace(dest)
    if remote_mtime is not None:
        os.utime(dest, (remote_mtime, remote_mtime))
    return dest


def osm_timestamp(pbf: Path) -> str | None:
    try:
        out = subprocess.run(
            ["osmium", "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(pbf)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def extract_features(pbf: Path, workdir: Path):
    """Yield (id, lat, lon, tags) for every food POI in the extract."""
    filtered = workdir / "filtered.osm.pbf"
    seq = workdir / "features.geojsonseq"
    subprocess.run(
        ["osmium", "tags-filter", str(pbf), "nwr/amenity=" + ",".join(AMENITIES), "-o", str(filtered), "--overwrite"],
        check=True,
    )
    subprocess.run(
        [
            "osmium", "export", str(filtered),
            "-f", "geojsonseq",
            "--geometry-types=point,linestring,polygon",
            "--add-unique-id=type_id",
            "-x", "print_record_separator=false",
            "-o", str(seq), "--overwrite",
        ],
        check=True,
    )  # fmt: skip
    with open(seq) as f:
        for line in f:
            feat = json.loads(line)
            tags = feat.get("properties") or {}
            if not keep(tags):
                continue
            pid = decode_export_id(feat["id"])
            geom = feat["geometry"]
            if geom["type"] == "Point":
                lon, lat = geom["coordinates"]
            else:
                c = shape(geom).centroid
                lon, lat = c.x, c.y
            yield pid, lat, lon, tags


def write_sqlite(path: Path, rows: list[dict], meta: dict) -> None:
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    # Rough spatial ordering: better gzip ratio and locality for range scans.
    rows.sort(key=lambda r: (int((r["lat"] + 90) * 10), r["lon"], r["id"]))
    placeholders = ",".join("?" for _ in COLUMNS)
    db.executemany(
        f"INSERT INTO places ({','.join(COLUMNS)}) VALUES ({placeholders})",
        ([r[c] for c in COLUMNS] for r in rows),
    )
    db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [(k, str(v)) for k, v in meta.items() if v is not None])
    db.commit()
    db.execute("VACUUM")
    db.close()


def gzip_file(src: Path, dest: Path) -> None:
    # mtime=0 and no filename in the header: identical input -> identical bytes.
    with open(src, "rb") as fi, open(dest, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as fo:
            shutil.copyfileobj(fi, fo, length=1 << 20)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_region(region: Region, cache_dir: Path, out_dir: Path, keep_pbf: bool = True) -> dict:
    t0 = time.monotonic()
    pbf = fetch(region.pbf_url, cache_dir / f"{region.flat_id}.osm.pbf")
    t_dl = time.monotonic() - t0
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        tmp = Path(tmp)
        # Keyed by id: a closed way's area geometry (exported last) replaces its linestring.
        rows = list({pid: to_row(pid, lat, lon, tags) for pid, lat, lon, tags in extract_features(pbf, tmp)}.values())
        built_at = now_iso()
        ts = osm_timestamp(pbf)
        meta = {
            "region_id": region.id,
            "built_at": built_at,
            "osm_timestamp": ts,
            "count": len(rows),
            "schema_version": SCHEMA_VERSION,
            "country_code": region.country_code,
            "country_codes": ",".join(region.country_codes),
            "iso3166_2": region.iso3166_2,
        }
        db_path = tmp / f"{region.flat_id}.sqlite"
        write_sqlite(db_path, rows, meta)
        gz = out_dir / f"{region.flat_id}.sqlite.gz"
        gzip_file(db_path, gz)
        sqlite_bytes = db_path.stat().st_size
    if not keep_pbf:
        pbf.unlink(missing_ok=True)
    n = meta["count"]
    oh = sum(1 for r in rows if r["opening_hours"])
    ohk = sum(1 for r in rows if r["opening_hours_kitchen"])
    result = {
        "id": region.id,
        "file": gz.name,
        "bytes": gz.stat().st_size,
        "sqlite_bytes": sqlite_bytes,
        "sha256": sha256(gz),
        "count": n,
        "built_at": built_at,
        "osm_timestamp": ts,
        "stats": {
            "with_opening_hours": oh,
            "with_opening_hours_kitchen": ohk,
            "download_seconds": round(t_dl, 1),
            "build_seconds": round(time.monotonic() - t0 - t_dl, 1),
        },
    }
    (out_dir / f"{region.flat_id}.json").write_text(json.dumps(result, indent=2) + "\n")
    log(
        f"{region.id}: {n} places, {result['bytes'] / 1e6:.2f} MB gz "
        f"({sqlite_bytes / 1e6:.2f} MB sqlite), hours {oh / max(n, 1):.1%}, kitchen {ohk / max(n, 1):.1%}, "
        f"build {result['stats']['build_seconds']}s"
    )
    return result
