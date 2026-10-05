"""Land warfare: fronts, assaults, pace, encirclement, rivers, the sea, attrition and mobilisation.

Mechanics run on the fictional map in conftest.py (provinces of 3,000 km2, 55 km a side);
the calibration tests run the real war in Ukraine at both of its paces, 2022 and 2025.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from conftest import BOR_CAPITAL, build_world, occupy

from wargame.conflict import land_warfare as lw
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import Branch, EscalationTier, Motivation, TerrainType, WarGoalType
from wargame.nation.military import EquipmentStock
from wargame.simulation import ScenarioConfig, Simulation
from wargame.world.world import FortifiedLine, World

ROOT = Path(__file__).resolve().parents[1]
AREA_KM2 = 3_000.0
GRINDING = 400_000   # ARD troops that attack BOR's front at R ~ 2: a slow, partial advance.


def world_with(ard_troops: int = 1_500_000, bor_troops: int = 100_000) -> World:
    world = build_world()
    for p in world.provinces.values():
        p.area_km2 = AREA_KM2
    world.country("ARD").oob.active_personnel = ard_troops
    world.country("ARD").mobilised = True  # Committed in full from the first day.
    world.country("BOR").oob.active_personnel = bor_troops
    return world


def start(world: World, goal: WarGoal | None = None, motivation: Motivation = Motivation.AGGRESSIVE,
          seed: int = 7) -> Simulation:
    goal = goal or WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({1, 2, 3}))
    return Simulation(world, ScenarioConfig("land", datetime(2026, 1, 1), goal, EscalationTier.VACUUM,
                                            attacker_motivation=motivation, seed=seed))


def progress(sim: Simulation, pid: int) -> float:
    if sim.world.provinces[pid].controller == "ARD":
        return 1.0
    return sim.world.contested.get(pid, ("", 0.0))[1]


def navy(tons: int) -> EquipmentStock:
    return EquipmentStock("frigates", Branch.NAVAL, quantity=tons, quality=0.8, readiness=1.0)


# --- calibration -------------------------------------------------------------------------------------


def _calibration():
    spec = importlib.util.spec_from_file_location("ukraine_2025", ROOT / "tools" / "calibration" / "ukraine_2025.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calibrated_to_the_2022_invasion():
    """From the 2021 map, Russia gains half again its hold on Ukraine in five weeks but cannot take Kyiv.

    It reaches about 40% of ISW's figure: in 2022 columns raced down roads through parts of seven oblasts
    at once (much of that ground was thin road control, given up in April), while oblast-sized provinces
    fall one after another (left-bank Kherson before Melitopol). Russia fights with the ~360,000 ground
    troops IISS counted in 2021, not its whole 900,000-strong military."""
    cal = _calibration()
    result = cal.run_2022(days=36)
    low, _ = cal.BENCHMARK_OCCUPIED_31_MARCH_2022
    assert result["occupied_km2"] >= 0.4 * low  # ISW: ~163,000 km2 on 31 March 2022.
    assert result["occupied_km2"] > 1.5 * result["occupied_before"]
    assert result["kyiv_held"] and not result["war_ended"]


def _war_from(year: int, aid: bool, days: int):
    cal = _calibration()
    sim = cal.start(year)
    if not aid:  # The West walks away: nobody will arm Ukraine.
        for tag, country in sim.world.countries.items():
            if tag != "UKR":
                country.relations["UKR"] = min(country.relations.get("UKR", 0.0), 0.0)
    for _ in range(days // 10):
        sim.run_days(10)
        if sim.finished:
            break
    return sim


def test_without_aid_ukraine_breaks_within_a_year():
    sim = _war_from(2026, aid=False, days=450)
    assert sim.finished and any(e.kind == "capitulation" and "Ukraine" in e.message for e in sim.events())
    assert sim.clock.hours_elapsed / 24 < 400


def test_with_aid_ukraine_keeps_fighting_as_it_did_in_2023_2025():
    sim = _war_from(2026, aid=True, days=3 * 365)  # Three more years like 2023-2025.
    assert not sim.finished
    assert sim.world.country("UKR").aid_coverage > 0.3  # Aid covers what its own industry can't make.


def test_the_2022_war_lasts_years_with_aid_and_one_without():
    with_aid = _war_from(2021, aid=True, days=3 * 365)
    assert not with_aid.finished  # Still fighting in 2025, as it was.
    without = _war_from(2021, aid=False, days=550)
    assert without.finished


def test_calibrated_to_the_2025_war():
    """Russia attacking from the real 1 January 2026 front reproduces 2025's pace and price."""
    cal = _calibration()
    result = cal.run(days=365)
    assert result["km2_per_day"] == pytest.approx(cal.BENCHMARK_KM2_PER_DAY, rel=0.25)        # DeepState
    assert result["ru_casualties_per_day"] == pytest.approx(cal.BENCHMARK_RU_CASUALTIES_PER_DAY, rel=0.2)  # UK MoD
    assert 0.35 <= result["ua_casualties_per_day"] / result["ru_casualties_per_day"] <= 0.55  # CSIS: 500-600k vs 1.2M
    assert not result["war_ended"]  # A year of this is not enough to break either side.


