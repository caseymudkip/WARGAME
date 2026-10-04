from __future__ import annotations

import pytest

from conftest import BOR_CAPITAL, build_world, occupy, supplies

from wargame.core.enums import EscalationTier, SupplyType
from wargame.core.escalation import EscalationPolicy
from wargame.nation.country import EVACUATION_DAYS, EVACUATION_LOSS, DailyContext
from wargame.nation.logistics import EFFECTIVENESS_FLOOR, LogisticsStockpile


def test_well_supplied_army_fights_at_full_strength():
    stock = supplies()
    stock.tick_day(production_factor=1.0, import_factor=1.0, tempo=1.0)
    assert stock.supply_ratio == pytest.approx(1.0)
    assert stock.combat_effectiveness() == pytest.approx(1.0)


def test_running_out_of_ammunition_is_catastrophic_not_linear():
    stock = LogisticsStockpile(
        stocks={s: 1_000.0 for s in SupplyType} | {SupplyType.AMMUNITION: 0.0},
        base_daily_consumption={s: 100.0 for s in SupplyType},
    )
    stock.tick_day(production_factor=0.0, import_factor=0.0, tempo=1.0)
    assert stock.last_fulfillment[SupplyType.AMMUNITION] == 0.0
    assert stock.supply_ratio == pytest.approx(0.60)                    # A linear model would say 60%...
    assert stock.combat_effectiveness() == pytest.approx(EFFECTIVENESS_FLOOR)  # ...but the guns are silent.


def test_stockpile_drains_then_effectiveness_plummets():
    stock = supplies(production_ratio=0.5, stock_days=5)
    effectiveness = []
    for _ in range(20):
        stock.tick_day(production_factor=1.0, import_factor=0.0, tempo=1.0)
        effectiveness.append(stock.combat_effectiveness())
    assert effectiveness[0] == pytest.approx(1.0)
    assert effectiveness[-1] < 0.5


def test_tier_one_vacuum_seals_off_imports():
    country = build_world().country("BOR")
    assert country.import_factor(EscalationPolicy.for_tier(EscalationTier.VACUUM)) == 0.0
    assert country.import_factor(EscalationPolicy.for_tier(EscalationTier.PROXY_WAR)) == 1.0


def test_naval_blockade_cuts_only_seaborne_imports():
    country = build_world().country("BOR")
    country.seaborne_import_share = 0.6
    country.blockade_interdiction = 1.0
    assert country.import_factor(None) == pytest.approx(0.4)


def test_capturing_industry_halts_its_production():
    world = build_world()
    bor = world.country("BOR")
    bor.mark_prewar_baseline(world)
    occupy(world, [2], by="ARD")  # 4 of 12 industrial units.
    assert bor.production_factor(world) == pytest.approx(8 / 12)


def test_capturing_an_airfield_cuts_enemy_sorties():
    world = build_world()
    bor = world.country("BOR")
    before = bor.air_sortie_capacity(world)
    occupy(world, [3], by="ARD")
    assert before > 0
    assert bor.air_sortie_capacity(world) == 0.0


def test_industry_evacuates_inward_at_a_cost_when_there_is_depth():
    world = build_world()
    bor = world.country("BOR")
    bor.mark_prewar_baseline(world)
    occupy(world, [1], by="ARD")  # Front now touches the industrial province 2.
    ctx = DailyContext(now_hour=24, hostile_tags=frozenset({"ARD"}),
                       policy=EscalationPolicy.for_tier(EscalationTier.VACUUM), existential_threat=True)

    bor.on_daily_tick(world, ctx)

    assert world.provinces[2].industrial_output == 0.0
    [order] = bor.relocations
    assert order.output_in_transit == pytest.approx(4.0 * (1 - EVACUATION_LOSS))
    destination = world.provinces[order.to_province]
    before = destination.industrial_output

    arrival = DailyContext(now_hour=24 + EVACUATION_DAYS * 24, hostile_tags=frozenset({"ARD"}),
                           policy=ctx.policy, existential_threat=True)
    bor.on_daily_tick(world, arrival)
    assert destination.industrial_output == pytest.approx(before + order.output_in_transit)
    assert not bor.relocations


def test_no_evacuation_without_strategic_depth():
    world = build_world()
    bor = world.country("BOR")
    occupy(world, [1, 3, 5, 7, 9], by="ARD")  # Front everywhere: nowhere is 4+ hops from the enemy.
    ctx = DailyContext(now_hour=24, hostile_tags=frozenset({"ARD"}), existential_threat=True)
    bor.on_daily_tick(world, ctx)
    assert not bor.relocations


def test_government_relocates_when_the_capital_falls():
    world = build_world()
    bor = world.country("BOR")
    occupy(world, [BOR_CAPITAL], by="ARD")
    bor.on_daily_tick(world, DailyContext(now_hour=24, hostile_tags=frozenset({"ARD"})))
    assert bor.government_seat_id != BOR_CAPITAL
    assert world.provinces[bor.government_seat_id].controller == "BOR"
