"""Strategy: when defenders strike back, when attackers give ground up (fictional map from conftest)."""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import build_world, occupy

from wargame.conflict import land_warfare as lw
from wargame.conflict import strategy
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.simulation import ScenarioConfig, Simulation
from wargame.world.world import FortifiedLine, World


def world_with(ard: int, bor: int) -> World:
    world = build_world()
    for p in world.provinces.values():
        p.area_km2 = 3_000.0
    world.country("ARD").oob.active_personnel = ard
    world.country("ARD").mobilised = True
    world.country("BOR").oob.active_personnel = bor
    world.country("BOR").mobilised = True
    return world


def start(world: World, goal: WarGoal | None = None) -> Simulation:
    goal = goal or WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR")
    return Simulation(world, ScenarioConfig("strategy", datetime(2026, 1, 1), goal, EscalationTier.VACUUM,
                                            attacker_motivation=Motivation.AGGRESSIVE))


# --- posture -----------------------------------------------------------------------------------------


def _thin_sector(world: World) -> tuple[Simulation, float]:
    """ARD has taken two Borovian provinces in this war and holds them with a screen of 20, its main
    force (2,000) kept elsewhere."""
    sim = start(world)
    occupy(world, [1, 2], "ARD")
    sim.land.deployments["ARD"].stationed = {2: 20.0, 12: 2_000.0}
    return sim, lw.ground_power(world.country("BOR")) * lw.COMMITMENT_EXISTENTIAL_DEFENCE


def test_a_defender_strikes_where_the_enemy_is_thin():
    sim, power = _thin_sector(world_with(ard=200_000, bor=200_000))
    share, posture = strategy.offensive_share(sim.land, sim.world, sim.active_wars, "BOR", power, {"ARD"}, 1.0)
    assert power < sim.land.committed_power({"ARD"})  # Outnumbered overall...
    assert posture is strategy.Posture.COUNTEROFFENSIVE and share > strategy.COUNTERATTACK_SHARE  # ...but Kharkiv, 2022.
    assert strategy.worth_attacking(sim.land, sim.world, sim.active_wars, "BOR", 2)


def test_a_defender_does_not_throw_itself_at_a_dug_in_drone_watched_line():
    world = world_with(ard=200_000, bor=200_000)
    world.country("ARD").drone_saturation = 1.0
    sim, power = _thin_sector(world)
    sim.land.fortification[2] = lw.FORTIFICATION_MAX
    share, posture = strategy.offensive_share(sim.land, sim.world, sim.active_wars, "BOR", power, {"ARD"}, 1.0)
    assert posture is not strategy.Posture.COUNTEROFFENSIVE  # 2:1 here buys ~60 m a day: Ukraine's restraint, 2024-25.
    assert not strategy.worth_attacking(sim.land, sim.world, sim.active_wars, "BOR", 2)


def test_an_attacker_presses_its_aim_whatever_the_price():
    world = world_with(ard=600_000, bor=100_000)
    world.country("BOR").drone_saturation = 1.0
    sim = start(world)
    assert sim.land.posture["ARD"] is strategy.Posture.OFFENSIVE
    assert sim.land.deployments["ARD"].attacks  # Russia, 2024-25: attacking a line that barely moves.


def test_neutral_soil_is_never_a_target():
    world = world_with(ard=600_000, bor=200_000)
    occupy(world, [20], "BOR")  # BOR troops sit in neutral DRV's province.
    sim = start(world)
    assert 20 not in sim.land.deployments["ARD"].attacks


# --- giving ground up ------------------------------------------------------------------------------------


def test_overextended_conquests_are_abandoned():
    world = world_with(ard=150_000, bor=400_000)
    occupy(world, [1, 2, 3, 4], "ARD")  # Deep, and freshly taken.
    sim = start(world)
    for pid in (2, 3, 4):
        sim.land._taken_hour[pid] = 0
    sim.run_days(10)
    kinds = [e.kind for e in sim.events()]
    assert "withdrawal" in kinds  # Kyiv, Chernihiv and Sumy, April 2022.
    assert world.provinces[4].controller == "BOR"


def test_a_bridgehead_across_a_major_river_is_given_up_when_outgunned():
    world = world_with(ard=150_000, bor=400_000)
    occupy(world, [1], "ARD")
    world.provinces[1].river_borders = ((11, 4),)  # The Dnipro behind it: Kherson, November 2022.
    world.provinces[11].river_borders = ((1, 4),)
    sim = start(world)
    assert any(w.province == 1 and "river" in w.reason
               for w in strategy.withdrawals(sim.land, world, sim.active_wars, *sim.land._sides(sim.active_wars)))