# --- assaults ------------------------------------------------------------------------------------------


def test_a_much_stronger_army_breaks_through_and_takes_the_objective():
    sim = start(world_with())
    sim.run_days(120)
    war = sim.wars[0]
    assert war.ended and war.treaty.winner == "ARD"
    assert all(sim.world.provinces[pid].owner == "ARD" for pid in (1, 2, 3))
    assert any(e.kind == "peace" for e in sim.events())


def test_a_weak_attacker_does_not_throw_its_troops_away():
    sim = start(world_with(ard_troops=60_000))
    sim.run_days(10)
    ledger = sim.wars[0].participants["ARD"].ledger
    assert sim.land.deployments["ARD"].attacks == {}       # Nothing reaches MIN_ASSAULT_RATIO...
    assert sum(c for c, _ in ledger.window) == 0           # ...so no blood is spent attacking...
    assert sim.world.provinces[1].controller == "BOR"        # ...and nothing moves.


def test_progress_is_partial_before_a_province_falls():
    sim = start(world_with(ard_troops=GRINDING))
    sim.run_days(5)
    assert 0.0 < progress(sim, 1) < 1.0
    assert sim.wars[0].participants["ARD"].ledger.value_held_yesterday > 0  # Grinding gains count.


def _progress_after(days: int, prepare) -> float:
    world = world_with(ard_troops=GRINDING)
    prepare(world)
    sim = start(world)
    sim.run_days(days)
    return progress(sim, 1)


def test_mountains_slow_the_advance():
    flat = _progress_after(5, lambda w: None)
    steep = _progress_after(5, lambda w: setattr(w.provinces[1], "terrain", TerrainType.MOUNTAINS))
    assert 0.0 <= steep < flat / 2


def test_a_major_river_blunts_the_assault():
    def river(world: World) -> None:
        world.provinces[11].river_borders = ((1, 4),)
        world.provinces[1].river_borders = ((11, 4),)
    assert lw.crossing_penalty(build_world(), 11, 1, 0.0) == 1.0
    assert _progress_after(5, river) < _progress_after(5, lambda w: None) / 2


def test_fronts_start_open_and_dig_in_while_static():
    sim = start(world_with(ard_troops=60_000))  # A front that doesn't move.
    assert sim.land.fortification[1] == pytest.approx(lw.FORTIFICATION_GROWTH)  # No works before the war.
    sim.run_days(5)
    assert sim.land.fortification[1] == pytest.approx(6 * lw.FORTIFICATION_GROWTH)
    sim.run_days(200)
    assert sim.land.fortification[1] == lw.FORTIFICATION_MAX
    assert 2 not in sim.land.fortification  # Only the front digs in.


def test_prewar_lines_are_dug_in_from_the_first_day():
    world = world_with(ard_troops=60_000)
    world.fortified_lines.append(FortifiedLine(("ARD", "BOR"), frozenset({"BOR"}), 0.5))
    world.fortified_lines.append(FortifiedLine(("BOR", "DRV"), frozenset({"BOR"}), 0.6))  # DRV isn't at war.
    sim = start(world)
    assert sim.land.fortification[1] == pytest.approx(0.5 + lw.FORTIFICATION_GROWTH)
    assert sim.land.fortification[11] == pytest.approx(lw.FORTIFICATION_GROWTH)  # ARD didn't dig.
    assert 6 not in sim.land.fortification


