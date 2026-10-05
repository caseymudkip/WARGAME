"""The air war: superiority, its weight on the ground, strikes, repair, basing; calibration anchors on the real map."""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import build_world

from wargame.conflict import air_war as aw
from wargame.conflict.air_war import AirWar
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import Branch, EscalationTier, Motivation, WarGoalType
from wargame.data.world_map import build_real_world
from wargame.nation.military import EquipmentStock
from wargame.nation.nuclear import MissileDefenseSystem
from wargame.simulation import ScenarioConfig, Simulation
from wargame.world.world import World


def arm(world: World, tag: str, aircraft: int = 0, sam_battalions: int = 0) -> None:
    oob = world.country(tag).oob
    if aircraft:
        oob.equipment["fighter"] = EquipmentStock("fighter", Branch.AIR, aircraft, quality=1.0, readiness=1.0)
    if sam_battalions:
        world.country(tag).nuclear.missile_defenses.append(
            MissileDefenseSystem("S-400", frozenset(), single_shot_pk=0.0, interceptors=0, batteries=sam_battalions))


def war(world: World, goal: WarGoalType = WarGoalType.REGIME_CHANGE) -> Simulation:
    return Simulation(world, ScenarioConfig("air", datetime(2026, 1, 1), WarGoal(goal, "ARD", "BOR"), EscalationTier.VACUUM,
                                            attacker_motivation=Motivation.AGGRESSIVE))


# --- the contest for the sky --------------------------------------------------------------------------


def test_one_sam_battalion_weighs_as_much_as_a_hundred_aircraft_units():
    world = build_world()
    arm(world, "ARD", aircraft=1_000)
    air = AirWar()
    assert air._superiority(world, frozenset({"ARD"}), frozenset({"BOR"})) == pytest.approx(1.0)
    arm(world, "BOR", sam_battalions=10)
    assert air._superiority(world, frozenset({"ARD"}), frozenset({"BOR"})) == pytest.approx(0.5)


def test_bmd_only_systems_do_not_fight_aircraft():
    world = build_world()
    arm(world, "ARD", aircraft=1_000)
    world.country("BOR").nuclear.missile_defenses.append(
        MissileDefenseSystem("GMD", frozenset(), single_shot_pk=0.5, interceptors=44, batteries=10))
    assert AirWar()._superiority(world, frozenset({"ARD"}), frozenset({"BOR"})) == pytest.approx(1.0)


def test_dominance_is_decisive_on_the_ground_and_contested_skies_are_not():
    world = build_world()
    arm(world, "ARD", aircraft=1_000)
    sim = war(world)
    assert sim.air.ground_multiplier("ARD") == pytest.approx(1.0 + aw.AIR_GROUND_MAX)  # 1991: the coalition.
    assert sim.air.ground_multiplier("BOR") == pytest.approx(1.0 - aw.AIR_GROUND_MAX)

    world = build_world()
    arm(world, "ARD", aircraft=1_000)
    arm(world, "BOR", sam_battalions=10)
    sim = war(world)
    assert 1.0 < sim.air.ground_multiplier("ARD") < 1.2  # Russia's glide bombs, 2024-25: an edge, not a rout.


def test_wrecked_airfields_fly_fewer_sorties():
    world = build_world()
    arm(world, "ARD", aircraft=1_000)
    full = AirWar.air_power(world, frozenset({"ARD"}))
    world.provinces[12].damage = 0.5  # ARD's only airfield.
    assert AirWar.air_power(world, frozenset({"ARD"})) == pytest.approx(0.5 * full)


# --- strikes ---------------------------------------------------------------------------------------


def _damage_after(days: int, sam_battalions: int) -> float:
    world = build_world()
    arm(world, "ARD", aircraft=2_000)
    arm(world, "BOR", sam_battalions=sam_battalions)
    sim = war(world, WarGoalType.COERCION)
    sim.run_days(days)
    return AirWar.leverage(world, "BOR")


def test_strikes_wreck_an_undefended_country_but_not_one_behind_dense_air_defences():
    open_sky = _damage_after(30, sam_battalions=0)
    defended = _damage_after(30, sam_battalions=20)  # s = 0.5: an eighth of the strikes get through.
    assert open_sky > 0.3
    assert defended < 0.5 * open_sky


def test_damage_is_repaired_over_months():
    world = build_world()
    world.provinces[5].damage = 0.5
    for _ in range(46):
        AirWar.repair(world)
    assert 0.2 < world.provinces[5].damage < 0.3  # Roughly halved in six weeks (Ukraine's grid, 2022-23).


def test_strike_casualties_are_never_rounded_away():
    world = build_world()
    arm(world, "ARD", aircraft=10)
    sim = war(world, WarGoalType.COERCION)
    sim.run_days(1)
    assert world.country("BOR").oob.casualties_total > 0


# --- the real map ------------------------------------------------------------------------------------


def _sim(year: int, attacker: str, defender: str, goal: WarGoalType) -> Simulation:
    world = build_real_world(year).world
    return Simulation(world, ScenarioConfig("air", datetime(year, 3, 24), WarGoal(goal, attacker, defender),
                                            EscalationTier.PROXY_WAR))


def test_superiority_anchors_on_the_real_map():
    sim = _sim(2026, "RUS", "UKR", WarGoalType.REGIME_CHANGE)
    s = sim.air.superiority
    assert 0.3 < s[("RUS", "UKR")] < 0.7  # Russia never won the sky over Ukraine...
    assert s[("UKR", "RUS")] < 0.1        # ...nor could Ukraine contest Russia's.
    sim = _sim(2026, "USA", "VEN", WarGoalType.COERCION)
    assert sim.air.superiority[("USA", "VEN")] > 0.9


def test_allies_lend_their_airfields():
    world = build_real_world(2021).world
    bases = AirWar._basing(world, frozenset({"USA"}), frozenset({"SRB"}))
    assert "ITA" in bases  # Aviano, 1999.
    assert "SRB" not in bases


def test_a_kosovo_style_air_campaign_forces_concessions_in_weeks_not_days():
    sim = _sim(2021, "USA", "SRB", WarGoalType.COERCION)
    war_ = sim.wars[0]
    for day in range(1, 151):
        sim.run_days(1)
        if war_.ended:
            break
    assert war_.ended and war_.treaty is not None and war_.treaty.winner == "USA"
    assert 40 <= day <= 130  # NATO, 1999: Belgrade gave way after 78 days.
