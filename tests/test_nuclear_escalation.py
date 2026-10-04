"""Decisions 1 and 2: Tier 2 escalation ratchet, victim response, compellence and sparing use."""

from __future__ import annotations

import random

from conftest import BOR_CAPITAL, build_world, occupy, patriotic_spirit, unstable_spirit

from wargame.conflict.war import War
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import (
    Branch,
    EscalationTier,
    MissileClass,
    Motivation,
    NuclearDoctrine,
    ParticipantRole,
    Side,
    WarGoalType,
)
from wargame.core.escalation import EscalationPolicy
from wargame.nation.country import CapitulationContext, DailyContext
from wargame.nation.military import EquipmentStock
from wargame.nation.nuclear import NuclearContext, NuclearPosture, nuclear_shock_response

REGIME_CHANGE = WarGoal(WarGoalType.REGIME_CHANGE, holder="ARD", target="BOR")


def arsenal(doctrine: NuclearDoctrine = NuclearDoctrine.ESCALATE_TO_DEESCALATE, warheads: int = 50) -> NuclearPosture:
    return NuclearPosture(warheads=warheads, delivery=frozenset({MissileClass.THEATER}), doctrine=doctrine)


def overwhelming_ardania(world) -> None:
    """Make Ardania a great power next to small Borovia (power ratio well above 6)."""
    world.country("ARD").oob.equipment["mbt"] = EquipmentStock("MBT", Branch.LAND, quantity=3_000, quality=0.9)


def declare(world, tier: EscalationTier) -> War:
    return War.declare(world, REGIME_CHANGE, tier, nuclear_weapons_enabled=True,
                       attacker_motivation=Motivation.AGGRESSIVE, defender_motivation=Motivation.CAUTIOUS, now_hour=0)


# --- the victim's response ----------------------------------------------------------


def test_small_unstable_nation_collapses_under_nuclear_attack():
    r = nuclear_shock_response(can_retaliate=False, population_ratio=0.1, stability=0.3)
    assert not r.rally
    assert r.war_support < -0.25 and r.stability < -0.3 and r.capitulation_shock > 0.25


def test_nuclear_peer_rallies_for_revenge():
    r = nuclear_shock_response(can_retaliate=True, population_ratio=2.3, stability=0.8)
    assert r.rally
    assert r.war_support > 0 and r.capitulation_shock == 0.0


def test_two_strikes_break_even_a_last_stand_nation():
    world = build_world(bor_spirit=patriotic_spirit())
    bor, ard = world.country("BOR"), world.country("ARD")
    assert bor.spirit.fights_to_last_man(0)

    bor.absorb_nuclear_strike(world, ard, now_hour=0)
    bor.absorb_nuclear_strike(world, ard, now_hour=72)

    assert not bor.spirit.fights_to_last_man(72)
    assert bor.nuclear_shock > 0.3


def test_shock_adds_capitulation_pressure_and_fades():
    world = build_world(bor_spirit=unstable_spirit())
    bor = world.country("BOR")
    bor.absorb_nuclear_strike(world, world.country("ARD"), now_hour=0)
    ctx = CapitulationContext(now_hour=0, hostile_tags=frozenset({"ARD"}))
    _, components = bor.capitulation_pressure(world, ctx)
    assert components["nuclear_shock"] > 0.2
    shock = bor.nuclear_shock
    bor.on_daily_tick(world, DailyContext(now_hour=24, hostile_tags=frozenset({"ARD"})))
    assert bor.nuclear_shock < shock


# --- compellence: sealing the deal against a stubborn, smaller enemy ------------------


def ctx(tier: EscalationTier, **kw) -> NuclearContext:
    base = dict(weapons_enabled=True, policy=EscalationPolicy.for_tier(tier), collapse_proximity=0.0,
                existential_threat=False, enemy_used_nuclear=False, enemy_warheads=0, enemy_second_strike=False,
                enemy_nuclear_umbrella=False, own_bmd_vs_retaliation=0.0, trade_dependence=0.3,
                pursuing_goal=True, goal_existential=True, power_ratio=8.0, stubbornness=1.0)
    base.update(kw)
    return NuclearContext(**base)


def test_great_power_considers_sealing_the_deal_in_a_vacuum():
    a = arsenal().evaluate_hesitation(ctx(EscalationTier.VACUUM))
    assert a.considering_use
    assert a.components["compellence"] < 0


def test_outside_a_vacuum_compellence_is_rare_or_absent():
    for tier in (EscalationTier.PROXY_WAR, EscalationTier.UNRESTRICTED):
        assert not arsenal().evaluate_hesitation(ctx(tier)).considering_use


def test_no_compellence_against_an_equal_or_a_nuclear_enemy():
    assert "compellence" not in arsenal().evaluate_hesitation(ctx(EscalationTier.VACUUM, power_ratio=1.2)).components
    assert "compellence" not in arsenal().evaluate_hesitation(ctx(EscalationTier.VACUUM, enemy_warheads=50)).components


def test_a_fresh_war_is_not_yet_a_stubborn_one():
    assert not arsenal().evaluate_hesitation(ctx(EscalationTier.VACUUM, stubbornness=0.0)).considering_use


def test_no_first_use_pledge_survives_compellence():
    assert not arsenal(NuclearDoctrine.NO_FIRST_USE).evaluate_hesitation(ctx(EscalationTier.VACUUM)).considering_use