def test_fieldworks_slow_attackers():
    world = world_with(ard_troops=GRINDING)
    dug_in = start(world)
    dug_in.land.fortification[1] = lw.FORTIFICATION_MAX
    open_field = start(world_with(ard_troops=GRINDING))
    open_field.land.fortification[1] = 0.0
    dug_in.run_days(3)
    open_field.run_days(3)
    assert progress(dug_in, 1) < progress(open_field, 1)


def test_captured_ground_is_damaged_and_unfortified():
    sim = start(world_with())
    for _ in range(60):
        sim.run_days(1)
        if sim.world.provinces[1].controller == "ARD":
            break
    p = sim.world.provinces[1]
    assert p.controller == "ARD" and p.damage > 0
    assert 1 not in sim.world.contested


# --- pace: the same rules give blitzkrieg and trench war -----------------------------------------------


def _depth_km_per_day(prepare, days: int = 2, ard_troops: int = GRINDING) -> float:
    world = world_with(ard_troops=ard_troops)
    world.country("BOR").mobilised = True  # A defence already deployed: no surprise (Dupuy's averages).
    prepare(world)
    sim = start(world)
    sim.run_days(days)
    frontage = world.provinces[1].border_with(11)
    return progress(sim, 1) * AREA_KM2 / days / frontage


def test_an_unopposed_army_advances_at_exploitation_speed():
    def empty(world: World) -> None:  # BOR's army is elsewhere; only local defence remains.
        world.country("BOR").oob.active_personnel = 1_000
    depth = _depth_km_per_day(empty, days=1)
    assert depth == pytest.approx(lw.MAX_DEPTH_KM_PER_DAY, rel=0.05)  # 3rd ID to Baghdad, 2003: ~25 km/day.


def test_a_ww2_style_attack_moves_at_ww2_division_pace():
    depth = _depth_km_per_day(lambda w: None, ard_troops=600_000)  # Force ratio ~2.3 on open ground.
    assert 1.0 < depth < 4.5  # Dupuy: 1.8 km/day (West 1943-45, average ratio 2.3), 4.5 (East 1943).


def test_a_drone_watched_fortified_front_is_slower_than_the_somme():
    """Same local odds (2.5:1, a massed assault), open ground against a dug-in, drone-watched line."""
    world = world_with()
    sim = start(world)
    land = sim.land
    open_ground = land.depth_km_per_day(world, 1, 2.5)
    world.country("BOR").drone_saturation = 1.0
    land.fortification[1] = lw.FORTIFICATION_MAX
    trench = land.depth_km_per_day(world, 1, 2.5)
    assert 0.015 < trench < 0.08  # CSIS: Russia 2024-25 at 15-70 m/day; the Somme 1916 at 80 m/day.
    assert trench < open_ground / 50


# --- mobilisation and basing --------------------------------------------------------------------------


def test_a_nation_fighting_for_survival_mobilises():
    goal = WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR")
    sim = start(world_with(ard_troops=60_000), goal)
    bor = sim.world.country("BOR").oob
    prewar = 100_000
    sim.run_days(30)
    # 2% of pre-war strength a day (Ukraine 2022: ~250,000 to ~700,000 by May); reserves replace the dead.
    assert bor.casualties_total > 0
    assert bor.active_personnel == pytest.approx(prewar + 31 * lw.MOBILISATION_RATE_EXISTENTIAL * prewar, rel=0.02)
    sim.run_days(200)
    assert bor.active_personnel <= lw.MOBILISATION_CEILING_EXISTENTIAL * prewar


def test_only_ground_troops_count_as_infantry_and_the_called_up_all_do():
    world = world_with()
    bor = world.country("BOR")
    bor.ground_share, bor.peacetime_active = 0.4, 100_000  # Russia, 2021: navies and missile forces don't hold a front.
    assert lw.ground_troops(bor) == pytest.approx(40_000)
    bor.oob.active_personnel = 150_000                     # 50,000 called up: all of them go to the front.
    assert lw.ground_troops(bor) == pytest.approx(90_000)


