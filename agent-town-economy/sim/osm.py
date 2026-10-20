"""Real OpenStreetMap geography for the town registry.

Loads business footprints for Lakeside, Pokhara from OpenStreetMap and projects
lat/lon to local metres, replacing the synthetic grid used by default. OSM has
no prices, so priced menus are synthesised on top of the real footprints via
``world.attach_synthetic_menus`` -- i.e. real geography, synthetic economy.

Fetching (network) is separated from loading (offline) so tests and reruns work
without a live Overpass connection:
    python data/fetch_osm.py                 # writes data/lakeside.geojson
    python run.py --osm-geojson data/lakeside.geojson --condition baseline
"""
from __future__ import annotations

import json
import math
import random
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from .world import Place, attach_synthetic_menus

# Approximate bounding box for Lakeside, Pokhara (south, west, north, east).
POKHARA_LAKESIDE_BBOX = (28.198, 83.942, 28.225, 83.968)

# OSM tag -> our seven categories.
_CATEGORY_MAP = {
    ("amenity", "restaurant"): "restaurant",
    ("amenity", "cafe"): "cafe",
    ("amenity", "bar"): "bar",
    ("amenity", "pub"): "bar",
    ("amenity", "fast_food"): "streetfood",
    ("amenity", "food_court"): "streetfood",
    ("shop", "bakery"): "bakery",
    ("tourism", "guest_house"): "guesthouse",
    ("tourism", "hotel"): "guesthouse",
    ("tourism", "hostel"): "guesthouse",
}


def latlon_to_local(lat: float, lon: float, center_lat: float, center_lon: float) -> Tuple[float, float]:
    """Equirectangular projection to metres relative to a centre point."""
    r = 6371000.0
    x = math.radians(lon - center_lon) * r * math.cos(math.radians(center_lat))
    y = math.radians(lat - center_lat) * r
    return (x, y)


def _map_category(tags: Dict[str, str]) -> str:
    for (k, v), cat in _CATEGORY_MAP.items():
        if tags.get(k) == v:
            return cat
    if "shop" in tags:
        return "shop"
    return "shop"


def _first_point(geometry: dict) -> Optional[Tuple[float, float]]:
    """Return (lon, lat) for any GeoJSON geometry by digging to the first pair."""
    coords = geometry.get("coordinates")

    def dig(c):
        if isinstance(c, (int, float)):
            return None
        if len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
            return (float(c[0]), float(c[1]))
        for sub in c:
            r = dig(sub)
            if r is not None:
                return r
        return None

    return dig(coords) if coords is not None else None


def load_places_from_geojson(
    path: str,
    rng: random.Random,
    center: Optional[Tuple[float, float]] = None,
) -> List[Place]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    features = data.get("features", [])

    parsed: List[Tuple[float, float, str]] = []  # (lon, lat, category)
    for f in features:
        pt = _first_point(f.get("geometry", {}))
        if pt is None:
            continue
        lon, lat = pt
        parsed.append((lon, lat, _map_category(f.get("properties", {}))))

    if not parsed:
        raise ValueError(f"no usable features in {path!r}")

    if center is None:
        center_lat = sum(lat for _, lat, _ in parsed) / len(parsed)
        center_lon = sum(lon for lon, _, _ in parsed) / len(parsed)
    else:
        center_lat, center_lon = center

    places: List[Place] = []
    for i, (lon, lat, cat) in enumerate(parsed):
        x, y = latlon_to_local(lat, lon, center_lat, center_lon)
        places.append(Place(place_id=i, category=cat, x=x, y=y))

    attach_synthetic_menus(places, rng)
    return places


# --- network fetch (not exercised by tests) --------------------------------
def overpass_query(bbox: Tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return (
        "[out:json][timeout:60];("
        f'node["amenity"~"restaurant|cafe|bar|pub|fast_food|food_court"]({s},{w},{n},{e});'
        f'node["shop"]({s},{w},{n},{e});'
        f'node["tourism"~"guest_house|hotel|hostel"]({s},{w},{n},{e});'
        f'way["amenity"~"restaurant|cafe|bar|pub|fast_food"]({s},{w},{n},{e});'
        ");out center tags;"
    )


def overpass_json_to_geojson(raw: dict) -> dict:
    """Convert a raw Overpass JSON response into a GeoJSON FeatureCollection."""
    features = []
    for el in raw.get("elements", []):
        if el.get("type") == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:  # way/relation with 'center'
            c = el.get("center", {})
            lon, lat = c.get("lon"), c.get("lat")
        if lon is None or lat is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": el.get("tags", {}),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def fetch_overpass(
    bbox: Tuple[float, float, float, float] = POKHARA_LAKESIDE_BBOX,
    out_path: str = "data/lakeside.geojson",
    endpoint: str = "https://overpass-api.de/api/interpreter",
) -> str:
    """Query Overpass and write a GeoJSON FeatureCollection. Requires network.

    A descriptive User-Agent and an Accept header are required: the public
    Overpass instance returns HTTP 406 without them.
    """
    query = overpass_query(bbox)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "User-Agent": "agent-town-economy/1.0 (arXiv:2609.11108 reproduction)",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = json.load(resp)

    out = overpass_json_to_geojson(raw)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    return out_path
