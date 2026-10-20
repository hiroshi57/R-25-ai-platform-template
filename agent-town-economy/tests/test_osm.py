"""Real-OSM geography path: GeoJSON loading, projection, and end-to-end run."""
import json
import random

from sim.conditions import baseline
from sim.engine import Simulation
from sim.osm import latlon_to_local, load_places_from_geojson
from sim.policy import HeuristicPolicy
from analysis.validate import validate


def _sample_geojson(path):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [83.955, 28.209]},
             "properties": {"amenity": "restaurant"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [83.958, 28.210]},
             "properties": {"amenity": "cafe"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [83.960, 28.212]},
             "properties": {"tourism": "guest_house"}},
            {"type": "Feature", "geometry": {"type": "Polygon",
             "coordinates": [[[83.961, 28.213], [83.962, 28.213], [83.962, 28.214], [83.961, 28.213]]]},
             "properties": {"shop": "gift"}},
        ],
    }
    path.write_text(json.dumps(fc), encoding="utf-8")
    return str(path)


def test_projection_centre_is_origin():
    x, y = latlon_to_local(28.21, 83.95, 28.21, 83.95)
    assert abs(x) < 1e-6 and abs(y) < 1e-6


def test_projection_metres_are_reasonable():
    # ~0.001 deg latitude is ~111 m.
    _, y = latlon_to_local(28.211, 83.95, 28.21, 83.95)
    assert 100 < y < 125


def test_load_geojson_builds_places_with_menus(tmp_path):
    path = _sample_geojson(tmp_path / "town.geojson")
    places = load_places_from_geojson(path, random.Random(0))
    assert len(places) == 4
    assert any(p.priced for p in places)          # synthetic menus attached
    cats = {p.category for p in places}
    assert "restaurant" in cats and "guesthouse" in cats


def test_simulation_runs_on_osm_places(tmp_path):
    path = _sample_geojson(tmp_path / "town.geojson")
    places = load_places_from_geojson(path, random.Random(0))
    sim = Simulation(baseline(), HeuristicPolicy(random.Random(7)), seed=1, n_agents=10, places=places)
    result = sim.run(30)
    assert validate(result)["all_passed"]         # conservation holds on real geography too
