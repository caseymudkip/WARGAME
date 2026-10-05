"""Assemble a real-world World: the province map plus a country snapshot.

    world = build_real_world(2026)
    russia, ukraine = world.country("RUS"), world.country("UKR")

Map facts (geography, cities, ports, airfields, power plants, de facto control at
each start date) come from data/map/world_map.json. Quantities that depend on
the snapshot are modelled here, in one place:

  population  each country's population (snapshot) split into its provinces:
              city population where the cities are, the rural remainder by
              area weighted by how habitable the terrain is.
  industry    the country's economy (PPP, in units of $100bn) split by each
              province's share of urban population (60%) and power capacity (40%).
  infra       national road density blended with the province's urbanisation.
  tags        INDUSTRIAL for provinces holding >= 8% of national industry;
              FARMLAND for open, mostly rural provinces.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wargame.core.enums import Branch, ProvinceTag, TerrainType
from wargame.core.mathutil import clamp
from wargame.data.profile import apply_diplomacy, build_country
from wargame.data.snapshot import DEFAULT_ROOT, Snapshot, load_snapshot
from wargame.nation.country import Country
from wargame.world.province import Province
from wargame.world.world import FortifiedLine, World

HABITABILITY = {
    TerrainType.PLAINS: 1.0, TerrainType.URBAN: 1.0, TerrainType.HILLS: 0.8, TerrainType.FOREST: 0.5,
    TerrainType.JUNGLE: 0.4, TerrainType.MARSH: 0.3, TerrainType.MOUNTAINS: 0.3, TerrainType.DESERT: 0.05,
    TerrainType.ARCTIC: 0.02,
}
MAX_URBAN_SHARE = 0.85          # Urban sums double-count suburbs; never let cities exceed this share.
INDUSTRY_UNIT_USD = 1e11        # One unit of industrial output = $100bn of PPP GDP.
INDUSTRIAL_TAG_SHARE = 0.08
FARMLAND_MAX_URBAN_SHARE = 0.3
FARMLAND_TERRAIN = {TerrainType.PLAINS, TerrainType.HILLS}


@dataclass
class RealWorld:
    world: World
    snapshot: Snapshot
    map_meta: dict[str, Any]

    def province_named(self, name: str, owner: str | None = None) -> Province:
        matches = [p for p in self.world.provinces.values() if p.name == name and (owner is None or p.owner == owner)]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} provinces named {name!r}" + (f" owned by {owner}" if owner else ""))
        return matches[0]


def load_map(root: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(((root or DEFAULT_ROOT) / "map" / "world_map.json").read_text())
    return data


def _populations(raw: list[dict[str, Any]], snapshot: Snapshot) -> dict[int, int]:
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for p in raw:
        by_owner.setdefault(p["owner"], []).append(p)
    out: dict[int, int] = {}
    for iso, provinces in by_owner.items():
        total = snapshot.countries[iso].get("population") if iso in snapshot.countries else 0.0
        urban = sum(p["urban_population"] for p in provinces)
        if total <= 0:
            out.update({p["id"]: p["urban_population"] for p in provinces})
            continue
        scale = min(1.0, MAX_URBAN_SHARE * total / urban) if urban else 0.0
        rural = total - urban * scale
        weights = {p["id"]: p["area_km2"] * HABITABILITY[TerrainType(p["terrain"])] for p in provinces}
        wsum = sum(weights.values()) or 1.0
        for p in provinces:
            out[p["id"]] = int(p["urban_population"] * scale + rural * weights[p["id"]] / wsum)
    return out


def _industry(raw: list[dict[str, Any]], snapshot: Snapshot) -> dict[int, float]:
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for p in raw:
        by_owner.setdefault(p["owner"], []).append(p)
    out: dict[int, float] = {}
    for iso, provinces in by_owner.items():
        economy = snapshot.countries[iso].get("ppp_usd") / INDUSTRY_UNIT_USD if iso in snapshot.countries else 0.0
        urban = sum(p["urban_population"] for p in provinces) or 1.0
        power = sum(p["power_mw"] for p in provinces)
        for p in provinces:
            share = p["urban_population"] / urban
            if power > 0:
                share = 0.6 * share + 0.4 * p["power_mw"] / power
            out[p["id"]] = economy * share
    return out


def _road_density_score(iso: str, snapshot: Snapshot) -> float:
    if iso not in snapshot.countries:
        return 0.3
    rec = snapshot.countries[iso]
    area = rec.get("land_area_km2")
    density = rec.get("roadway_km") / area if area > 0 else 0.0
    return clamp(math.log10(1.0 + 1_000.0 * density) / 3.0)


def build_real_world(year: int, root: Path | None = None) -> RealWorld:
    snapshot = load_snapshot(year, root)
    data = load_map(root)
    raw = data["provinces"]
    control = {int(pid): c["controller"] for pid, c in data["control"][str(year)].items()}
    population = _populations(raw, snapshot)
    industry = _industry(raw, snapshot)
    national_industry: dict[str, float] = {}
    for p in raw:
        national_industry[p["owner"]] = national_industry.get(p["owner"], 0.0) + industry[p["id"]]

    world = World()
    for p in raw:
        terrain = TerrainType(p["terrain"])
        pop = population[p["id"]]
        urban_share = min(1.0, p["urban_population"] / pop) if pop else 0.0
        tags = {ProvinceTag(t) for t in p["tags"]}
        if national_industry[p["owner"]] > 0 and industry[p["id"]] >= INDUSTRIAL_TAG_SHARE * national_industry[p["owner"]]:
            tags.add(ProvinceTag.INDUSTRIAL)
        if terrain in FARMLAND_TERRAIN and urban_share < FARMLAND_MAX_URBAN_SHARE:
            tags.add(ProvinceTag.FARMLAND)
        world.add_province(Province(
            id=p["id"],
            name=p["name"],
            owner=p["owner"],
            controller=control.get(p["id"], p["owner"]),
            terrain=terrain,
            infrastructure=clamp(0.6 * _road_density_score(p["owner"], snapshot) + 0.4 * math.sqrt(urban_share)),
            population=pop,
            industrial_output=round(industry[p["id"]], 4),
            tags=frozenset(tags),
            neighbors=tuple(p["neighbors"]),
            sea_links=tuple((j, km) for j, km in p["sea_links"]),
            river_borders=tuple((j, rank) for j, _, rank in p["river_borders"]),
            border_km=tuple((j, km) for j, km in p["border_km"]),
            coastal=p["coastal"],
            area_km2=float(p["area_km2"]),
        ))

    airfields: dict[str, int] = {}
    for p in raw:
        if "airfield" in p["tags"]:
            airfields[p["owner"]] = airfields.get(p["owner"], 0) + 1
    countries: dict[str, Country] = {}
    for iso, record in snapshot.countries.items():
        capital = data["capitals"].get(iso)
        if capital is None:
            continue
        countries[iso] = build_country(record, capital_province_id=capital, airfields=max(1, airfields.get(iso, 0)))
    apply_diplomacy(snapshot, countries)
    for country in countries.values():
        world.add_country(country)
    _apply_curated_posture(world, year, root)
    return RealWorld(world, snapshot, {k: v for k, v in data.items() if k != "provinces"})


def _curated(name: str, root: Path | None) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(((root or DEFAULT_ROOT) / "curated" / name).read_text())
    return raw


def _apply_curated_posture(world: World, year: int, root: Path | None) -> None:
    """Fortified lines, drone saturation, mobilisation and leadership at the start date."""
    by_name: dict[str, int] = {}
    for p in world.provinces.values():
        by_name.setdefault(p.name, p.id)
    for line in _curated("fortifications.json", root).get(str(year), []):
        a, b = line["between"]
        if a not in world.countries or b not in world.countries:
            continue
        provinces = frozenset(by_name[n] for n in line["provinces"]) if "provinces" in line else None
        world.fortified_lines.append(FortifiedLine((a, b), frozenset(line["fortify"]), float(line["level"]), provinces))
    for tag, posture in _curated("force_posture.json", root).get(str(year), {}).items():
        if tag in world.countries:
            world.countries[tag].drone_saturation = float(posture["drone_saturation"])
            world.countries[tag].mobilised = bool(posture["mobilised"])
            world.countries[tag].hosts = frozenset(posture.get("hosts", []))
            world.countries[tag].hosting_days = posture.get("hosting_days")
            share = posture.get("active_share")
            if share is not None:
                for stock in world.countries[tag].oob.equipment.values():
                    if stock.branch is Branch.LAND:
                        total = stock.quantity + stock.stored
                        stock.quantity = round(total * float(share))
                        stock.stored = total - stock.quantity
    for tag, leader in _curated("leadership.json", root).get(str(year), {}).items():
        if tag in world.countries:
            world.countries[tag].leadership_defiance = float(leader["defiance"])
