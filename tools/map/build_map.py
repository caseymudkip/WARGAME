"""Build data/map/world_map.json: the global province map.

    pip install shapely pyproj pyshp       # dev-only; the engine itself is stdlib
    python tools/map/build_map.py [--cache DIR]

Inputs are pinned and hash-checked; downloads are cached (default ~/.cache/wargame-map):

  Natural Earth 5.1.2, public domain (github.com/nvkelso/natural-earth-vector @ NE_COMMIT)
      admin-1 states/provinces, populated places, ports, airports, physical geography regions
  WRI Global Power Plant Database 1.3, CC BY 4.0 (github.com/wri/global-power-plant-database @ GPPD_COMMIT)
  DeepStateMap occupied territory of Ukraine on 2026-01-01 (vendored: data/raw/map/deepstate_20260101.geojson,
      from github.com/cyterat/deepstate-map-data @ de86af1, GPL-3.0 archive of DeepStateMap.Live)
  data/curated/map_control.json   de jure ownership fixes, de facto control, naval bases

The output holds geography and facts only (areas, adjacency, terrain, cities, ports, airfields,
power plants, control). Model quantities that depend on a snapshot (population per province,
industry, infrastructure) are computed by the engine's loader.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

import shapefile
from pyproj import Geod
from shapely import STRtree, dwithin, make_valid, unary_union
from shapely.geometry import LineString, Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, split

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "map" / "world_map.json"
CONTROL = ROOT / "data" / "curated" / "map_control.json"
DEEPSTATE = ROOT / "data" / "raw" / "map" / "deepstate_20260101.geojson"

NE_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"
GPPD_COMMIT = "7a91cfbb2a4e272597acbc00506d61fc1ec73b3d"
NE_LAYERS = {
    "ne_10m_admin_1_states_provinces": "10m_cultural",
    "ne_10m_populated_places": "10m_cultural",
    "ne_10m_ports": "10m_cultural",
    "ne_10m_airports": "10m_cultural",
    "ne_10m_geography_regions_polys": "10m_physical",
}
SHA256 = {
    "ne_10m_admin_1_states_provinces.shp": "c6f5c8b4b1320d9417033762419c6df1eb423989cd880fba78ea0b1e3522cbe4",
    "ne_10m_admin_1_states_provinces.shx": "37a9e2bc79ed31d3bdea3cb62d928f77281a1c88d645cd33430231c75dbcf350",
    "ne_10m_admin_1_states_provinces.dbf": "7b3244333680d6aec58cc49bde9484177a0baa44c974ec9d371a8f7f1cdb5359",
    "ne_10m_populated_places.shp": "f4073365d248dfe35a44cca6715bf5b4812fa723294ed6a37043d7a3e09cc998",
    "ne_10m_populated_places.shx": "cdbad984f9e40948f2b77c87f73319477775d7bc95d47112455abea30ad39757",
    "ne_10m_populated_places.dbf": "eff834a8727e06f28fc52b04393c462d074aecf9a7acd1b870e766240a995b6c",
    "ne_10m_ports.shp": "dd480990277f96967d88b366008e5819124d7b8391c4189fa588bb47670aeda2",
    "ne_10m_ports.shx": "74566591c840ae462eaac8ef86407361a3d88110bc3350c641e1c47c38653a8b",
    "ne_10m_ports.dbf": "176db7241564930d921bc175db0a91aa474d96525e84a0671e0cf54be2e51707",
    "ne_10m_airports.shp": "b5de68f0db63f141f4a26683e624948ed12a697dd493d58a2070ef50a781ae7f",
    "ne_10m_airports.shx": "d5fac8d0de6dd4b216e458e2dcaf97f2541a68f15b30ac74aafac6872b89fee3",
    "ne_10m_airports.dbf": "7525737e431f0cad72b0d535dcc6da23ad8366c9c5a1b41b25d6d64251ef44c8",
    "ne_10m_geography_regions_polys.shp": "d7468f7967368ae2180a9d4cd4ed5de302ee53fb3615268a212ffeb3a33bf042",
    "ne_10m_geography_regions_polys.shx": "9235923e01fa16cff375e3c0baf0ecb6d253346c006a5ba3b1ab41f9fe52d2f0",
    "ne_10m_geography_regions_polys.dbf": "142d853c15315e6c553ac9ca7665e65be80a1cbc821774688b143d4d4acf8801",
    "global_power_plant_database.csv": "4b1f93e0fd93664f18684d9b05d0a52ed9658c6a8cf0d21ff2520791379ba7fc",
}

GEOD = Geod(ellps="WGS84")

# Over-fragmented countries (many tiny units) are merged to Natural Earth's 'region' field.
MERGE_MIN_UNITS = 60
MERGE_MAX_MEAN_KM2 = 3_000

# Splitting a province along an occupation line: pieces smaller than this stay with their neighbour.
MIN_PIECE_KM2 = 300
MIN_PIECE_SHARE = 0.03

ADJACENCY_TOLERANCE_DEG = 1e-5
COAST_EXPOSED_MIN_DEG = 0.02       # Boundary not shared with another province => coast (or lake shore).
SEA_LINK_MAX_KM = 250             # Crossings for amphibious and naval movement (Taiwan Strait 130-180 km).
SEA_LINK_SEARCH_DEG = 2.4
SEA_LINKS_PER_PROVINCE = 8
POINT_SNAP_DEG = 0.15              # Coastal points (ports, harbour cities) can sit just offshore.

URBAN_CENTER_MIN_METRO = 1_000_000
ENERGY_MIN_MW = 2_000

# Natural Earth physical region classes -> engine terrain.
REGION_TERRAIN = {
    "Range/mtn": "mountains", "Desert": "desert", "Tundra": "arctic", "Wetlands": "marsh", "Delta": "marsh",
    "Plateau": "hills", "Foothills": "hills", "Plain": "plains", "Lowland": "plains", "Basin": "plains",
    "Valley": "plains", "Depression": "plains",
}
TERRAIN_MIN_SHARE = 0.35
URBAN_TERRAIN_DENSITY = 1_000     # people/km2 in cities alone...
URBAN_TERRAIN_MAX_KM2 = 5_000     # ...in a compact province => urban terrain.


# --- inputs -------------------------------------------------------------------------------------

def fetch(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    urls = {f"{layer}.{ext}": f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_COMMIT}/{folder}/{layer}.{ext}"
            for layer, folder in NE_LAYERS.items() for ext in ("shp", "shx", "dbf")}
    urls["global_power_plant_database.csv"] = (
        f"https://raw.githubusercontent.com/wri/global-power-plant-database/{GPPD_COMMIT}/output_database/global_power_plant_database.csv")
    for name, url in urls.items():
        path = cache / name
        if not path.exists():
            print(f"downloading {name}")
            urllib.request.urlretrieve(url, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != SHA256[name]:
            raise SystemExit(f"{name}: sha256 {digest} does not match the pinned input")


def km2(geom: BaseGeometry) -> float:
    return abs(GEOD.geometry_area_perimeter(geom)[0]) / 1e6


def valid(geom: BaseGeometry) -> BaseGeometry:
    return geom if geom.is_valid else make_valid(geom)


def polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Drop any line/point debris an overlay left behind."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else geom


# --- provinces --------------------------------------------------------------------------------------

class Unit:
    def __init__(self, code: str, name: str, iso: str, region: str, geom: BaseGeometry, kind: str = "") -> None:
        self.codes = [code]
        self.name = name
        self.kind = kind
        self.iso = iso
        self.region = region
        self.geom = geom
        self.piece: str | None = None  # Set when a province is split along an occupation line.
        self.control: dict[str, str] = {}  # snapshot -> controller, when different from owner.
        self.control_basis: dict[str, str] = {}


def load_units(cache: Path, control: dict[str, Any]) -> list[Unit]:
    codes = {k: v for k, v in control["ne_country_codes"].items() if not k.startswith("_")}
    fixes = {k: v for k, v in control["owner_fixes"].items() if not k.startswith("_")}
    units = []
    for sr in shapefile.Reader(str(cache / "ne_10m_admin_1_states_provinces")).iterShapeRecords():
        rec = sr.record
        iso = codes.get(rec["adm0_a3"], rec["adm0_a3"])
        code = rec["adm1_code"]
        if code in fixes:
            iso = fixes[code]["owner"]
        name = rec["name_en"] or rec["name"] or code
        units.append(Unit(code, name, iso, rec["region"] or "", valid(shape(sr.shape.__geo_interface__)), rec["type_en"] or ""))
    return units


def disambiguate(units: list[Unit]) -> None:
    """Same name twice in one country (Washington state vs D.C., Moscow city vs oblast): add the unit type."""
    seen: dict[tuple[str, str], list[Unit]] = defaultdict(list)
    for u in units:
        seen[(u.iso, u.name)].append(u)
    for group in seen.values():
        if len(group) > 1:
            for k, u in enumerate(group):
                u.name = f"{u.name} ({u.kind or k + 1})"


def merge_fragmented(units: list[Unit], exclusions: set[str]) -> list[Unit]:
    by_iso: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        by_iso[u.iso].append(u)
    out: list[Unit] = []
    for iso, group in sorted(by_iso.items()):
        mean = sum(km2(u.geom) for u in group) / len(group)
        if iso in exclusions or len(group) <= MERGE_MIN_UNITS or mean >= MERGE_MAX_MEAN_KM2:
            out.extend(group)
            continue
        regions: dict[str, list[Unit]] = defaultdict(list)
        for u in group:
            regions[u.region or f"{iso} (other)"].append(u)
        for region, members in sorted(regions.items()):
            merged = Unit(members[0].codes[0], region, iso, region, valid(unary_union([m.geom for m in members])))
            merged.codes = sorted(c for m in members for c in m.codes)
            out.append(merged)
        print(f"merged {iso}: {len(group)} units -> {len(regions)} regions")
    return out


def donbas_2015(units: list[Unit], control: dict[str, Any], z26: BaseGeometry) -> BaseGeometry:
    spec = control["donbas_contact_line_2015"]
    donbas = unary_union([u.geom for u in units if set(u.codes) & {"UKR-327", "UKR-329"}])
    pieces = split(donbas, LineString(spec["line"]))
    refs = [Point(p) for p in spec["occupied_reference_points"]]
    held = [g for g in pieces.geoms if any(g.contains(r) for r in refs)]
    return valid(unary_union(held)).intersection(z26)  # Clipped: the 2014 zone lies inside the 2026 one.


def split_occupied(units: list[Unit], zones: dict[str, BaseGeometry], owner: str, controller: str) -> list[Unit]:
    """Split owner's provinces along the occupation zones of every snapshot."""
    labels = {"2021": "occupied since 2014", "2026": "occupied since 2022"}
    z21, z26 = zones["2021"], zones["2026"]
    out: list[Unit] = []
    for u in units:
        if u.iso != owner or not (u.geom.intersects(z26) or u.geom.intersects(z21)):
            out.append(u)
            continue
        total = km2(u.geom)
        candidates = {
            "2021": polygonal(u.geom.intersection(z21)),
            "2026": polygonal(u.geom.intersection(z26).difference(z21)),
            "free": polygonal(u.geom.difference(z26).difference(z21)),
        }
        sizes = {k: km2(g) if not g.is_empty else 0.0 for k, g in candidates.items()}
        keep = [k for k, a in sizes.items() if a >= MIN_PIECE_KM2 and a >= MIN_PIECE_SHARE * total]
        if len(keep) <= 1:
            # Not worth splitting: control follows the majority of the province's area.
            if sizes["2021"] > total / 2:
                u.control.update({"2021": controller, "2026": controller})
            elif sizes["2021"] + sizes["2026"] > total / 2:
                u.control["2026"] = controller
            for snap in u.control:
                u.control_basis[snap] = f"majority of the province occupied ({labels.get(snap, snap)})"
            out.append(u)
            continue
        biggest = max(keep, key=lambda k: sizes[k])
        leftovers = [candidates[k] for k in candidates if k not in keep and sizes[k] > 0]
        for key in keep:
            geom = candidates[key]
            if key == biggest and leftovers:
                geom = valid(unary_union([geom, *leftovers]))
            piece = Unit(u.codes[0], u.name, u.iso, u.region, geom)
            piece.codes = list(u.codes)
            piece.piece = key
            piece.name = f"{u.name} ({'Ukrainian-held' if key == 'free' else labels[key]})"
            if key == "2021":
                piece.control.update({"2021": controller, "2026": controller})
            elif key == "2026":
                piece.control["2026"] = controller
            for snap in piece.control:
                piece.control_basis[snap] = "DeepStateMap 2026-01-01" if snap == "2026" else "2015-2022 line of contact (approximate)"
            out.append(piece)
        print(f"split {u.name}: {', '.join(f'{k} {sizes[k]:,.0f} km2' for k in keep)}")
    return out


