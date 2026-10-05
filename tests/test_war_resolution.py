from __future__ import annotations

from datetime import datetime

from conftest import BOR_CAPITAL, build_world, occupy, patriotic_spirit, unstable_spirit

from wargame.conflict.war import OPENING_CAMPAIGN_DAYS
from wargame.conflict.treaty import draft_treaty
from wargame.conflict.war_goal import WarGoal
from wargame.core.clock import Cadence
from wargame.core.enums import EscalationTier, Motivation, TermType, WarGoalType
from wargame.simulation import ScenarioConfig, Simulation

START = datetime(2026, 1, 1)


def scenario(goal: WarGoal, attacker: Motivation = Motivation.CAUTIOUS,
             tier: EscalationTier = EscalationTier.VACUUM) -> ScenarioConfig:
    return ScenarioConfig(name="test", start=START, war_goal=goal, escalation_tier=tier,
                          attacker_motivation=attacker, land_combat=False)  # Fronts are scripted here.


def scripted_offensive(day: int, pids: list[int], by: str = "ARD"):
    """A stand-in for the combat system: take the given provinces on a given day."""
    def system(sim: Simulation) -> None:
        if sim.clock.day == day:
            occupy(sim.world, pids, by)
    return system


def bleed(casualties: dict[str, int]):
    def system(sim: Simulation) -> None:
        for war in sim.active_wars:
            for tag, n in casualties.items():
                if tag in war.participants:
                    war.record_casualties(sim.world, tag, n)
    return system


def term_types(sim: Simulation) -> list[TermType]:
    return [t.type for t in sim.wars[0].treaty.terms]


# --- limited wars end as soon as the objective is held ---------------------------


def test_border_skirmish_ends_once_the_provinces_are_secured():
    world = build_world()
    sim = Simulation(world, scenario(WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1, 2}))))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1, 2, 3]))

    sim.run_days(5)

    assert sim.finished
    assert term_types(sim) == [TermType.PROVINCE_TRANSFER]
    assert world.provinces[1].owner == world.provinces[2].owner == "ARD"
    # A cautious attacker doesn't spend war score on extras: the overrun province 3 goes back.
    assert world.provinces[3].owner == world.provinces[3].controller == "BOR"


def test_aggressive_attacker_grabs_extra_ground_at_the_table():
    world = build_world()
    sim = Simulation(world, scenario(WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1, 2})),
                                     attacker=Motivation.AGGRESSIVE))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1, 2, 3]))

    sim.run_days(5)

    assert sim.finished
    assert world.provinces[3].owner == "ARD"


# --- regime change hinges on the capital -------------------------------------------


def test_regime_change_requires_the_capital():
    world = build_world(bor_spirit=patriotic_spirit())  # Fights to the last: won't capitulate.
    sim = Simulation(world, scenario(WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"), Motivation.AGGRESSIVE))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1, 2, 3, 4, 5, 6, 8]))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=20, pids=[BOR_CAPITAL]))

    sim.run_days(19)
    assert not sim.finished
    assert sim.wars[0].assessments["BOR"].last_stand

    sim.run_days(3)
    war = sim.wars[0]
    # The regime has changed: the old government signed and became a puppet...
    assert TermType.PUPPET in [t.type for t in war.settlements[0].terms]
    assert world.country("BOR").overlord == "ARD"
    # ...but this nation is too patriotic to quit: Free Borovia fights on from the free provinces.
    assert "FREE_BOR" in war.participants and not sim.finished


def test_regime_change_ends_the_war_when_nobody_is_left_to_fight_on():
    world = build_world(bor_spirit=unstable_spirit())
    sim = Simulation(world, scenario(WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"), Motivation.AGGRESSIVE))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1, BOR_CAPITAL]))

    sim.run_days(4)

    assert sim.finished
    assert TermType.PUPPET in term_types(sim)
    assert world.country("BOR").overlord == "ARD"


def test_regime_change_with_territorial_demands_takes_both():
    world = build_world(bor_spirit=unstable_spirit())
    goal = WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR", frozenset({1, 2}))  # A puppet, and these two annexed.
    sim = Simulation(world, scenario(goal, Motivation.AGGRESSIVE))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1, BOR_CAPITAL]))

    sim.run_days(4)

    assert sim.finished
    assert {TermType.PUPPET, TermType.PROVINCE_TRANSFER} <= set(term_types(sim))
    assert world.country("BOR").overlord == "ARD"
    assert world.provinces[1].owner == world.provinces[2].owner == "ARD"  # 2 was never even taken.


def test_total_capitulation_ends_in_annexation():
    world = build_world(bor_spirit=unstable_spirit())
    sim = Simulation(world, scenario(WarGoal(WarGoalType.TOTAL_CAPITULATION, "ARD", "BOR"), Motivation.AGGRESSIVE))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1, 2, 3, 4, 5]))

    sim.run_days(15)

    assert sim.finished
    assert term_types(sim) == [TermType.ANNEXATION]
    assert all(p.owner == "ARD" for p in world.provinces.values() if p.id <= 10)
    assert any(e.kind == "collapse_begins" for e in sim.events())