def _fielded_after(days: int, **reserve) -> int:
    world = world_with(ard_troops=GRINDING, bor_troops=24_000)
    world.country("BOR").mobilised = False
    for key, value in reserve.items():
        setattr(world.country("BOR"), key, value)
    sim = start(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    sim.run_days(days)
    return world.country("BOR").oob.active_personnel + world.country("BOR").oob.casualties_total


def test_a_reserve_army_fields_its_wartime_structure_in_days():
    paper = _fielded_after(10, organised_reserve=256_000)                     # Registered, but no wartime structure.
    finland = _fielded_after(10, organised_reserve=256_000, mobilisation_days=7.0)  # 280,000 wartime strength.
    assert finland > 200_000
    assert paper < 40_000


def test_an_already_mobilised_nation_only_replaces_losses():
    world = world_with(ard_troops=60_000)
    world.country("BOR").mobilised = True
    sim = start(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    sim.run_days(30)
    assert sim.world.country("BOR").oob.active_personnel <= 100_000


def test_a_host_lets_an_attacker_strike_from_its_soil():
    world = world_with()
    world.country("DRV").hosts = frozenset({"ARD"})  # Belarus, February 2022.
    goal = WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({6}))
    sim = start(world, goal)
    attacks = sim.land.deployments["ARD"].attacks
    assert attacks[6][0] == 20  # Straight from DRV's province into the objective.
    sim.run_days(3)
    assert progress(sim, 6) > 0


# --- encirclement --------------------------------------------------------------------------------------


def test_provinces_cut_off_from_the_capital_are_encircled():
    world = world_with(ard_troops=60_000)
    occupy(world, [5], "ARD")  # 1-2-3-4 now have no road to the capital at 7.
    sim = start(world)
    assert {1, 2, 3, 4} <= sim.land.encircled
    assert BOR_CAPITAL not in sim.land.encircled and 6 not in sim.land.encircled
    # The pocket can't stage troops: the BOR army holds 4-5 at the capital's side, not 1-4.
    stations = sim.land.deployments["BOR"].stationed
    assert stations.get(6, 0.0) > 0 and all(stations.get(pid, 0.0) == 0.0 for pid in (2, 3))


def test_a_surrounded_capital_is_the_pocket_not_the_rest_of_the_country():
    world = world_with(ard_troops=60_000)
    occupy(world, [6, 8], "ARD")  # The capital at 7 is cut off; 1-5 and 9-10 are not.
    sim = start(world)
    assert BOR_CAPITAL in sim.land.encircled
    assert not {1, 2, 3, 4, 5} & sim.land.encircled  # The main body (most value) keeps its supply.


def test_a_friendly_neighbour_keeps_a_cut_off_region_supplied():
    world = world_with(ard_troops=60_000)
    occupy(world, [5], "ARD")
    world.country("DRV").relations["BOR"] = 0.6  # DRV borders province 6, on the capital's side...
    world.country("CAL").relations["BOR"] = 0.0
    world.provinces[20].neighbors = (6, 2)        # ...and now also 2, in the cut-off west.
    world.provinces[2].neighbors = (1, 3, 20)
    sim = start(world)
    assert not {1, 2, 3, 4} & sim.land.encircled  # Aid comes over the border, as through Poland.


def test_a_pocket_fights_at_half_strength():
    world = world_with(ard_troops=GRINDING)
    sim = start(world)
    whole, _ = sim.land._defence(world, 1, {"BOR"})
    sim.land.encircled.add(1)
    pocket, _ = sim.land._defence(world, 1, {"BOR"})
    assert pocket == pytest.approx(whole * lw.ENCIRCLED_DEFENCE)


# --- the sea ----------------------------------------------------------------------------------------------


def _coast(world: World) -> None:
    """Give ARD's province 15 a 120 km sea crossing to BOR's industrial province 9."""
    for pid in (9, 15):
        world.provinces[pid].coastal = True
    world.provinces[15].sea_links = ((9, 120),)
    world.provinces[9].sea_links = ((15, 120),)


def test_amphibious_assaults_need_naval_superiority():
    goal = WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({9}))
    without = world_with(ard_troops=GRINDING)
    _coast(without)
    assert all(km == 0 for _, _, km in start(without, goal).land.deployments["ARD"].attacks.values())

    with_fleet = world_with()
    _coast(with_fleet)
    with_fleet.country("ARD").oob.equipment["frigates"] = navy(40)
    sim = start(with_fleet, goal)
    attacks = sim.land.deployments["ARD"].attacks
    assert attacks.get(9, (0, 0.0, 0.0))[2] == 120  # Straight across the water to the objective.