# --- attributes -----------------------------------------------------------------------------------

def assign_points(points: list[tuple[float, float]], geoms: list[BaseGeometry], tree: STRtree,
                  snap: float = POINT_SNAP_DEG) -> list[int | None]:
    """Index of the province containing each point (or the nearest within `snap` degrees)."""
    out: list[int | None] = []
    for lon, lat in points:
        pt = Point(lon, lat)
        hit = next((int(i) for i in tree.query(pt) if geoms[int(i)].contains(pt)), None)
        if hit is None:
            near = tree.query_nearest(pt, max_distance=snap)
            hit = int(near[0]) if len(near) else None
        out.append(hit)
    return out


def terrain_for(geom: BaseGeometry, lat: float, regions: list[tuple[str, BaseGeometry]], rtree: STRtree) -> tuple[str, str]:
    shares: dict[str, float] = defaultdict(float)
    area = geom.area
    for i in rtree.query(geom):
        cls, rgeom = regions[int(i)]
        terrain = REGION_TERRAIN.get(cls)
        if terrain and rgeom.intersects(geom):
            shares[terrain] += valid(rgeom).intersection(geom).area / area
    if shares:
        terrain, share = max(shares.items(), key=lambda kv: kv[1])
        if share >= TERRAIN_MIN_SHARE:
            return terrain, f"Natural Earth physical region ({share:.0%} of area)"
    if abs(lat) >= 66:
        return "arctic", "latitude >= 66"
    if abs(lat) >= 55:
        return "forest", "latitude 55-66 (boreal)"
    if abs(lat) < 10:
        return "jungle", "latitude < 10 (tropical)"
    return "plains", "default"