def test_each_strike_makes_the_next_harder():
    first = arsenal().evaluate_hesitation(ctx(EscalationTier.VACUUM)).hesitation
    later = arsenal().evaluate_hesitation(ctx(EscalationTier.VACUUM, own_prior_strikes=3)).hesitation
    assert later > first


def test_stubbornness_builds_as_the_war_drags_on():
    world = build_world(bor_spirit=patriotic_spirit())
    war = declare(world, EscalationTier.VACUUM)
    war.on_daily_tick(world, 24, random.Random(0))
    assert war.stubbornness(world, 24) == 0.0
    assert war.stubbornness(world, 24 * 200) > 0.5


# --- strike cadence -------------------------------------------------------------------


def test_coercive_strikes_are_spaced_out_unless_answered():
    world = build_world()
    world.country("ARD").nuclear = arsenal()
    war = declare(world, EscalationTier.VACUUM)
    war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))
    assert war._awaiting_strike_effect("ARD", now_hour=48)
    assert not war._awaiting_strike_effect("ARD", now_hour=24 + 72)

    world.country("BOR").nuclear = arsenal()
    war.resolve_nuclear_strike(world, "BOR", now_hour=30, rng=random.Random(0))
    assert not war._awaiting_strike_effect("ARD", now_hour=48)  # Retaliation is answered at once.


def test_vacuum_war_against_a_stubborn_minnow_goes_nuclear_eventually():
    world = build_world(bor_spirit=patriotic_spirit())
    overwhelming_ardania(world)
    world.country("ARD").nuclear = arsenal()
    war = declare(world, EscalationTier.VACUUM)
    occupy(world, [1], by="ARD")  # A grinding war that is going nowhere.

    rng = random.Random(7)
    for day in range(1, 400):
        war.on_daily_tick(world, day * 24, rng)
        if war.nuclear_strikes or war.ended:
            break
    assert war.nuclear_strikes, "a great power in a vacuum should eventually use its arsenal"
    assert war.nuclear_strikes[0].hour > 60 * 24  # Only after the war has dragged on.


def test_same_war_in_proxy_tier_stays_conventional():
    world = build_world(bor_spirit=patriotic_spirit())
    overwhelming_ardania(world)
    world.country("ARD").nuclear = arsenal()
    war = declare(world, EscalationTier.PROXY_WAR)
    occupy(world, [1], by="ARD")
    rng = random.Random(7)
    for day in range(1, 400):
        war.on_daily_tick(world, day * 24, rng)
    assert not war.nuclear_strikes


# --- Tier 2 ratchet and patron intervention -------------------------------------------


def test_nuclear_use_in_a_proxy_war_brings_committed_patrons_in():
    world = build_world()
    world.country("ARD").nuclear = arsenal()
    world.country("CAL").relations.update({"BOR": 0.8, "ARD": -0.6})  # Committed patron.
    world.country("DRV").relations.update({"BOR": 0.55, "ARD": -0.3})  # Supplies arms, won't fight.
    war = declare(world, EscalationTier.PROXY_WAR)
    assert {f.supporter for f in war.external_support} == {"CAL", "DRV"}

    war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))

    assert war.escalation_tier is EscalationTier.UNRESTRICTED
    assert war.participants["CAL"].side is Side.DEFENDER
    assert war.participants["CAL"].role is ParticipantRole.CO_BELLIGERENT
    assert "DRV" not in war.participants
    kinds = [e.kind for e in war.events]
    assert "escalation" in kinds and "patron_intervention" in kinds


def test_vacuum_has_no_ratchet():
    world = build_world()
    world.country("ARD").nuclear = arsenal()
    war = declare(world, EscalationTier.VACUUM)
    war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))
    assert war.escalation_tier is EscalationTier.VACUUM


def test_strong_patrons_deter_nuclear_use_in_a_proxy_war():
    world = build_world()
    world.country("ARD").nuclear = arsenal()
    war = declare(world, EscalationTier.PROXY_WAR)
    alone = war.nuclear_context(world, "ARD", now_hour=24)

    world.country("CAL").relations.update({"BOR": 0.8, "ARD": -0.6})
    war.review_external_support(world, 24)
    backed = war.nuclear_context(world, "ARD", now_hour=24)

    assert alone.enemy_patron_power_ratio == 0.0
    assert backed.enemy_patron_power_ratio > 0.0
    h_alone = world.country("ARD").nuclear.evaluate_hesitation(alone).hesitation
    h_backed = world.country("ARD").nuclear.evaluate_hesitation(backed).hesitation
    assert h_backed > h_alone


def test_detonation_on_target_province_is_reflected_in_victim_morale():
    world = build_world(bor_spirit=unstable_spirit())
    world.country("ARD").nuclear = arsenal()
    war = declare(world, EscalationTier.VACUUM)
    before = world.country("BOR").spirit.effective_war_support(24)
    war.resolve_nuclear_strike(world, "ARD", now_hour=24, rng=random.Random(0))
    assert world.provinces[BOR_CAPITAL].damage >= 0.9
    assert world.country("BOR").spirit.effective_war_support(24) < before
    assert any(e.kind == "nuclear_shock" for e in war.events)
    assert world.country("BOR").nuclear_shock > 0.0
