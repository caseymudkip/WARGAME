"""The global province map and real-world scenario assembly."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, ProvinceTag, TerrainType, WarGoalType
from wargame.data.world_map import build_real_world, load_map
from wargame.simulation import ScenarioConfig, Simulation

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def raw_map():
    return load_map()


@pytest.fixture(scope="module", params=(2021, 2026))
def real(request):
    return build_real_world(request.param)


def by_name(raw_map, name):
    return next(p for p in raw_map["provinces"] if p["name"] == name)


# --- the map itself -------------------------------------------------------------------------------


def test_map_is_current_with_its_curated_inputs(raw_map):
    """Editing map_control.json requires rebuilding the map (tools/map/build_map.py)."""
    digest = hashlib.sha256((ROOT / "data" / "curated" / "map_control.json").read_bytes()).hexdigest()
    assert raw_map["inputs"]["curated"].endswith(digest)


def test_adjacency_is_symmetric_and_ids_resolve(raw_map):
    provinces = {p["id"]: p for p in raw_map["provinces"]}
    assert len(provinces) > 3_500
    for p in provinces.values():
        for n in p["neighbors"]:
            assert p["id"] in provinces[n]["neighbors"]
        for n, km in p["sea_links"]:
            assert 0 <= km <= 250 and any(back == p["id"] for back, _ in provinces[n]["sea_links"])


def test_almost_every_province_is_reachable(raw_map):
    isolated = [p for p in raw_map["provinces"] if not p["neighbors"] and not p["sea_links"]]
    assert len(isolated) < 60  # Remote islands only (Falklands, Kerguelen, Galapagos...).


def test_every_snapshot_country_has_a_capital(raw_map):
    for year in (2021, 2026):
        countries = json.loads((ROOT / "data" / "snapshots" / f"{year}.json").read_text())["countries"]
        assert set(countries) <= set(raw_map["capitals"])


def test_capitals_are_the_right_places(raw_map):
    provinces = {p["id"]: p for p in raw_map["provinces"]}
    cap = lambda iso: provinces[raw_map["capitals"][iso]]["name"]  # noqa: E731
    assert cap("UKR") == "Kyiv (Municipality)"
    assert cap("USA") == "Washington (Federal District)"
    assert cap("RUS") == "Moscow (Federal City)"
    assert cap("TWN") == "Taipei"


def test_crimea_is_ukrainian_de_jure_and_russian_held(raw_map):
    for name in ("Autonomous Republic of Crimea", "Sevastopol"):
        p = by_name(raw_map, name)
        assert p["owner"] == "UKR"
        for year in ("2021", "2026"):
            assert raw_map["control"][year][str(p["id"])]["controller"] == "RUS"


def test_donetsk_is_split_along_the_real_front(raw_map):
    old = by_name(raw_map, "Donetsk (occupied since 2014)")
    new = by_name(raw_map, "Donetsk (occupied since 2022)")
    free = by_name(raw_map, "Donetsk (Ukrainian-held)")
    assert str(old["id"]) in raw_map["control"]["2021"] and str(old["id"]) in raw_map["control"]["2026"]
    assert str(new["id"]) not in raw_map["control"]["2021"] and str(new["id"]) in raw_map["control"]["2026"]
    assert str(free["id"]) not in raw_map["control"]["2026"]
    assert free["largest_city"] == "Kramatorsk" and old["largest_city"] == "Donetsk"
    assert new["id"] in free["neighbors"] and old["id"] in new["neighbors"]


def test_kherson_city_stays_ukrainian_while_the_left_bank_is_occupied(raw_map):
    assert by_name(raw_map, "Kherson (Ukrainian-held)")["largest_city"] == "Kherson"
    occupied = by_name(raw_map, "Kherson (occupied since 2022)")
    assert raw_map["control"]["2026"][str(occupied["id"])]["controller"] == "RUS"


def test_key_sea_crossings_exist(raw_map):
    def link_km(a, b):
        pa, pb = by_name(raw_map, a), by_name(raw_map, b)
        return next((km for j, km in pa["sea_links"] if j == pb["id"]), None)
    assert link_km("Autonomous Republic of Crimea", "Krasnodar Krai") <= 10  # Kerch Strait
    assert link_km("Fujian", "Kinmen") <= 15
    assert link_km("Fujian", "Changhua") <= 160                              # Taiwan Strait


def test_dependencies_belong_to_their_sovereign(raw_map):
    owners = {p["name"]: p["owner"] for p in raw_map["provinces"]}
    assert owners["Guam"] == "USA"
    assert any(p["owner"] == "DNK" and p["ne_adm1"][0].startswith("GRL") for p in raw_map["provinces"])


def test_city_provinces_are_urban_terrain(raw_map):
    for name in ("Kyiv (Municipality)", "Moscow (Federal City)", "Washington (Federal District)"):
        assert by_name(raw_map, name)["terrain"] == "urban"


# --- assembled worlds -----------------------------------------------------------------------------


def test_population_matches_the_snapshot(real):
    for iso in ("USA", "RUS", "CHN", "UKR", "IND"):
        total = sum(p.population for p in real.world.owned_by(iso))
        assert total == pytest.approx(real.snapshot[iso].get("population"), rel=0.001)


def test_ukraine_starts_partly_occupied_and_more_so_in_2026():
    early, late = build_real_world(2021), build_real_world(2026)
    occ = lambda rw: rw.world.country("UKR").occupied_fraction(rw.world, frozenset({"RUS"}))  # noqa: E731
    assert 0.05 < occ(early) < 0.2 < occ(late) < 0.35


def test_real_provinces_carry_strategic_tags(real):
    kyiv = real.province_named("Kyiv (Municipality)")
    assert {ProvinceTag.CAPITAL, ProvinceTag.URBAN_CENTER} <= kyiv.tags
    assert ProvinceTag.NAVAL_BASE in real.province_named("Sevastopol").tags
    assert any(ProvinceTag.INDUSTRIAL in p.tags for p in real.world.owned_by("DEU"))
    assert real.province_named("Kyiv (Municipality)").terrain is TerrainType.URBAN


def test_real_proxy_war_runs_with_real_coalitions():
    rw = build_real_world(2026)
    sim = Simulation(rw.world, ScenarioConfig("RU-UA", datetime(2026, 1, 1), WarGoal(WarGoalType.REGIME_CHANGE, "RUS", "UKR"),
                                              EscalationTier.PROXY_WAR, nuclear_weapons_enabled=True,
                                              attacker_motivation=Motivation.AGGRESSIVE))
    sim.run_days(30)
    war = sim.wars[0]
    backers = {f.supporter for f in war.external_support if f.recipient == "UKR"}
    assert {"USA", "GBR", "DEU", "POL", "FRA"} <= backers
    assert {f.supporter for f in war.external_support if f.recipient == "RUS"} >= {"BLR", "PRK", "IRN"}
    assert not war.ended and not war.nuclear_strikes
    assert war.assessments["UKR"].components["territory"] > 0.2


def test_aggression_hardens_the_victims_friends_in_a_2021_start():
    rw = build_real_world(2021)
    before = rw.world.country("DEU").relations["UKR"]
    Simulation(rw.world, ScenarioConfig("RU-UA 2021", datetime(2021, 1, 1), WarGoal(WarGoalType.REGIME_CHANGE, "RUS", "UKR"),
                                        EscalationTier.PROXY_WAR))
    deu = rw.world.country("DEU")
    assert deu.relations["UKR"] > before and deu.relations["RUS"] < 0
