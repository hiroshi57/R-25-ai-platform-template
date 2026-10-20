"""The spatial world: places, priced menus, and a visibility radius.

Modelled on the paper's Lakeside, Pokhara setting (2609.11108, section 3):
762 registered places across seven categories, most carrying priced menus
(the paper reports 735 priced places / 3,981 items).

Two registry sources are supported:
  * synthetic  -- coordinates on a metric grid (default, no network needed)
  * real OSM   -- footprints loaded from OpenStreetMap via ``sim.osm`` and
                  passed in as ``places``; priced menus are synthesised on top
                  of the real geography (OSM has no prices).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CATEGORIES = [
    "restaurant",
    "cafe",
    "shop",
    "guesthouse",
    "bar",
    "bakery",
    "streetfood",
]

# Paper-reported registry scale (used by the synthetic generator).
N_PLACES = 762
N_PRICED_PLACES = 735
TARGET_MENU_ITEMS = 3981

TOWN_SIZE_M = 2000.0  # ~2km square, Lakeside strip scale
VISIBILITY_RADIUS_M = 80.0


@dataclass
class MenuItem:
    name: str
    price: int  # integer NPR
    reprices: int = 0


@dataclass
class Place:
    place_id: int
    category: str
    x: float
    y: float
    menu: List[MenuItem] = field(default_factory=list)
    owner_id: Optional[int] = None
    wage_per_shift: int = 0
    revenue: int = 0
    n_transactions: int = 0

    @property
    def priced(self) -> bool:
        return len(self.menu) > 0

    def cheapest(self) -> Optional[MenuItem]:
        return min(self.menu, key=lambda m: m.price) if self.menu else None


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def attach_synthetic_menus(
    places: List[Place],
    rng: random.Random,
    target_items: int = TARGET_MENU_ITEMS,
    priced_fraction: float = N_PRICED_PLACES / N_PLACES,
) -> None:
    """Give a fraction of places a priced menu, distributing the item budget so
    the total lands on ``target_items`` (when there are enough priced places).
    Shared by both the synthetic and the OSM registry paths."""
    n = len(places)
    if n == 0:
        return
    n_priced = min(n, max(1, round(priced_fraction * n)))
    priced = rng.sample(places, n_priced)
    counts = [1] * n_priced
    remaining = max(0, target_items - n_priced)
    for _ in range(remaining):
        counts[rng.randrange(n_priced)] += 1
    for place, c in zip(priced, counts):
        base = rng.randint(80, 600)
        place.menu = [
            MenuItem(name=f"item_{place.place_id}_{i}", price=max(10, base + rng.randint(-40, 300)))
            for i in range(c)
        ]
        place.wage_per_shift = rng.randint(300, 900)


def build_synthetic_registry(rng: random.Random) -> List[Place]:
    places = [
        Place(
            place_id=pid,
            category=rng.choice(CATEGORIES),
            x=rng.uniform(0, TOWN_SIZE_M),
            y=rng.uniform(0, TOWN_SIZE_M),
        )
        for pid in range(N_PLACES)
    ]
    attach_synthetic_menus(places, rng)
    return places


class World:
    def __init__(self, rng: random.Random, places: Optional[List[Place]] = None) -> None:
        self.rng = rng
        source = places if places is not None else build_synthetic_registry(rng)
        self.places: Dict[int, Place] = {p.place_id: p for p in source}

    def total_menu_items(self) -> int:
        return sum(len(p.menu) for p in self.places.values())

    def priced_places(self) -> List[Place]:
        return [p for p in self.places.values() if p.priced]

    def places_within(self, pos: Tuple[float, float], radius: float = VISIBILITY_RADIUS_M) -> List[Place]:
        return [p for p in self.places.values() if _distance(pos, (p.x, p.y)) <= radius]

    def nearest_priced(self, pos: Tuple[float, float]) -> Optional[Place]:
        priced = self.priced_places()
        if not priced:
            return None
        return min(priced, key=lambda p: _distance(pos, (p.x, p.y)))
