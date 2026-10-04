"""Shared fixtures: a small fictional map.

    ARD (attacker)        BOR (defender)
    15-14-13-12-11  ---  1-2-3-4-5-6-7-8-9-10
                                    |
                                    20 DRV (rival neighbour)   30 CAL (friend, not adjacent)

All nations are fictional; numbers are illustrative, not real-world data.
"""

from __future__ import annotations

import pytest

from wargame.core.enums import ProvinceTag as T
from wargame.core.enums import RegimeType, SupplyType
from wargame.nation.country import Country
from wargame.nation.logistics import LogisticsStockpile
from wargame.nation.military import OrderOfBattle
from wargame.nation.national_spirit import NationalSpirit
from wargame.world.province import Province
from wargame.world.world import World

BOR_CAPITAL = 7
ARD_CAPITAL = 13


def supplies(scale: float = 1.0, production_ratio: float = 0.9, stock_days: float = 30.0) -> LogisticsStockpile:
    consumption = {
        SupplyType.FUEL: 100.0 * scale,
        SupplyType.AMMUNITION: 100.0 * scale,
        SupplyType.RATIONS: 50.0 * scale,
        SupplyType.SPARE_PARTS: 20.0 * scale,
    }
    return LogisticsStockpile(
        stocks={s: v * stock_days for s, v in consumption.items()},
        base_daily_consumption=dict(consumption),
        base_daily_production={s: v * production_ratio for s, v in consumption.items()},
        base_daily_imports={s: v * 0.1 for s, v in consumption.items()},
    )


def unstable_spirit() -> NationalSpirit:
    return NationalSpirit(patriotism=0.2, stability=0.25, war_support=0.3, regime=RegimeType.HYBRID)


def patriotic_spirit() -> NationalSpirit:
    return NationalSpirit(patriotism=0.95, stability=0.8, war_support=0.85, regime=RegimeType.LIBERAL_DEMOCRACY)


def steady_spirit() -> NationalSpirit:
    return NationalSpirit(patriotism=0.6, stability=0.6, war_support=0.6, regime=RegimeType.FLAWED_DEMOCRACY)


def _chain(ids: list[int]) -> dict[int, list[int]]:
    links: dict[int, list[int]] = {i: [] for i in ids}
    for a, b in zip(ids, ids[1:]):
        links[a].append(b)
        links[b].append(a)
    return links


def build_world(bor_spirit: NationalSpirit | None = None, ard_spirit: NationalSpirit | None = None) -> World:
    bor_ids = list(range(1, 11))
    ard_ids = [15, 14, 13, 12, 11]
    links = _chain(bor_ids)
    links.update(_chain(ard_ids))
    links[11].append(1)
    links[1].append(11)
    links[20] = [6]
    links[6].append(20)
    links[30] = []

    def prov(pid: int, owner: str, pop: int = 50_000, industry: float = 0.0, tags: tuple[T, ...] = (T.FARMLAND,),
             infra: float = 0.5) -> Province:
        return Province(id=pid, name=f"{owner}-{pid}", owner=owner, controller=owner, population=pop,
                        industrial_output=industry, tags=frozenset(tags), infrastructure=infra,
                        neighbors=tuple(links[pid]))

    world = World()
    world.add_provinces([
        prov(1, "BOR"),
        prov(2, "BOR", pop=300_000, industry=4.0, tags=(T.INDUSTRIAL,)),
        prov(3, "BOR", pop=80_000, tags=(T.AIRFIELD,), infra=0.8),
        prov(4, "BOR"),
        prov(5, "BOR", pop=1_000_000, industry=2.0, tags=(T.URBAN_CENTER,)),
        prov(6, "BOR"),
        prov(BOR_CAPITAL, "BOR", pop=3_000_000, industry=3.0, tags=(T.CAPITAL, T.URBAN_CENTER), infra=0.9),
        prov(8, "BOR"),
        prov(9, "BOR", pop=200_000, industry=3.0, tags=(T.INDUSTRIAL,), infra=0.7),
        prov(10, "BOR", pop=60_000, tags=(T.RAIL_HUB, T.FARMLAND)),
        prov(11, "ARD"),
        prov(12, "ARD", pop=90_000, tags=(T.AIRFIELD,)),
        prov(ARD_CAPITAL, "ARD", pop=4_000_000, industry=5.0, tags=(T.CAPITAL, T.URBAN_CENTER), infra=0.9),
        prov(14, "ARD", pop=400_000, industry=4.0, tags=(T.INDUSTRIAL,)),
        prov(15, "ARD"),
        prov(20, "DRV", pop=500_000, industry=1.0, tags=(T.URBAN_CENTER,)),
        prov(30, "CAL", pop=2_000_000, industry=6.0, tags=(T.CAPITAL,)),
    ])

    def oob(manpower: int) -> OrderOfBattle:
        return OrderOfBattle(active_personnel=manpower // 10, reserve_personnel=manpower // 10,
                             mobilizable_manpower=manpower, sorties_per_airfield=40.0)

    world.add_country(Country("BOR", "Borovia", BOR_CAPITAL, bor_spirit or steady_spirit(),
                              oob=oob(1_000_000), logistics=supplies()))
    world.add_country(Country("ARD", "Ardania", ARD_CAPITAL, ard_spirit or steady_spirit(),
                              oob=oob(2_000_000), logistics=supplies(1.5)))
    world.add_country(Country("DRV", "Dravia", 20, steady_spirit(), oob=oob(300_000), logistics=supplies(0.3)))
    world.add_country(Country("CAL", "Caledon", 30, steady_spirit(), oob=oob(3_000_000), logistics=supplies(3.0)))
    return world


def occupy(world: World, pids: list[int], by: str) -> None:
    for pid in pids:
        world.set_controller(pid, by)


@pytest.fixture
def world() -> World:
    return build_world()