def test_own_soil_is_never_abandoned():
    world = world_with(ard=600_000, bor=50_000)
    sim = start(world)
    assert not [w for w in strategy.withdrawals(sim.land, world, sim.active_wars, *sim.land._sides(sim.active_wars))
                if w.tag == "BOR"]


def test_unpressed_gains_are_pushed_back_by_a_stronger_defence():
    world = world_with(ard=100_000, bor=300_000)
    sim = start(world)
    world.contested[1] = ("ARD", 0.5)  # Ground ARD took, then stopped pressing.
    sim.land.deployments["ARD"].attacks.pop(1, None)
    sim.land._reclaim(world, *sim.land._sides(sim.active_wars))
    assert world.contested.get(1, ("ARD", 0.0))[1] < 0.5


# --- halts -------------------------------------------------------------------------------------------------


def test_a_halted_offensive_regroups_for_at_least_two_weeks():
    from wargame.conflict.war import MIN_HALT_DAYS
    world = world_with(ard=300_000, bor=300_000)
    sim = start(world, WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1})))
    war = sim.wars[0]
    p = war.participants["ARD"]
    p.offensive_halted, p.halted_hour = True, sim.clock.hours_elapsed
    sim.run_days(MIN_HALT_DAYS - 1)
    assert p.offensive_halted
    assert MIN_HALT_DAYS == pytest.approx(14)


# --- how wars end and who joins --------------------------------------------------------------------------


def test_a_war_nobody_fights_freezes_into_an_armistice():
    from wargame.conflict.war import ARMISTICE_QUIET_DAYS
    world = world_with(ard=40_000, bor=400_000)  # Too weak to attack, and BOR won't invade.
    world.country("ARD").leadership_defiance = 1.0  # Never quits...
    occupy(world, [1], "ARD")
    sim = start(world, WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({2})))
    world.country("BOR").drone_saturation = 0.0
    sim.land.fortification[1] = lw.FORTIFICATION_MAX
    world.country("ARD").drone_saturation = 1.0  # ...and BOR sees no point attacking a drone-watched line.
    sim.run_days(ARMISTICE_QUIET_DAYS + 10)
    treaty = sim.wars[0].treaty
    assert treaty is not None and treaty.frozen  # Korea 1953: ground held stays held.
    assert world.provinces[1].controller == "ARD"


def test_peace_returns_only_what_this_war_took():
    from wargame.conflict.treaty import apply_treaty, white_peace
    world = world_with(ard=100_000, bor=100_000)
    occupy(world, [20], "BOR")  # BOR held DRV's province before this war...
    sim = start(world, WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1})))
    occupy(world, [1], "ARD")   # ...and ARD takes province 1 in it.
    apply_treaty(world, white_peace(0, "test"), {"ARD", "BOR"}, prewar_occupation=sim.wars[0].prewar_occupation)
    assert world.provinces[1].controller == "BOR"   # Status quo ante bellum...
    assert world.provinces[20].controller == "BOR"  # ...not a reset of every old occupation.


def test_in_total_war_close_partners_join_without_a_treaty():
    world = world_with(ard=200_000, bor=100_000)
    world.country("CAL").relations.update({"BOR": 0.7, "ARD": -0.6})  # The US and Taiwan.
    sim = Simulation(world, ScenarioConfig("total", datetime(2026, 1, 1), WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"),
                                           EscalationTier.UNRESTRICTED))
    assert "CAL" in sim.wars[0].participants
    assert any(e.kind == "intervention" for e in sim.events())
    proxy = Simulation(world_with(ard=200_000, bor=100_000), ScenarioConfig(
        "proxy", datetime(2026, 1, 1), WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"), EscalationTier.PROXY_WAR))
    assert "CAL" not in proxy.wars[0].participants


def test_limited_aims_get_limited_forces():
    big = start(world_with(ard=600_000, bor=100_000), WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({1})))
    small = start(world_with(ard=600_000, bor=100_000), WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1})))
    assert small.land._commitment(small.active_wars, "ARD") == pytest.approx(
        lw.GOAL_COMMITMENT["border_skirmish"] * big.land._commitment(big.active_wars, "ARD"))  # Kargil, Galwan.


def test_defenders_liberate_rather_than_invade():
    world = world_with(ard=100_000, bor=600_000)
    sim = start(world, WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1})))
    war = sim.wars[0]
    hops = sim.land._capital_hops(world, sim.active_wars)
    assert sim.land._relevance(world, sim.active_wars, "BOR", 11, hops) == lw.DEFENDER_INCURSION  # Kursk-sized.
    occupy(world, [2], "ARD")
    assert sim.land._relevance(world, sim.active_wars, "BOR", 2, hops) == 3.0
    del war