def build(cache: Path) -> dict[str, Any]:
    control = json.loads(CONTROL.read_text())
    units = load_units(cache, control)
    units = merge_fragmented(units, set(control["merge_exclusions"]["countries"]))

    z26 = valid(shape(json.loads(DEEPSTATE.read_text())["features"][0]["geometry"]))
    crimea = unary_union([u.geom for u in units if set(u.codes) & {"RUS-283", "RUS-5482"}])
    z21 = valid(unary_union([crimea, donbas_2015(units, control, z26)]))
    units = split_occupied(units, {"2021": z21, "2026": z26}, owner="UKR", controller="RUS")

    for snap, entries in control["control"].items():
        for u in units:
            for code in u.codes:
                if code in entries and u.piece is None:
                    u.control[snap] = entries[code]["controller"]
                    u.control_basis[snap] = entries[code]["basis"]

    disambiguate(units)
    units.sort(key=lambda u: (u.iso, u.name, u.piece or ""))
    geoms = [u.geom for u in units]
    tree = STRtree(geoms)
    n = len(units)
    print(f"{n} provinces")

    # Land adjacency.
    neighbors: list[set[int]] = [set() for _ in range(n)]
    for i, g in enumerate(geoms):
        for j in tree.query(g, predicate="dwithin", distance=ADJACENCY_TOLERANCE_DEG):
            j = int(j)
            if j != i:
                neighbors[i].add(j)
                neighbors[j].add(i)

    # Coast: boundary not shared with any neighbour.
    coastal = [False] * n
    for i, g in enumerate(geoms):
        shared = unary_union([geoms[j] for j in neighbors[i]]) if neighbors[i] else None
        exposed = g.boundary if shared is None else g.boundary.difference(shared.buffer(1e-4))
        coastal[i] = exposed.length > COAST_EXPOSED_MIN_DEG

    # Sea crossings between coastal provinces that are not land neighbours, with their length.
    sea_links: list[dict[int, int]] = [{} for _ in range(n)]
    d = SEA_LINK_SEARCH_DEG
    for i, g in enumerate(geoms):
        if not coastal[i]:
            continue
        minx, miny, maxx, maxy = g.bounds
        cands = []
        for j in tree.query(box(minx - d, miny - d, maxx + d, maxy + d)):
            j = int(j)
            if j == i or not coastal[j] or j in neighbors[i]:
                continue
            a, b = nearest_points(g, geoms[j])
            dist_km = GEOD.inv(a.x, a.y, b.x, b.y)[2] / 1000
            if dist_km <= SEA_LINK_MAX_KM:
                cands.append((dist_km, j))
        for dist_km, j in sorted(cands)[:SEA_LINKS_PER_PROVINCE]:
            sea_links[i][j] = round(dist_km)
    for i in range(n):  # Symmetric.
        for j, dist_km in list(sea_links[i].items()):
            sea_links[j].setdefault(i, dist_km)

    # Terrain.
    regions = [(r.record["FEATURECLA"], valid(shape(r.shape.__geo_interface__)))
               for r in shapefile.Reader(str(cache / "ne_10m_geography_regions_polys")).iterShapeRecords()
               if r.record["FEATURECLA"] in REGION_TERRAIN]
    rtree = STRtree([g for _, g in regions])
    rep = [g.representative_point() for g in geoms]
    terrain = [terrain_for(geoms[i], rep[i].y, regions, rtree) for i in range(n)]
    areas = [km2(g) for g in geoms]

    # Cities, capitals.
    places = list(shapefile.Reader(str(cache / "ne_10m_populated_places")).iterShapeRecords())
    where = assign_points([(p.record["LONGITUDE"], p.record["LATITUDE"]) for p in places], geoms, tree)
    urban = [0] * n
    biggest: list[tuple[int, str]] = [(0, "")] * n
    capital_candidates: dict[str, list[tuple[int, int]]] = defaultdict(list)
    codes = {k: v for k, v in control["ne_country_codes"].items() if not k.startswith("_")}
    for p, i in zip(places, where):
        if i is None:
            continue
        rec = p.record
        urban[i] += max(0, int(rec["POP_MIN"] or 0))
        metro = max(0, int(rec["POP_MAX"] or 0))
        if metro > biggest[i][0]:
            biggest[i] = (metro, rec["NAME"])
        if rec["FEATURECLA"] == "Admin-0 capital":
            capital_candidates[codes.get(rec["ADM0_A3"], rec["ADM0_A3"])].append((metro, i))
    capitals = {iso: max(c)[1] for iso, c in capital_candidates.items()}
    for i in range(n):
        if areas[i] <= URBAN_TERRAIN_MAX_KM2 and urban[i] / max(areas[i], 1.0) >= URBAN_TERRAIN_DENSITY:
            terrain[i] = ("urban", f"city population density {urban[i] / areas[i]:,.0f}/km2")

    # Ports, airports, power, naval bases.
    ports_shp = list(shapefile.Reader(str(cache / "ne_10m_ports")).iterShapes())
    ports = [0] * n
    for i in assign_points([s.points[0] for s in ports_shp], geoms, tree):
        if i is not None:
            ports[i] += 1
    airports = [0] * n
    military_airports = [0] * n
    air = list(shapefile.Reader(str(cache / "ne_10m_airports")).iterShapeRecords())
    for a, i in zip(air, assign_points([s.shape.points[0] for s in air], geoms, tree)):
        if i is None or a.record["type"] in ("spaceport", "small"):
            continue
        airports[i] += 1
        military_airports[i] += "military" in a.record["type"]
    power_mw = [0.0] * n
    nuclear_mw = [0.0] * n
    with open(cache / "global_power_plant_database.csv", newline="") as f:
        plants = list(csv.DictReader(f))
    for row, i in zip(plants, assign_points([(float(r["longitude"]), float(r["latitude"])) for r in plants], geoms, tree)):
        if i is not None:
            power_mw[i] += float(row["capacity_mw"])
            if row["primary_fuel"] == "Nuclear":
                nuclear_mw[i] += float(row["capacity_mw"])
    naval: list[list[str]] = [[] for _ in range(n)]
    bases = control["naval_bases"]["bases"]
    coastal_geoms = [geoms[i] for i in range(n) if coastal[i]]
    coastal_ids = [i for i in range(n) if coastal[i]]
    ctree = STRtree(coastal_geoms)
    for (name, lon, lat), k in zip(bases, assign_points([(b[1], b[2]) for b in bases], coastal_geoms, ctree, snap=0.3)):
        if k is not None:
            naval[coastal_ids[k]].append(name)

    provinces = []
    for i, u in enumerate(units):
        tags = []
        if any(capitals.get(iso) == i for iso in capitals):
            tags.append("capital")
        if biggest[i][0] >= URBAN_CENTER_MIN_METRO:
            tags.append("urban_center")
        if ports[i]:
            tags.append("port")
        if airports[i]:
            tags.append("airfield")
        if naval[i]:
            tags.append("naval_base")
        if power_mw[i] >= ENERGY_MIN_MW or nuclear_mw[i] > 0:
            tags.append("energy")
        provinces.append({
            "id": i + 1,
            "name": u.name,
            "owner": u.iso,
            "ne_adm1": u.codes,
            "piece": u.piece,
            "region": u.region,
            "area_km2": round(areas[i]),
            "lat": round(rep[i].y, 4),
            "lon": round(rep[i].x, 4),
            "terrain": terrain[i][0],
            "terrain_basis": terrain[i][1],
            "coastal": coastal[i],
            "neighbors": sorted(j + 1 for j in neighbors[i]),
            "sea_links": [[j + 1, km] for j, km in sorted(sea_links[i].items())],
            "urban_population": urban[i],
            "largest_city": biggest[i][1] or None,
            "largest_metro_population": biggest[i][0],
            "ports": ports[i],
            "airports": airports[i],
            "military_airports": military_airports[i],
            "power_mw": round(power_mw[i]),
            "nuclear_mw": round(nuclear_mw[i]),
            "naval_bases": naval[i],
            "tags": tags,
        })
    control_out = {snap: {str(i + 1): {"controller": u.control[snap], "basis": u.control_basis.get(snap, "")}
                          for i, u in enumerate(units) if snap in u.control}
                   for snap in ("2021", "2026")}
    return {
        "generated_by": "tools/map/build_map.py",
        "inputs": {
            "natural_earth": f"github.com/nvkelso/natural-earth-vector @ {NE_COMMIT} (v5.1.2, public domain)",
            "power_plants": f"github.com/wri/global-power-plant-database @ {GPPD_COMMIT} (v1.3, CC BY 4.0)",
            "deepstate": "data/raw/map/deepstate_20260101.geojson (github.com/cyterat/deepstate-map-data @ de86af1, GPL-3.0)",
            "curated": "data/curated/map_control.json sha256 " + hashlib.sha256(CONTROL.read_bytes()).hexdigest(),
        },
        "capitals": {iso: i + 1 for iso, i in sorted(capitals.items())},
        "control": control_out,
        "not_modelled": control["not_modelled"],
        "provinces": provinces,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "wargame-map")
    args = ap.parse_args()
    fetch(args.cache)
    data = build(args.cache)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"{OUT.relative_to(ROOT)}: {len(data['provinces'])} provinces, {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
