"""Calibrate land combat against the real 2025 war in Ukraine.

    python tools/calibration/ukraine_2025.py [days]

Runs Russia attacking Ukraine from the real 1 January 2026 front (Tier 2, aggressive, regime
change plus the four oblasts Russia claims) and reports ground taken and casualty rates next
to the 2025 benchmarks:
  ~11.9 km2/day   DeepState: 4,336 km2 taken in 2025
  ~1,137/day      UK MoD / CSIS: ~415,000 Russian casualties in 2025
  ~0.42-0.5       Ukrainian / Russian casualties, CSIS (Jan 2026): 500-600k vs ~1.2M since 2022
"""

from __future__ import annotations

import sys
from datetime import datetime

from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.data.world_map import build_real_world
from wargame.simulation import ScenarioConfig, Simulation

BENCHMARK_KM2_PER_DAY = 4_336 / 365
BENCHMARK_RU_CASUALTIES_PER_DAY = 415_000 / 365
# Russia's stated demand on top of regime change: the four oblasts it declared annexed in September 2022.
ANNEXED_OBLASTS = ("Donetsk", "Luhansk", "Zaporizhzhia", "Kherson")


def run(days: int = 180, seed: int = 1) -> dict[str, float]:
    real = build_real_world(2026)
    world = real.world
    demands = frozenset(p.id for p in world.owned_by("UKR") if p.name.startswith(ANNEXED_OBLASTS))
    sim = Simulation(world, ScenarioConfig("calibration", datetime(2026, 1, 1),
                                           WarGoal(WarGoalType.REGIME_CHANGE, "RUS", "UKR", demands), EscalationTier.PROXY_WAR,
                                           nuclear_weapons_enabled=False, attacker_motivation=Motivation.AGGRESSIVE, seed=seed))
    start_held = {p.id for p in world.provinces.values() if p.owner == "UKR" and p.controller == "RUS"}
    ru0, ua0 = world.country("RUS").oob.casualties_total, world.country("UKR").oob.casualties_total
    sim.run_days(days)
    war = sim.wars[0]
    taken = sum(p.area_km2 for p in world.provinces.values()
                if p.owner == "UKR" and p.controller == "RUS" and p.id not in start_held)
    taken += sum(world.provinces[pid].area_km2 * prog for pid, (tag, prog) in world.contested.items() if tag == "RUS")
    elapsed = (sim.clock.hours_elapsed // 24) or 1
    return {
        "days": elapsed,
        "km2_per_day": taken / elapsed,
        "ru_casualties_per_day": (world.country("RUS").oob.casualties_total - ru0) / elapsed,
        "ua_casualties_per_day": (world.country("UKR").oob.casualties_total - ua0) / elapsed,
        "war_ended": float(war.ended),
        "ru_resolve": war.participants["RUS"].resolve if "RUS" in war.participants else 0.0,
        "ua_pressure": war.assessments["UKR"].pressure if "UKR" in war.assessments else 0.0,
        "ua_threshold": war.assessments["UKR"].threshold if "UKR" in war.assessments else 0.0,
    }


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    result = run(days)
    print(f"after {result['days']} days:")
    print(f"  ground taken      {result['km2_per_day']:7.1f} km2/day   (2025: {BENCHMARK_KM2_PER_DAY:.1f})")
    print(f"  RU casualties     {result['ru_casualties_per_day']:7.0f} /day     (2025: {BENCHMARK_RU_CASUALTIES_PER_DAY:.0f})")
    print(f"  UA casualties     {result['ua_casualties_per_day']:7.0f} /day     "
          f"(UA/RU {result['ua_casualties_per_day'] / max(result['ru_casualties_per_day'], 1):.2f}; CSIS: 0.42-0.5)")
    print(f"  RU resolve {result['ru_resolve']:.2f}; UA pressure {result['ua_pressure']:.2f} of {result['ua_threshold']:.2f}; ended={bool(result['war_ended'])}")
