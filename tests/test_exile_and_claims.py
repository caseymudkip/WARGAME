"""Decision 3: governments in exile (Free France pattern) and opportunists' own claims."""

from __future__ import annotations

import random

from conftest import BOR_CAPITAL, build_world, occupy, unstable_spirit

from wargame.conflict.war import War
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import (
    EscalationTier,
    Motivation,
    ParticipantRole,
    RegimeType,
    Side,
    TermType,
    WarGoalType,
)
from wargame.nation.national_spirit import NationalSpirit

OCCUPIED_HEARTLAND = [1, 2, 3, 4, 5, 6, BOR_CAPITAL, 8]  # Everything but 9 and 10.


class AlwaysRolls(random.Random):
    def random(self) -> float:
        return 0.0


def defiant_spirit() -> NationalSpirit:
    """Patriotic, but the government itself is shaky: it can fall while the nation fights on."""
    return NationalSpirit(patriotism=0.8, stability=0.35, war_support=0.5, regime=RegimeType.FLAWED_DEMOCRACY)


def run_until(war: War, world, predicate, days: int = 30, rng: random.Random | None = None) -> None:
    rng = rng or random.Random(0)
    for day in range(1, days + 1):
        war.on_daily_tick(world, day * 24, rng)
        if predicate():
            return


def declare(world, goal: WarGoal, tier: EscalationTier = EscalationTier.VACUUM,
            attacker: Motivation = Motivation.AGGRESSIVE) -> War:
    return War.declare(world, goal, tier, nuclear_weapons_enabled=False,
                       attacker_motivation=attacker, defender_motivation=Motivation.CAUTIOUS, now_hour=0)


# --- governments in exile ---------------------------------------------------------------


def test_free_state_forms_when_the_government_falls_but_morale_holds():
    world = build_world(bor_spirit=defiant_spirit())
    war = declare(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    occupy(world, OCCUPIED_HEARTLAND, by="ARD")

    run_until(war, world, lambda: "FREE_BOR" in war.participants)

    free = world.country("FREE_BOR")
    assert free.is_exile and free.name == "Free Borovia"
    assert {p.id for p in world.owned_by("FREE_BOR")} == {9, 10}
    assert war.participants["FREE_BOR"].side is Side.DEFENDER
    assert war.participants["FREE_BOR"].role is ParticipantRole.PRIMARY
    # The old government signed a separate surrender and became a puppet.
    assert "BOR" not in war.participants and "BOR" in war.exited
    assert world.country("BOR").overlord == "ARD"
    assert [t.type for t in war.settlements[0].terms][0] is TermType.PUPPET
    # Occupied land stays occupied while the war goes on.
    assert world.provinces[BOR_CAPITAL].controller == "ARD"
    # A loyal share of the forces followed the Free government.
    assert 0 < free.oob.active_personnel < 100_000


def test_strong_attacker_pursues_the_free_state():
    world = build_world(bor_spirit=defiant_spirit())
    war = declare(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    occupy(world, OCCUPIED_HEARTLAND, by="ARD")
    run_until(war, world, lambda: "FREE_BOR" in war.participants)

    assert not war.ended
    assert war.goal == WarGoal(WarGoalType.TOTAL_CAPITULATION, "ARD", "FREE_BOR")
    assert any(e.kind == "pursuit" for e in war.events)

    occupy(world, [9, 10], by="ARD")  # Run the Free state to ground.
    run_until(war, world, lambda: war.ended)
    assert war.ended
    # The war was for a new regime, not for land: the Free state's ground is reunited under the puppet.
    assert all(p.owner == "BOR" for p in world.provinces.values() if p.id in (9, 10))
    assert world.country("BOR").overlord == "ARD"


def test_exhausted_attacker_settles_and_the_free_state_survives():
    world = build_world(bor_spirit=defiant_spirit())
    war = declare(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    occupy(world, OCCUPIED_HEARTLAND, by="ARD")
    world.country("ARD").spirit.war_support = 0.0  # Its public has had enough.
    run_until(war, world, lambda: war.ended or "FREE_BOR" in war.participants)

    assert war.ended and war.treaty.is_white_peace
    assert any(e.kind == "ceasefire" for e in war.events)
    assert {p.id for p in world.owned_by("FREE_BOR")} == {9, 10}  # A rump state endures.


def test_no_exile_when_morale_is_low():
    world = build_world(bor_spirit=unstable_spirit())
    war = declare(world, WarGoal(WarGoalType.REGIME_CHANGE, "ARD", "BOR"))
    occupy(world, OCCUPIED_HEARTLAND, by="ARD")
    run_until(war, world, lambda: war.ended)
    assert war.ended and "FREE_BOR" not in world.countries


def test_no_exile_over_a_border_war():
    world = build_world(bor_spirit=defiant_spirit())
    war = declare(world, WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({1, 2})))
    occupy(world, OCCUPIED_HEARTLAND, by="ARD")
    world.country("BOR").collapse_progress = 0.99
    run_until(war, world, lambda: war.ended)
    assert war.ended and "FREE_BOR" not in world.countries


# --- opportunists bring their own claims ---------------------------------------------------


def test_opportunist_claims_border_land_and_keeps_it_at_the_peace():
    world = build_world(bor_spirit=unstable_spirit())
    world.country("DRV").relations["BOR"] = -0.9
    war = declare(world, WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1, 2})),
                  tier=EscalationTier.UNRESTRICTED, attacker=Motivation.CAUTIOUS)
    occupy(world, [3, 5], by="ARD")  # Push Borovia toward collapse without securing the goal yet.
    war.on_daily_tick(world, 24, AlwaysRolls())

    drv = war.participants["DRV"]
    assert drv.claim == WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "DRV", "BOR", frozenset({6}))

    occupy(world, [6], by="DRV")
    war.on_daily_tick(world, 48, random.Random(0))
    assert drv.offensive_halted
    assert any(e.kind == "claim_secured" for e in war.events)

    occupy(world, [1, 2], by="ARD")  # Ardania secures its own goal; the war ends.
    run_until(war, world, lambda: war.ended)
    assert world.provinces[6].owner == "DRV"
    assert world.provinces[1].owner == world.provinces[2].owner == "ARD"


def test_an_opportunist_never_joins_the_losing_side():
    """A collapsing victim inside a stronger coalition is no opportunity: the jackal would face them all."""
    world = build_world(bor_spirit=unstable_spirit())
    world.country("DRV").relations["BOR"] = -0.9
    war = declare(world, WarGoal(WarGoalType.BORDER_SKIRMISH, "ARD", "BOR", frozenset({1, 2})),
                  tier=EscalationTier.UNRESTRICTED, attacker=Motivation.CAUTIOUS)
    occupy(world, [3, 5], by="ARD")
    world.country("ARD").oob.active_personnel = 1_000  # Ardania's side is the weaker one.
    war.on_daily_tick(world, 24, AlwaysRolls())
    assert "DRV" not in war.participants

