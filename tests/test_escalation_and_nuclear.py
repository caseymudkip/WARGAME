from __future__ import annotations

import random

import pytest

from conftest import BOR_CAPITAL, build_world, occupy, unstable_spirit

from wargame.conflict.war import War
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import (
    EscalationTier,
    MissileClass,
    Motivation,
    NuclearDoctrine,
    ParticipantRole,
    Side,
    SupplyType,
    WarGoalType,
)
from wargame.core.escalation import EscalationPolicy
from wargame.nation.nuclear import (
    MissileDefenseSystem,
    NuclearContext,
    NuclearPosture,
    layered_intercept_probability,
)

REGIME_CHANGE = WarGoal(WarGoalType.REGIME_CHANGE, holder="ARD", target="BOR")


def declare(world, tier: EscalationTier, goal: WarGoal = REGIME_CHANGE, nukes: bool = False) -> War:
    return War.declare(world, goal, tier, nuclear_weapons_enabled=nukes,
                       attacker_motivation=Motivation.AGGRESSIVE, defender_motivation=Motivation.CAUTIOUS, now_hour=0)


def befriend(world, supporter: str, recipient: str, enemy: str) -> None:
    world.country(supporter).relations.update({recipient: 0.8, enemy: -0.6})


# --- escalation tiers ----------------------------------------------------------


def test_tier_one_has_no_lend_lease_even_for_close_friends(world):
    befriend(world, "CAL", "BOR", "ARD")
    war = declare(world, EscalationTier.VACUUM)
    assert war.external_support == []


def test_tier_two_lend_lease_follows_geopolitical_leanings(world):
    befriend(world, "CAL", "BOR", "ARD")
    war = declare(world, EscalationTier.PROXY_WAR)

    [flow] = war.external_support
    assert (flow.supporter, flow.recipient) == ("CAL", "BOR")
    assert "CAL" not in war.participants  # No boots on the ground at Tier 2.

    war.on_daily_tick(world, 24, random.Random(0))
    assert world.country("BOR").logistics.lend_lease_inbound[SupplyType.FUEL] > 0
    assert war.support_level(world, "BOR") > 0


def test_tier_two_ignores_defensive_pacts(world):
    world.country("BOR").defensive_pacts.add("CAL")
    war = declare(world, EscalationTier.PROXY_WAR)
    assert "CAL" not in war.participants


def test_tier_three_defensive_pacts_trigger_immediately(world):
    world.country("BOR").defensive_pacts.add("CAL")
    war = declare(world, EscalationTier.UNRESTRICTED)
    cal = war.participants["CAL"]
    assert cal.side is Side.DEFENDER and cal.role is ParticipantRole.CO_BELLIGERENT


class AlwaysRolls(random.Random):
    """Every probabilistic check succeeds, so tests exercise the rules, not the dice."""

    def random(self) -> float:
        return 0.0


def _weakened_borovia(tier: EscalationTier):
    world = build_world(bor_spirit=unstable_spirit())
    world.country("DRV").relations["BOR"] = -0.9  # Rival neighbour.
    world.country("CAL").relations["BOR"] = -0.9  # Rival, but not adjacent.
    war = declare(world, tier)
    occupy(world, [1, 2, 3], by="ARD")
    war.on_daily_tick(world, 24, AlwaysRolls())
    return world, war


def test_tier_three_opportunist_piles_onto_a_collapsing_rival():
    world, war = _weakened_borovia(EscalationTier.UNRESTRICTED)
    assert war.assessments["BOR"].proximity >= 0.6
    assert war.side_of("DRV") is Side.ATTACKER
    assert war.participants["DRV"].role is ParticipantRole.CO_BELLIGERENT
    assert "CAL" not in war.participants  # Hostile, but no shared border.


def test_tier_two_never_admits_opportunists():
    world, war = _weakened_borovia(EscalationTier.PROXY_WAR)
    assert war.assessments["BOR"].proximity >= 0.6
    assert "DRV" not in war.participants


# --- missile defence -------------------------------------------------------------


def test_bmd_layers_combine_independently():
    upper = MissileDefenseSystem("exo-layer", frozenset({MissileClass.THEATER}), single_shot_pk=0.5, interceptors=10)
    lower = MissileDefenseSystem("terminal", frozenset({MissileClass.THEATER, MissileClass.TACTICAL}),
                                 single_shot_pk=0.5, interceptors=10, shots_per_target=1)
    # Upper: 1-(0.5^2)=0.75. Lower: 0.5. Combined: 1 - 0.25*0.5 = 0.875.
    assert layered_intercept_probability([upper, lower], MissileClass.THEATER, 1) == pytest.approx(0.875)
    assert layered_intercept_probability([upper, lower], MissileClass.STRATEGIC, 1) == 0.0


