from __future__ import annotations

import pytest

from conftest import BOR_CAPITAL, build_world, occupy, patriotic_spirit, unstable_spirit

from wargame.core.enums import RegimeType
from wargame.nation.country import CapitulationContext
from wargame.nation.national_spirit import NationalSpirit

HOSTILE = frozenset({"ARD"})


def ctx(existential: bool = False, support: float = 0.0, allies: int = 0) -> CapitulationContext:
    return CapitulationContext(now_hour=0, hostile_tags=HOSTILE, existential_threat=existential,
                               external_support_level=support, allied_belligerents=allies)


def run_days(world, tag: str, context: CapitulationContext, days: int):
    country = world.country(tag)
    assessment = None
    for _ in range(days):
        assessment = country.evaluate_capitulation(world, context)
    return assessment


def test_threshold_scales_with_national_spirit():
    unstable = build_world(bor_spirit=unstable_spirit()).country("BOR")
    patriotic = build_world(bor_spirit=patriotic_spirit()).country("BOR")

    t_unstable, _ = unstable.capitulation_threshold(ctx())
    t_patriotic, _ = patriotic.capitulation_threshold(ctx())

    assert t_unstable == pytest.approx(0.25, abs=0.02)
    assert t_patriotic > 0.8


def test_last_stand_only_when_survival_is_at_stake():
    country = build_world(bor_spirit=patriotic_spirit()).country("BOR")
    assert country.capitulation_threshold(ctx(existential=True)) == (1.0, True)
    threshold, last_stand = country.capitulation_threshold(ctx(existential=False))
    assert not last_stand and threshold < 1.0


def test_external_support_and_allies_harden_resolve():
    country = build_world(bor_spirit=unstable_spirit()).country("BOR")
    base, _ = country.capitulation_threshold(ctx())
    supported, _ = country.capitulation_threshold(ctx(support=1.0, allies=2))
    assert supported > base


def test_unstable_nation_surrenders_after_losing_about_a_fifth_of_its_value():
    world = build_world(bor_spirit=unstable_spirit())
    world.country("BOR").mark_prewar_baseline(world)
    occupy(world, [1, 2, 3], by="ARD")  # ~23% of strategic value, incl. an industrial zone.

    assessment = run_days(world, "BOR", ctx(), days=7)

    assert assessment.components["territory"] == pytest.approx(0.232, abs=0.01)
    assert assessment.pressure > assessment.threshold
    assert assessment.capitulates


def test_minor_losses_do_not_topple_an_unstable_nation():
    world = build_world(bor_spirit=unstable_spirit())
    world.country("BOR").mark_prewar_baseline(world)
    occupy(world, [1], by="ARD")

    assessment = run_days(world, "BOR", ctx(), days=30)

    assert not assessment.capitulates
    assert assessment.collapse_progress == 0.0


def test_patriotic_nation_shrugs_off_the_same_losses():
    world = build_world(bor_spirit=patriotic_spirit())
    world.country("BOR").mark_prewar_baseline(world)
    occupy(world, [1, 2, 3], by="ARD")

    assessment = run_days(world, "BOR", ctx(), days=30)

    assert not assessment.capitulates


def test_collapse_takes_time_and_recovers_if_pressure_eases():
    world = build_world(bor_spirit=unstable_spirit())
    country = world.country("BOR")
    country.mark_prewar_baseline(world)
    occupy(world, [1, 2, 3], by="ARD")

    first = country.evaluate_capitulation(world, ctx())
    assert 0.0 < first.collapse_progress < 1.0 and not first.capitulates

    occupy(world, [1, 2, 3], by="BOR")  # Counter-offensive retakes everything.
    later = run_days(world, "BOR", ctx(), days=10)
    assert later.collapse_progress == 0.0


def test_last_stand_nation_fights_until_fully_occupied():
    world = build_world(bor_spirit=patriotic_spirit())
    country = world.country("BOR")
    everything_but_one = [pid for pid in range(1, 10)]
    occupy(world, everything_but_one, by="ARD")

    holdout = run_days(world, "BOR", ctx(existential=True), days=30)
    assert holdout.last_stand and not holdout.capitulates

    occupy(world, [10], by="ARD")
    final = country.evaluate_capitulation(world, ctx(existential=True))
    assert final.fully_occupied and final.capitulates


def test_capital_loss_hurts_less_once_the_government_has_evacuated():
    world = build_world()
    country = world.country("BOR")
    occupy(world, [BOR_CAPITAL], by="ARD")

    _, trapped = country.capitulation_pressure(world, ctx())
    country.government_seat_id = 10
    _, evacuated = country.capitulation_pressure(world, ctx())

    assert evacuated["capital"] < trapped["capital"]


def test_threshold_erodes_as_war_support_collapses():
    country = build_world().country("BOR")
    before, _ = country.capitulation_threshold(ctx())
    country.spirit.war_support = 0.05
    after, _ = country.capitulation_threshold(ctx())
    assert after < before


def test_regime_type_changes_what_holds_a_nation_together():
    democracy = NationalSpirit(0.5, stability=0.9, war_support=0.1, regime=RegimeType.LIBERAL_DEMOCRACY)
    autocracy = NationalSpirit(0.5, stability=0.9, war_support=0.1, regime=RegimeType.TOTALITARIAN)
    # Unpopular war, rock-solid regime: the autocracy keeps fighting, the democracy wavers.
    assert autocracy.resolve(0) > democracy.resolve(0)