def test_a_beachhead_builds_up_one_lift_at_a_time():
    goal = WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({9}))
    world = world_with()
    _coast(world)
    world.country("ARD").oob.equipment["frigates"] = navy(40)
    sim = start(world, goal)
    planned = sim.land.deployments["ARD"].attacks[9][1]
    first = sim.land.ashore[("ARD", 9)]
    assert 0 < first < planned  # One division at a time: PLA Navy lift ~20,000 troops (DoD).
    sim.run_days(1)
    assert sim.land.ashore.get(("ARD", 9), planned) > first or world.provinces[9].controller == "ARD"


def test_a_blue_water_navy_can_land_far_from_home():
    goal = WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({9}))
    world = world_with()
    for pid in (9, 15):
        world.provinces[pid].coastal = True
    world.provinces[9].lat, world.provinces[15].lat = 10.0, 18.0  # ~890 km apart: no sea link.
    world.country("ARD").oob.equipment["frigates"] = navy(40)
    assert not [km for _, _, km in start(world, goal).land.deployments["ARD"].attacks.values() if km]
    world.country("ARD").oob.equipment["helicopter_carriers"] = EquipmentStock("helicopter_carriers", Branch.NAVAL, 2, 0.8)
    assert lw.LandWarfare.blue_water(world.country("ARD"))  # Two big decks: the US in the Caribbean.
    assert start(world, goal).land.deployments["ARD"].attacks.get(9, (0, 0.0, 0.0))[2] == lw.AMPHIBIOUS_MAX_KM


def _two_beaches(world: World) -> None:
    _coast(world)
    world.provinces[8].coastal = True
    world.provinces[15].sea_links = ((9, 120), (8, 150))
    world.provinces[8].sea_links = ((15, 150),)
    world.country("ARD").oob.equipment["frigates"] = navy(40)


def test_lift_fills_the_main_beachhead_before_the_next():
    world = world_with()
    _two_beaches(world)
    sim = start(world, WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({8, 9})))
    land, dep = sim.land, sim.land.deployments["ARD"]
    lift = lw.LIFT_TROOPS_PER_NAVAL_POWER * world.country("ARD").oob.branch_power(Branch.NAVAL) / dep.personnel_per_power
    dep.attacks = {9: (15, 2 * lift, 120.0), 8: (15, 0.5 * lift, 150.0)}
    land.ashore.clear()
    land._land_waves(world, {"ARD": {"ARD"}})
    assert land.ashore[("ARD", 9)] == pytest.approx(lift)  # Normandy first...
    assert land.ashore[("ARD", 8)] == 0.0                  # ...Provence when there are ships to spare.


def test_waves_still_at_sea_sail_for_the_new_beach():
    world = world_with()
    _two_beaches(world)
    sim = start(world, WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({8, 9})))
    land, dep = sim.land, sim.land.deployments["ARD"]
    dep.attacks = {9: (15, 10_000.0, 120.0)}
    land.ashore = {("ARD", 8): 300.0}  # The plan no longer names beach 8.
    lift = lw.LIFT_TROOPS_PER_NAVAL_POWER * world.country("ARD").oob.branch_power(Branch.NAVAL) / dep.personnel_per_power
    land._land_waves(world, {"ARD": {"ARD"}})
    assert land.ashore == {("ARD", 9): pytest.approx(300.0 + lift)}


def test_a_landing_goes_in_only_once_it_can_win_a_lodgement():
    world = world_with()
    _coast(world)
    world.country("ARD").oob.equipment["frigates"] = navy(40)
    sim = start(world, WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({9})))
    land, allies = sim.land, {"BOR": {"BOR"}, "ARD": {"ARD"}}
    defence = land._defence(world, 9, {"BOR"})[0]
    per_power = land.deployments["ARD"].effectiveness * lw.amphibious_penalty(120) * land._reach_factor("ARD", 15)
    enough = lw.MIN_ASSAULT_RATIO * defence / per_power
    assert not land._lodgement(world, "ARD", 9, 15, 0.5 * enough, 120.0, allies)  # Dieppe, 1942.
    assert land._lodgement(world, "ARD", 9, 15, 1.01 * enough, 120.0, allies)
    world.contested[9] = ("ARD", 0.05)  # Once ashore, the beachhead fights on whatever the odds.
    assert land._lodgement(world, "ARD", 9, 15, 0.1 * enough, 120.0, allies)