def test_bmd_runs_dry():
    layer = MissileDefenseSystem("terminal", frozenset({MissileClass.TACTICAL}), 0.9, interceptors=2)
    layer.expend(MissileClass.TACTICAL, 1)
    assert layer.intercept_probability(MissileClass.TACTICAL, 1) == 0.0


# --- nuclear hesitation --------------------------------------------------------------


def nuclear_ctx(tier: EscalationTier, **overrides) -> NuclearContext:
    base = dict(weapons_enabled=True, policy=EscalationPolicy.for_tier(tier), collapse_proximity=0.0,
                existential_threat=False, enemy_used_nuclear=False, enemy_warheads=0, enemy_second_strike=False,
                enemy_nuclear_umbrella=False, own_bmd_vs_retaliation=0.0, trade_dependence=0.3)
    base.update(overrides)
    return NuclearContext(**base)


def posture(doctrine: NuclearDoctrine = NuclearDoctrine.ESCALATE_TO_DEESCALATE) -> NuclearPosture:
    return NuclearPosture(warheads=50, delivery=frozenset({MissileClass.THEATER}), doctrine=doctrine)


def test_hesitation_is_lowest_in_a_vacuum():
    situation = dict(collapse_proximity=1.0, existential_threat=True)
    h = {tier: posture().evaluate_hesitation(nuclear_ctx(tier, enemy_nuclear_umbrella=True, **situation)).hesitation
         for tier in EscalationTier}
    assert h[EscalationTier.VACUUM] < h[EscalationTier.PROXY_WAR] < h[EscalationTier.UNRESTRICTED]


def test_disabled_toggle_means_no_nuclear_option():
    a = posture().evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM, weapons_enabled=False,
                                                  collapse_proximity=1.0, existential_threat=True))
    assert a.hesitation == 1.0 and not a.considering_use


def test_comfortable_nation_never_considers_use_even_in_a_vacuum():
    a = posture().evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM))
    assert not a.considering_use


def test_desperate_nation_in_a_vacuum_considers_use():
    a = posture().evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM, collapse_proximity=1.0, existential_threat=True))
    assert a.considering_use
    assert a.components["desperation"] < 0


def test_mutually_assured_destruction_restrains_and_bmd_weakens_that_restraint():
    exposed = posture().evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM, enemy_warheads=100, enemy_second_strike=True))
    shielded = posture().evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM, enemy_warheads=100,
                                                         enemy_second_strike=True, own_bmd_vs_retaliation=0.9))
    assert exposed.components["mutually_assured_destruction"] > shielded.components["mutually_assured_destruction"]


def test_no_first_use_holds_until_the_enemy_goes_nuclear():
    desperate = dict(collapse_proximity=1.0, existential_threat=True)
    nfu = posture(NuclearDoctrine.NO_FIRST_USE)
    assert not nfu.evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM, **desperate)).considering_use
    assert nfu.evaluate_hesitation(nuclear_ctx(EscalationTier.VACUUM, enemy_used_nuclear=True, **desperate)).considering_use


def test_nuclear_strike_resolution_and_diplomatic_fallout(world):
    world.country("ARD").nuclear = posture()
    world.country("CAL").relations["ARD"] = 0.2
    war = declare(world, EscalationTier.PROXY_WAR, nukes=True)

    strike = war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))

    assert strike is not None and not strike.intercepted  # Borovia has no BMD.
    assert strike.province_id == BOR_CAPITAL                 # Highest-value target.
    assert world.provinces[BOR_CAPITAL].damage >= 0.9
    assert world.country("ARD").nuclear.warheads == 49
    assert world.country("ARD").sanction_severity > 0
    assert world.country("CAL").relations["ARD"] < 0.2


def test_vacuum_strike_carries_no_diplomatic_fallout(world):
    world.country("ARD").nuclear = posture()
    war = declare(world, EscalationTier.VACUUM, nukes=True)
    war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))
    assert world.country("ARD").sanction_severity == 0.0


def test_bmd_can_stop_a_strike(world):
    world.country("ARD").nuclear = posture()
    world.country("BOR").nuclear.missile_defenses.append(
        MissileDefenseSystem("area-defence", frozenset({MissileClass.THEATER}), single_shot_pk=1.0, interceptors=4)
    )
    war = declare(world, EscalationTier.VACUUM, nukes=True)
    strike = war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))
    assert strike is not None and strike.intercepted
    assert world.provinces[BOR_CAPITAL].damage == 0.0
