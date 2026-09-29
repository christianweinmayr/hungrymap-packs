# OSM download packs

Turns Geofabrik extracts into small per-region SQLite packs of food places
(opening hours, kitchen hours, diet tags, website/menu) plus a `manifest.json`
the Hungry Map iOS app downloads. Data © OpenStreetMap contributors, ODbL.

## Usage

Requires `uv` and `osmium-tool` (`brew install osmium-tool` / `apt install osmium-tool`).

```sh
cd packs
uv run packs regions [--continent europe]          # list pack regions
uv run packs build --region austria --region germany/bayern
uv run packs build --all [--continent europe] [--no-keep-pbf]
uv run packs manifest [--base-url URL] [--previous old-manifest.json|URL]
uv run pytest
```

- Outputs go to `out/`: `<flat-id>.sqlite.gz`, `<flat-id>.json` (build result), `manifest.json`.
- Downloads are cached in `cache/` (`<flat-id>.osm.pbf`, `index-v1.json`) and
  re-downloaded only when Geofabrik's `Last-Modified`/`Content-Length` differ.
- `base_url` = `--base-url` > `$PACKS_BASE_URL` >
  `https://github.com/christianweinmayr/hungrymap-packs/releases/download/packs-latest`.
- `--previous` carries over pack fields (url, sha256, ...) for regions that were
  not rebuilt this run, so a failed region keeps its last good pack.

This directory is the root of the public repo `christianweinmayr/hungrymap-packs`.
CI: `.github/workflows/packs.yml` runs Mondays 03:00 UTC (and on demand), one
job per Geofabrik continent, then a `publish` job uploads all packs and
`manifest.json` to the `packs-latest` release (`gh release upload --clobber`).
Set the repo variable `PACKS_BASE_URL` to host elsewhere (e.g. R2).

## Regions

Source: <https://download.geofabrik.de/index-v1.json>.

- Every Geofabrik country is one pack. If the country has sub-regions, each
  **direct** child is a pack instead (germany → 16 states, us → states, france →
  regions, ...). Deeper levels are not split (bayern stays one pack).
- Region ids are paths: `austria`, `germany/bayern`, `us/california`,
  `russia/kaliningrad`. Asset/file names flatten `/` to `__`: `germany__bayern.sqlite.gz`.
- Skipped: Geofabrik composites that duplicate other extracts (`dach`, `alps`,
  `great-britain`, `britain-and-ireland`, `sea`, `us-west` etc.,
  `south-africa-and-lesotho`), `azores` (inside `portugal`), `crimean-fed-district`
  (inside `ukraine`).
- Some Geofabrik extracts contain others (`germany/brandenburg` includes Berlin,
  `germany/niedersachsen` includes Bremen, `china/guangdong` includes Hong Kong,
  `morocco` includes Ceuta/Melilla...). Packs may therefore overlap.

## Pack format (schema_version 1)

`<flat-id>.sqlite.gz` = gzip (level 9) of a VACUUMed SQLite 3 database:

```sql
CREATE TABLE places (
  id TEXT PRIMARY KEY,           -- OSM type + id: "n123" (node), "w456" (way), "r789" (relation)
  name TEXT,                     -- tag name; NULL allowed (only kept if opening_hours is set)
  lat REAL NOT NULL,             -- WGS84; nodes: position, ways/relations: polygon centroid
  lon REAL NOT NULL,
  amenity TEXT,                  -- restaurant|cafe|fast_food|bar|pub|biergarten|food_court|ice_cream
  cuisine TEXT,                  -- raw OSM cuisine value, semicolon list ("regional;bavarian")
  opening_hours TEXT,            -- raw OSM opening_hours
  opening_hours_kitchen TEXT,    -- raw OSM opening_hours:kitchen
  diet TEXT,                     -- diet:* keys with value yes|only, sorted, comma list:
                                 --   "gluten_free,vegan:only,vegetarian" (":only" suffix for only)
  website TEXT,                  -- website, else contact:website
  menu_url TEXT,                 -- website:menu, else menu:url
  phone TEXT,                    -- phone, else contact:phone
  brand TEXT,                    -- brand
  brand_wikidata TEXT,           -- brand:wikidata ("Q38076")
  takeaway TEXT,                 -- raw takeaway (yes|no|only|...)
  outdoor_seating TEXT,          -- raw outdoor_seating (yes|no|...)
  name_en TEXT                   -- name:en
);
CREATE INDEX places_lat_lon ON places (lat, lon);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
```

All values are TEXT except lat/lon; empty strings are stored as NULL.

`meta` rows (all values TEXT):