def test_the_beach_is_the_hard_part():
    world = world_with()
    _coast(world)
    assert lw.LandWarfare._crossing(world, "ARD", 15, 9, 120.0) == pytest.approx(lw.amphibious_penalty(120))
    world.contested[9] = ("ARD", 0.05)
    assert lw.LandWarfare._crossing(world, "ARD", 15, 9, 120.0) == lw.BEACHHEAD_SUPPLY


def test_a_blue_water_navy_supplies_a_lodgement_across_an_ocean():
    def reach_to_9(carriers: int) -> int:
        world = world_with()
        for pid in (9, 15):
            world.provinces[pid].coastal = True
        world.provinces[9].lat, world.provinces[15].lat = 10.0, 18.0  # ~890 km: no sea link.
        world.country("ARD").oob.equipment["frigates"] = navy(40)
        if carriers:
            world.country("ARD").oob.equipment["helicopter_carriers"] = EquipmentStock(
                "helicopter_carriers", Branch.NAVAL, carriers, 0.8)
        sim = start(world, WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({9})))
        world.set_controller(9, "ARD")
        sim.land._taken_hour[9] = sim.clock.hours_elapsed  # Just landed: not yet consolidated rear.
        sim.land._cache = {}
        sim.land._operational_reach(world, {"ARD": {"BOR"}, "BOR": {"ARD"}}, {"ARD": {"ARD"}, "BOR": {"BOR"}},
                                    sim.clock.hours_elapsed)
        return sim.land.hops_from_rear("ARD", 9)
    assert reach_to_9(carriers=0) == lw.REACH_CUT_OFF_HOPS
    assert reach_to_9(carriers=2) == 1  # Normandy's Mulberry harbours; the US in the Caribbean.


def test_a_dominant_fleet_blockades_the_weaker_side():
    world = world_with(ard_troops=60_000)
    world.country("ARD").oob.equipment["frigates"] = navy(40)
    world.country("BOR").oob.equipment["frigates"] = navy(5)
    sim = start(world)
    level = sim.world.country("BOR").blockade_interdiction
    assert level == pytest.approx(min(lw.BLOCKADE_MAX, lw.BLOCKADE_BASE + lw.BLOCKADE_PER_RATIO * (8 - lw.BLOCKADE_MIN_RATIO)))
    assert sim.world.country("ARD").blockade_interdiction == 0.0


# --- attrition -------------------------------------------------------------------------------------------


def _after_ten_days_of_losses(wreck_industry: bool):
    """ARD loses 2,000 soldiers a day (1% of its active army) for ten days."""
    world = build_world()
    ard = world.country("ARD")
    ard.mark_prewar_baseline(world)
    ard.oob.equipment["tanks"] = EquipmentStock("tanks", Branch.LAND, quantity=2_000, quality=0.7)
    if wreck_industry:
        for p in world.owned_by("ARD"):
            p.damage = 1.0
    land = lw.LandWarfare()
    for _ in range(10):
        ard.oob.record_casualties(2_000)
        land.casualties_today["ARD"] = 2_000
        land._apply_attrition(world)
    return ard.oob


def test_losses_wear_down_equipment_unless_industry_keeps_up():
    wrecked = _after_ten_days_of_losses(wreck_industry=True)
    intact = _after_ten_days_of_losses(wreck_industry=False)
    # ~1% of the tanks go with 1% of the men each day (x0.6); intact factories rebuild some.
    assert 1_850 < wrecked.equipment["tanks"].quantity < intact.equipment["tanks"].quantity < 2_000


