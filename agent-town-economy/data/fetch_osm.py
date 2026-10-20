"""Fetch real OSM footprints for Lakeside, Pokhara and write a GeoJSON.

Requires network access to the Overpass API. Run once; the resulting file is
then loaded offline by ``run.py --osm-geojson``.

    python data/fetch_osm.py                       # -> data/lakeside.geojson
    python data/fetch_osm.py --out data/town.geojson
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.osm import POKHARA_LAKESIDE_BBOX, fetch_overpass  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch OSM footprints via Overpass")
    ap.add_argument("--out", default="data/lakeside.geojson")
    ap.add_argument("--south", type=float, default=POKHARA_LAKESIDE_BBOX[0])
    ap.add_argument("--west", type=float, default=POKHARA_LAKESIDE_BBOX[1])
    ap.add_argument("--north", type=float, default=POKHARA_LAKESIDE_BBOX[2])
    ap.add_argument("--east", type=float, default=POKHARA_LAKESIDE_BBOX[3])
    args = ap.parse_args()

    path = fetch_overpass((args.south, args.west, args.north, args.east), out_path=args.out)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
