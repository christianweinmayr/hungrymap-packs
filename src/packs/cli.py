"""`packs` command line: regions | build | manifest."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .build import build_region, fetch, log
from .manifest import load_previous, make_manifest, write_manifest
from .regions import INDEX_URL, compute_regions, load_index, region_by_id

ROOT = Path(__file__).resolve().parents[2]  # packs/

DEFAULT_BASE_URL = "https://github.com/christianweinmayr/hungrymap-packs/releases/download/packs-latest"


def default_base_url() -> str:
    return os.environ.get("PACKS_BASE_URL") or DEFAULT_BASE_URL


def load_regions(cache: Path):
    return compute_regions(load_index(fetch(INDEX_URL, cache / "index-v1.json")))


def cmd_regions(args) -> int:
    regions = load_regions(args.cache)
    for r in regions:
        if args.continent and r.continent != args.continent:
            continue
        print(f"{r.id}\t{r.continent}\t{','.join(r.country_codes)}\t{r.name}")
    return 0


def cmd_build(args) -> int:
    regions = load_regions(args.cache)
    if args.all:
        todo = [r for r in regions if not args.continent or r.continent == args.continent]
    elif args.region:
        todo = [region_by_id(regions, rid) for rid in args.region]
    else:
        log("specify --region or --all")
        return 2
    previous = load_previous(args.previous)
    failed = []
    unchanged = 0
    t0 = time.monotonic()
    for i, r in enumerate(todo, 1):
        log(f"[{i}/{len(todo)}] {r.id}")
        try:
            res = build_region(r, args.cache, args.out, keep_pbf=not args.no_keep_pbf, previous=previous.get(r.id))
            unchanged += bool(res.get("unchanged"))
        except Exception as e:  # keep going; report at the end
            log(f"FAILED {r.id}: {e}")
            failed.append(r.id)
    log(f"built {len(todo) - len(failed)}/{len(todo)} ({unchanged} unchanged) in {time.monotonic() - t0:.0f}s")
    if failed:
        log("failed: " + " ".join(failed))
    return 1 if failed else 0


def cmd_manifest(args) -> int:
    regions = load_regions(args.cache)
    manifest = make_manifest(regions, args.out, args.base_url or default_base_url(), load_previous(args.previous))
    paths = write_manifest(manifest, args.out)
    built = sum(1 for e in manifest["regions"] if e.get("url"))
    for p in paths:
        log(f"wrote {p} ({p.stat().st_size / 1e6:.2f} MB)")
    log(f"{len(manifest['regions'])} regions, {built} with packs")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="packs")
    ap.add_argument("--cache", type=Path, default=ROOT / "cache", help="pbf/index cache dir")
    ap.add_argument("--out", type=Path, default=ROOT / "out", help="output dir")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("regions", help="list pack regions")
    p.add_argument("--continent")
    p.set_defaults(fn=cmd_regions)

    p = sub.add_parser("build", help="build packs")
    p.add_argument("--region", action="append", help="region id, e.g. austria or germany/bayern (repeatable)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--continent", help="with --all: only this Geofabrik continent")
    p.add_argument("--no-keep-pbf", action="store_true", help="delete each pbf after building (CI disk space)")
    p.add_argument("--previous", help="previous manifest.json (path or URL); regions whose content hash is unchanged are not rewritten")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("manifest", help="write out/manifest.json")
    p.add_argument("--base-url", help="default: $PACKS_BASE_URL or the repo's packs-latest release")
    p.add_argument("--previous", help="previous manifest.json (path or URL) to carry over packs not rebuilt")
    p.set_defaults(fn=cmd_manifest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
