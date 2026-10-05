"""Governments in exile: the government gives up, the nation does not.

Modelled on Free France (1940): the government in Paris signed an armistice,
but de Gaulle's Free French, rallying the unoccupied colonies, fought on.
Here, when a government capitulates to an existential war goal while morale
in its still-free territory remains high, a "Free <name>" state forms. It
takes ownership of the free provinces, the loyal share of the armed forces,
and the national deterrent, and stays in the war. The attacker then decides
whether to pursue it (see War._government_in_exile).

Only existential wars produce exiles: losing a border war is a treaty, not
the end of the state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wargame.core.mathutil import clamp
from wargame.nation.country import Country
from wargame.nation.logistics import LogisticsStockpile
from wargame.nation.military import EquipmentStock, OrderOfBattle
from wargame.nation.national_spirit import NationalSpirit
from wargame.nation.nuclear import NuclearPosture

if TYPE_CHECKING:
    from wargame.world.world import World

EXILE_MIN_MORALE = 0.55       # 0.7 x patriotism + 0.3 x war support.
EXILE_MIN_FREE_SHARE = 0.05   # Share of national value still free.
EXILE_STABILITY = 0.60        # Fresh legitimacy of a government that refused to surrender.
EXILE_BASE_LOYALTY = 0.25     # Share of the armed forces that follows the exile...
EXILE_LOYALTY_PER_MORALE = 0.5  # ...plus this much per point of morale.


def exile_morale(country: Country, now_hour: int) -> float:
    return clamp(0.7 * country.spirit.patriotism + 0.3 * country.spirit.effective_war_support(now_hour))


def free_share(world: World, country: Country) -> float:
    total = world.owned_value(country.tag)
    free = sum(p.strategic_value() for p in world.owned_by(country.tag) if p.controller == country.tag)
    return free / total if total > 0 else 0.0


def exile_eligible(world: World, country: Country, now_hour: int) -> bool:
    return (
        not country.is_exile
        and exile_morale(country, now_hour) >= EXILE_MIN_MORALE
        and free_share(world, country) >= EXILE_MIN_FREE_SHARE
    )


def _split_oob(oob: OrderOfBattle, share: float, manpower_share: float) -> OrderOfBattle:
    """Detach `share` of the forces into a new OOB; the original keeps the rest."""
    detached: dict[str, EquipmentStock] = {}
    for key, e in oob.equipment.items():
        moved = int(e.quantity * share)
        e.quantity -= moved
        detached[key] = EquipmentStock(e.name, e.branch, moved, e.quality, e.readiness, e.unit_cost, e.tonnage,
                                       e.combat_weight)
    active = int(oob.active_personnel * share)
    oob.active_personnel -= active
    return OrderOfBattle(
        equipment=detached,
        active_personnel=active,
        reserve_personnel=int(oob.reserve_personnel * manpower_share),
        mobilizable_manpower=int(oob.mobilizable_manpower * manpower_share),
        sorties_per_airfield=oob.sorties_per_airfield,
    )


def _split_logistics(log: LogisticsStockpile, stock_share: float, production_share: float,
                     consumption_share: float) -> LogisticsStockpile:
    moved = {s: v * stock_share for s, v in log.stocks.items()}
    for s, v in moved.items():
        log.stocks[s] -= v
    return LogisticsStockpile(
        stocks=moved,
        base_daily_consumption={s: v * consumption_share for s, v in log.base_daily_consumption.items()},
        base_daily_production={s: v * production_share for s, v in log.base_daily_production.items()},
        base_daily_imports={s: v * stock_share for s, v in log.base_daily_imports.items()},
    )


def form_government_in_exile(world: World, country: Country, now_hour: int) -> Country:
    """Create the Free state, hand it the free provinces, loyal forces and arsenal. Caller checks eligibility."""
    free = [p for p in world.owned_by(country.tag) if p.controller == country.tag]
    seat_candidates = [p for p in free if p.id == country.government_seat_id]
    seat = seat_candidates[0] if seat_candidates else max(free, key=lambda p: p.strategic_value())

    morale = exile_morale(country, now_hour)
    loyalty = clamp(EXILE_BASE_LOYALTY + EXILE_LOYALTY_PER_MORALE * morale)
    share = free_share(world, country)
    total_pop = sum(p.population for p in world.owned_by(country.tag))
    pop_share = sum(p.population for p in free) / total_pop if total_pop else share
    prewar = country.prewar_industrial_capacity or country.industrial_capacity(world)
    production_share = sum(p.effective_industry for p in free) / prewar if prewar > 0 else share

    spirit = NationalSpirit(
        patriotism=country.spirit.patriotism,
        stability=EXILE_STABILITY,
        war_support=max(morale, country.spirit.effective_war_support(now_hour)),
        regime=country.spirit.regime,
        war_exhaustion=country.spirit.war_exhaustion * 0.5,
    )
    exile = Country(
        tag=f"FREE_{country.tag}",
        name=f"Free {country.name}",
        capital_province_id=seat.id,
        spirit=spirit,
        oob=_split_oob(country.oob, loyalty, pop_share),
        logistics=_split_logistics(country.logistics, share, production_share, loyalty),
        nuclear=country.nuclear,  # The deterrent stays with the government that is still fighting.
        alignment=country.alignment,
        relations=dict(country.relations),
        defensive_pacts=set(country.defensive_pacts),
        trade_dependence=country.trade_dependence,
        seaborne_import_share=country.seaborne_import_share,
        blockade_interdiction=country.blockade_interdiction,
        is_exile=True,
        exile_of=country.tag,
    )
    country.nuclear = NuclearPosture()

    # The world treats the Free state as the legitimate continuation of the old one.
    for other in world.countries.values():
        if country.tag in other.relations:
            other.relations[exile.tag] = other.relations[country.tag]
        if country.tag in other.defensive_pacts:
            other.defensive_pacts.add(exile.tag)

    world.add_country(exile)
    for p in free:
        world.transfer_ownership(p.id, exile.tag)
    exile.mark_prewar_baseline(world)
    return exile