def test_reserves_refill_the_ranks_at_a_limited_pace():
    oob = _after_ten_days_of_losses(wreck_industry=False)
    per_day = int(lw.MAX_REPLACEMENT_SHARE_PER_DAY * 200_000)  # ~600 of the 2,000 lost each day.
    assert oob.reserve_personnel == pytest.approx(200_000 - 10 * per_day, abs=10 * per_day // 10)
    assert 200_000 - 20_000 < oob.active_personnel < 200_000 - 10 * per_day


def test_land_combat_is_deterministic():
    def run() -> tuple:
        sim = start(world_with(ard_troops=GRINDING), seed=3)
        sim.run_days(40)
        return (sim.world.country("ARD").oob.casualties_total, sim.world.country("BOR").oob.casualties_total,
                sorted(sim.world.contested.items()), [p.controller for p in sim.world.provinces.values()])
    assert run() == run()


# --- surprise, reach, hosting --------------------------------------------------------------------------


def test_surprise_fades_over_three_days():
    world = world_with()
    sim = start(world)  # BOR is not on a war footing.
    war, now = sim.wars[0], sim.clock.hours_elapsed
    assert sim.land._surprise(world, [war], "ARD", "BOR", now) == pytest.approx(lw.SURPRISE_UNMOBILISED)
    assert sim.land._surprise(world, [war], "ARD", "BOR", now + 3 * 24) == 1.0
    world.country("BOR").mobilised = True
    assert sim.land._surprise(world, [war], "ARD", "BOR", now) == 1.0


def test_surprise_belongs_to_whoever_struck_first():
    world = world_with()
    world.country("ARD").mobilised = False
    sim = start(world)
    war, now = sim.wars[0], sim.clock.hours_elapsed
    assert sim.land._surprise(world, [war], "BOR", "ARD", now) == 1.0  # A counterattack catches no one napping.


def test_a_thin_line_is_bypassed_on_a_broad_front(monkeypatch):
    def gained(thin_lines: bool) -> float:
        if not thin_lines:
            monkeypatch.setattr(lw, "DENSITY_TO_HOLD", 0.0)
        world = world_with(ard_troops=300_000, bor_troops=20_000)
        world.country("BOR").mobilised = True
        world.provinces[1].area_km2 = 40_000.0  # A long front: 400 km to hold.
        world.provinces[1].border_km = ((11, 400),)
        world.provinces[11].border_km = ((1, 400),)
        sim = start(world)
        sim.run_days(1)
        monkeypatch.undo()
        return progress(sim, 1)
    assert gained(thin_lines=True) > 1.4 * gained(thin_lines=False)  # Ukraine's south, February 2022.


def test_attackers_lose_little_at_overwhelming_odds(monkeypatch):
    def losses(lanchester: bool) -> int:
        if not lanchester:
            monkeypatch.setattr(lw, "LOPSIDED_RATIO", 1e9)
        world = world_with(ard_troops=1_500_000, bor_troops=10_000)
        world.country("BOR").mobilised = True
        sim = start(world)
        sim.run_days(1)
        monkeypatch.undo()
        return world.country("ARD").oob.casualties_total
    assert losses(lanchester=True) < 0.6 * losses(lanchester=False)  # 1991: ~0.03% of the force a day.


def test_armies_at_war_learn_the_drone_war():
    world = world_with(ard_troops=GRINDING)
    sim = start(world)
    sim.run_days(100)
    for tag in ("ARD", "BOR"):
        assert world.country(tag).drone_saturation == pytest.approx(100 * lw.DRONE_ADAPTATION_PER_DAY, abs=0.005)


def test_assaults_from_freshly_taken_ground_are_weaker():
    sim = start(world_with())
    for _ in range(60):
        sim.run_days(1)
        if sim.world.provinces[1].controller == "ARD":
            break
    sim.run_days(1)
    assert sim.land.reach["ARD"][11] == 0            # Owned soil: the rail runs there.
    assert sim.land.reach["ARD"][1] == 1             # Just taken: supply by truck.
    assert sim.land._reach_factor("ARD", 1) == pytest.approx(lw.REACH_PER_HOP)


def test_armies_that_are_not_rail_bound_reach_further():
    sim = start(world_with())
    for _ in range(60):
        sim.run_days(1)
        if sim.world.provinces[1].controller == "ARD":
            break
    sim.run_days(1)
    assert sim.land._reach_factor("ARD", 1) == pytest.approx(lw.REACH_PER_HOP)  # Russia, 2022: tied to its railheads.
    sim.world.country("ARD").reach_per_hop = 0.85                               # The US, 2003: trucks and airlift.
    sim.run_days(1)
    assert sim.land.hops_from_rear("ARD", 1) == 1
    assert sim.land._reach_factor("ARD", 1) == pytest.approx(0.85)


def test_hosting_ends_after_the_agreed_window():
    world = world_with(ard_troops=GRINDING)
    world.country("DRV").hosts = frozenset({"ARD"})
    world.country("DRV").hosting_days = 10
    sim = start(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    assert "DRV" in sim.land._hosts["ARD"]
    sim.run_days(12)
    assert not sim.finished
    assert "DRV" not in sim.land._hosts.get("ARD", set())
    assert all(origin != 20 for origin, _, _ in sim.land.deployments["ARD"].attacks.values())