# --- cost/reward: bleeding without gains breaks the attacker --------------------------


def _bloody_stalemate(motivation: Motivation) -> tuple[Simulation, int]:
    world = build_world()
    sim = Simulation(world, scenario(WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1})), motivation))
    sim.register_system(Cadence.DAILY, bleed({"ARD": 1_500, "BOR": 1_500}))
    for day in range(1, 120):
        sim.run_days(1)
        if sim.finished:
            return sim, day
    raise AssertionError("war never ended")


def test_attacker_halts_then_sues_for_white_peace_when_losses_buy_nothing():
    sim, _ = _bloody_stalemate(Motivation.CAUTIOUS)
    kinds = [e.kind for e in sim.events()]
    assert "offensive_halted" in kinds
    assert sim.wars[0].treaty.is_white_peace


def _halt_day(goal: WarGoal) -> int | None:
    sim = Simulation(build_world(), scenario(goal, Motivation.AGGRESSIVE))
    sim.register_system(Cadence.DAILY, bleed({"ARD": 4_000, "BOR": 300}))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[1]))  # A little ground at a ruinous price.
    for day in range(1, 61):
        sim.run_days(1)
        if sim.wars[0].participants["ARD"].offensive_halted:
            return day
    return None


def test_an_all_out_invasion_runs_its_opening_campaign_before_it_is_reassessed():
    invasion = WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR")
    assert _halt_day(invasion) == OPENING_CAMPAIGN_DAYS  # Russia's "first stage", 24 Feb - 25 Mar 2022.
    assert (_halt_day(WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({1, 2})))
            < OPENING_CAMPAIGN_DAYS)  # A limited operation is judged as it goes.


def test_epic_motivation_bleeds_far_longer_than_realistic():
    _, cautious_days = _bloody_stalemate(Motivation.CAUTIOUS)
    _, aggressive_days = _bloody_stalemate(Motivation.AGGRESSIVE)
    assert aggressive_days > 2 * cautious_days


def test_defender_dictates_terms_when_the_attacker_breaks_while_losing():
    world = build_world()
    sim = Simulation(world, scenario(WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1}))))
    sim.register_system(Cadence.DAILY, bleed({"ARD": 3_000, "BOR": 300}))
    sim.register_system(Cadence.DAILY, scripted_offensive(day=2, pids=[11], by="BOR"))  # Counter-attack.

    sim.run_days(60)

    assert sim.finished
    treaty = sim.wars[0].treaty
    assert treaty.winner == "BOR"
    assert world.provinces[11].owner == "BOR"


def test_a_defender_that_outlasts_an_invader_takes_only_what_it_holds():
    """Iran-Iraq 1988, Ethiopia-Eritrea 2000: the invader gives up; the defender does not carve it up."""
    world = build_world()
    world.country("ARD").capitulated = True
    goal = WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({1}))

    def terms() -> set[int]:
        treaty = draft_treaty(world=world, goal=goal, winner="BOR", loser="ARD", winner_side={"BOR"}, war_score=100.0,
                              ambition=1.0, goal_achieved=False, signed_hour=0, reason="test")
        return set(treaty.transferred_provinces())

    assert terms() == set()
    occupy(world, [11], "BOR")  # Its counterattack crossed the border.
    assert terms() == {11}