| key | example |
|---|---|
| `region_id` | `germany/bayern` |
| `built_at` | `2026-09-28T21:03:40Z` |
| `osm_timestamp` | `2026-09-27T20:23:36Z` (replication timestamp of the extract; may be absent) |
| `count` | `37365` |
| `schema_version` | `1` |
| `country_code` | `DE` (ISO 3166-1 alpha-2, primary; use for holiday calendars) |
| `country_codes` | `DE` (comma list; multi-country extracts, e.g. `AE,QA,OM,BH,KW`) |
| `iso3166_2` | `DE-BY` (ISO 3166-2 subdivision; row absent for whole-country packs and sub-regions without one, e.g. italy/sud) |

Inclusion: any node/way/relation (multipolygon) with one of the amenities above
that has a `name` or an `opening_hours`. Ways/relations are reduced to their
centroid.

## manifest.json

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-28T21:03:42Z",
  "base_url": "https://github.com/christianweinmayr/hungrymap-packs/releases/download/packs-latest",
  "regions": [
    {
      "id": "austria",
      "name": "Austria",
      "parent": null,
      "continent": "europe",
      "country_code": "AT",
      "country_codes": ["AT"],
      "iso3166_2": null,
      "bbox": [9.5268, 46.3698, 17.1641, 49.024],
      "polygon": [[[15.962, 48.802], [16.095, 48.75], "... 277 points ...", [15.962, 48.802]]],
      "pbf_url": "https://download.geofabrik.de/europe/austria-latest.osm.pbf",
      "file": "austria.sqlite.gz",
      "bytes": 2803803,
      "sqlite_bytes": 5582848,
      "sha256": "2ef456f7...",
      "count": 33965,
      "built_at": "2026-09-28T21:03:00Z",
      "osm_timestamp": "2026-09-27T20:23:36Z",
      "url": "https://github.com/christianweinmayr/hungrymap-packs/releases/download/packs-latest/austria.sqlite.gz"
    }
  ]
}
```

- Every region is listed. Regions without a built pack have **no** `file`, `url`,
  `bytes`, `sqlite_bytes`, `sha256`, `count`, `built_at`, `osm_timestamp` keys
  ("pack not available yet").
- `iso3166_2`: ISO 3166-2 subdivision (`"DE-BY"`, `"US-CA"`, `"ES-MD"`, `"AU-VIC"`,
  `"CA-QC"`) for sub-country packs, else `null`. Geofabrik macro-regions that are
  not ISO subdivisions (italy/nord-est, france/* old regions, japan/kanto, ...) are
  `null`; use `country_code` for them. Whole-country packs are `null` except
  `canary-islands` (`ES-CN`).
- `parent`: country id for sub-country packs (`"germany"`), `null` for whole-country packs.
- `bytes` = size of the `.gz` (show in "Download X (N MB)?"); `sqlite_bytes` = unpacked size.
- `sha256` is of the `.gz` file. Assets are replaced in place weekly, so if a
  download's hash doesn't match, refetch `manifest.json` and retry.
- `bbox` = `[minLon, minLat, maxLon, maxLat]` of `polygon`.
- `polygon` = list of rings, each a closed list of `[lon, lat]` (3 decimals).
  Test with the **even-odd rule across all rings** (a point inside an odd number
  of rings is in the region; handles islands and holes). It is Geofabrik's
  extract boundary simplified with Douglas-Peucker (topology preserving,
  tolerance from 0.005° raised per region until ≤ 300 points; median ~140).
  Geofabrik boundaries run slightly outside the real border and some extracts
  contain others, so a point can match several regions: pick the match with the
  smallest bbox area (Berlin wins over Brandenburg, Hong Kong over Guangdong).
  Checked: Salzburg → austria, Basel → switzerland, NYC → us/new-york.
- If the manifest ever exceeds 3 MB, polygons move to `regions.geojson`
  (`FeatureCollection`, feature `id` = region id, geometry `MultiLineString` of
  the same rings) and the manifest gets `"polygons_url"`; `bbox` stays inline.
  Currently it is ~1.1 MB, so polygons are inline.

## Lean weekly builds

Each pack carries a `content_hash` (SHA-256 over its sorted rows + country metadata, ignoring
timestamps). The weekly build passes the published manifest as `--previous`; regions whose content
hash is unchanged are not rewritten or uploaded, and keep their previous `sha256`, so installed apps
don't re-download them either. Geofabrik refreshes every extract daily, so source timestamps can't
be used for skipping — only the content can.

## Publishing

`packs/` in the private `find-food` monorepo is the source of truth; the public
`christianweinmayr/hungrymap-packs` repo mirrors it (its root = this directory) and runs the workflow.
