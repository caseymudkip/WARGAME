"""Land warfare: fronts, assaults, encirclement, rivers, the sea and attrition.

Mechanics run on the fictional map in conftest.py (provinces shrunk to 300 km2 so fronts
move within a test); the calibration test runs the real 2026 front.
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
from wargame.world.world import World

ROOT = Path(__file__).resolve().parents[1]
AREA_KM2 = 300.0
GRINDING = 800_000   # ARD troops that attack BOR's front at R ~ 2: a slow, partial advance.


def world_with(ard_troops: int = 1_500_000, bor_troops: int = 100_000) -> World:
    world = build_world()
    for p in world.provinces.values():
        p.area_km2 = AREA_KM2
    world.country("ARD").oob.active_personnel = ard_troops
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
    assert sim.land.deployments["ARD"].attacks == {}       # Nothing reaches MIN_ASSAULT_RATIO...
    assert sim.world.country("ARD").oob.casualties_total == 0  # ...so nobody bleeds...
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


def test_fortification_grows_on_a_static_front_and_slows_attackers():
    sim = start(world_with(ard_troops=60_000))  # A front that doesn't move.
    assert sim.land.fortification[1] == pytest.approx(lw.ESTABLISHED_FRONT_FORTIFICATION + lw.FORTIFICATION_GROWTH)
    sim.run_days(5)
    assert sim.land.fortification[1] == pytest.approx(lw.ESTABLISHED_FRONT_FORTIFICATION + 6 * lw.FORTIFICATION_GROWTH)
    sim.run_days(30)
    assert sim.land.fortification[1] == lw.FORTIFICATION_MAX
    assert 2 not in sim.land.fortification  # Only the front digs in.

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

    with_fleet = world_with(ard_troops=GRINDING)
    _coast(with_fleet)
    with_fleet.country("ARD").oob.equipment["frigates"] = navy(40)
    attacks = start(with_fleet, goal).land.deployments["ARD"].attacks
    assert attacks.get(9, (0, 0.0, 0.0))[2] == 120  # Straight across the water to the objective.


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
