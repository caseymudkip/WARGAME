"""Calibrate land combat against the real war in Ukraine, at both of its paces.

    python tools/calibration/ukraine_2025.py [days]        # the 2025 grind (default 365 days)
    python tools/calibration/ukraine_2025.py 2022 [days]   # the February 2022 invasion (default 36 days)

The same engine must produce both, from the real start dates and data:

  2022, from the 2021 map: Russia attacks an unmobilised Ukraine across unfortified borders (except
  the Donbas line), from Russia, Crimea and Belarus. It held ~163,000-167,000 km2 of Ukraine on
  31 March 2022 (ISW; Statista), up from ~43,000, and did not take Kyiv.

  2025, from the 1 January 2026 front: a fortified, drone-saturated front, both sides mobilised.
    ~11.9 km2/day   DeepState: 4,336 km2 taken in 2025
    ~1,137/day      UK Defence Intelligence: ~415,000 Russian casualties in 2025
    ~0.42-0.5       Ukrainian / Russian casualties, CSIS (Jan 2026): 500-600k vs ~1.2M since 2022
"""

from __future__ import annotations

import sys
from datetime import datetime

from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.data.world_map import build_real_world
from wargame.simulation import ScenarioConfig, Simulation
from wargame.world.world import World

BENCHMARK_KM2_PER_DAY = 4_336 / 365
BENCHMARK_RU_CASUALTIES_PER_DAY = 415_000 / 365
BENCHMARK_OCCUPIED_31_MARCH_2022 = (163_000, 167_223)  # ISW; Statista
# Russia's territorial aims on top of regime change: the Donetsk and Luhansk "republics" it recognised
# on 21 February 2022, and the land bridge to Crimea through Zaporizhzhia and Kherson (with the North
# Crimean Canal), seized in the first days (RUSI, "Preliminary Lessons", November 2022). All four were
# declared annexed in September 2022 and remained Russia's demands through 2025.
DEMANDS = ("Donetsk", "Luhansk", "Zaporizhzhia", "Kherson")


def occupied_km2(world: World) -> float:
    """Ukrainian territory Russia holds, counting the taken share of provinces still being fought over."""
    held = sum(p.area_km2 for p in world.provinces.values() if p.owner == "UKR" and p.controller == "RUS")
    return held + sum(world.provinces[pid].area_km2 * progress
                      for pid, (tag, progress) in world.contested.items() if tag == "RUS")


def start(year: int, seed: int = 1, tier: EscalationTier = EscalationTier.PROXY_WAR) -> Simulation:
    world = build_real_world(year).world
    demands = frozenset(p.id for p in world.owned_by("UKR") if p.name.startswith(DEMANDS))
    return Simulation(world, ScenarioConfig(f"calibration {year}", datetime(year, 1, 1) if year == 2026 else datetime(2022, 2, 24),
                                            WarGoal(WarGoalType.REGIME_CHANGE, "RUS", "UKR", demands), tier,
                                            nuclear_weapons_enabled=False, attacker_motivation=Motivation.AGGRESSIVE, seed=seed))


def run(days: int = 365, seed: int = 1) -> dict[str, float]:
    """The 2025 grind, from the 1 January 2026 front."""
    sim = start(2026, seed)
    world = sim.world
    before = occupied_km2(world)
    ru0, ua0 = world.country("RUS").oob.casualties_total, world.country("UKR").oob.casualties_total
    sim.run_days(days)
    war = sim.wars[0]
    elapsed = (sim.clock.hours_elapsed // 24) or 1
    return {
        "days": elapsed,
        "km2_per_day": (occupied_km2(world) - before) / elapsed,
        "ru_casualties_per_day": (world.country("RUS").oob.casualties_total - ru0) / elapsed,
        "ua_casualties_per_day": (world.country("UKR").oob.casualties_total - ua0) / elapsed,
        "war_ended": float(war.ended),
        "ru_resolve": war.participants["RUS"].resolve if "RUS" in war.participants else 0.0,
        "ua_pressure": war.assessments["UKR"].pressure if "UKR" in war.assessments else 0.0,
        "ua_threshold": war.assessments["UKR"].threshold if "UKR" in war.assessments else 0.0,
    }


def run_2022(days: int = 36, seed: int = 1) -> dict[str, float]:
    """The invasion of 24 February 2022, from the 2021 map."""
    sim = start(2021, seed)
    world = sim.world
    before = occupied_km2(world)
    sim.run_days(days)
    kyiv = next(p for p in world.provinces.values() if p.name == "Kyiv (Municipality)")
    return {
        "days": days,
        "occupied_before": before,
        "occupied_km2": occupied_km2(world),
        "kyiv_held": float(kyiv.controller == "UKR"),
        "war_ended": float(sim.wars[0].ended),
        "ua_active": float(world.country("UKR").oob.active_personnel),
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "2022":
        r = run_2022(int(sys.argv[2]) if len(sys.argv) > 2 else 36)
        lo, hi = BENCHMARK_OCCUPIED_31_MARCH_2022
        print(f"after {r['days']} days from 24 February 2022:")
        print(f"  Russian-held Ukraine  {r['occupied_before']:9,.0f} -> {r['occupied_km2']:9,.0f} km2   (31 March 2022: {lo:,}-{hi:,})")
        print(f"  Kyiv held: {bool(r['kyiv_held'])}; Ukrainian active forces {r['ua_active']:,.0f}; war ended: {bool(r['war_ended'])}")
    else:
        result = run(int(sys.argv[1]) if len(sys.argv) > 1 else 365)
        print(f"after {result['days']} days from 1 January 2026:")
        print(f"  ground taken      {result['km2_per_day']:7.1f} km2/day   (2025: {BENCHMARK_KM2_PER_DAY:.1f})")
        print(f"  RU casualties     {result['ru_casualties_per_day']:7.0f} /day     (2025: {BENCHMARK_RU_CASUALTIES_PER_DAY:.0f})")
        print(f"  UA casualties     {result['ua_casualties_per_day']:7.0f} /day     "
              f"(UA/RU {result['ua_casualties_per_day'] / max(result['ru_casualties_per_day'], 1):.2f}; CSIS: 0.42-0.5)")
        print(f"  RU resolve {result['ru_resolve']:.2f}; UA pressure {result['ua_pressure']:.2f} of {result['ua_threshold']:.2f}; "
              f"ended={bool(result['war_ended'])}")
