# WARGAME

An autonomous, modern-era (2021 / 2026 start) grand strategy war simulator.
You set up the scenario: belligerents, war goal, escalation tier, nuclear
toggle and motivation. Then the engine runs the war on its own while you watch
at the speed you choose.

This repository currently contains the **simulation engine** (data model, daily
and hourly logic loops, land warfare, amphibious operations, an air war and a
strategy layer, in pure Python 3.11+ with no runtime dependencies), **real-world
data for 140–145 countries** at two start dates, every number with its source,
and a **3,604-province world map** with the real front lines. Combat is
calibrated against the war in Ukraine and NATO's 1999 air campaign, and
smoke-tested on eleven real-world flashpoints. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the
design and resolved GDD decisions, and [`data/README.md`](data/README.md) for
sources, cross-checks and known gaps.

## Layout

```
src/wargame/
  core/        enums, clock (fixed 1h tick), modifiers, escalation tier rule table
  world/       Province (terrain, infrastructure, strategic tags), World registry
  nation/      Country, NationalSpirit, LogisticsStockpile, OrderOfBattle, NuclearPosture/BMD, exile
  conflict/    War (escalation, exile pursuit, claims, armistice), WarGoal, PeaceTreaty,
               land warfare (fronts, assaults, encirclement, rivers, amphibious, blockade, attrition),
               strategy (posture, counteroffensives, withdrawals), air war (superiority, strikes)
  data/        snapshot loader, real data -> Country (python -m wargame.data RUS UKR), map -> World
  simulation.py  ScenarioConfig + Simulation tick loop
data/          sources, raw extracts, curated research, built 2021/2026 snapshots, province map
tools/data/    reproducible extraction and build scripts
tools/map/     reproducible province map build (Natural Earth, power plants, DeepState front)
tools/calibration/  land combat against the real 2025 war (python tools/calibration/ukraine_2025.py)
tools/scenarios/    eleven real-world flashpoints run for a year (python tools/scenarios/sweep.py)
tests/         behaviour tests (fictional map), real-map tests, dataset integrity tests
```

## Usage

```python
from datetime import datetime
from wargame.conflict import WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.data.world_map import build_real_world
from wargame.simulation import ScenarioConfig, Simulation

real = build_real_world(2026)          # 3,604 real provinces, 145 countries, 1 Jan 2026 front lines
claimed = frozenset(p.id for p in real.world.owned_by("UKR")
                    if p.name.startswith(("Donetsk", "Luhansk", "Zaporizhzhia", "Kherson")))
scenario = ScenarioConfig(
    name="Russia-Ukraine, proxy war",
    start=datetime(2026, 1, 1),
    # Regime change, plus territorial demands annexed at the peace table.
    war_goal=WarGoal(WarGoalType.REGIME_CHANGE, holder="RUS", target="UKR", province_ids=claimed),
    escalation_tier=EscalationTier.PROXY_WAR,
    nuclear_weapons_enabled=True,
    attacker_motivation=Motivation.AGGRESSIVE,
)
sim = Simulation(real.world, scenario)
sim.run_days(30)
for event in sim.events():
    print(event.hour // 24, event.message)   # declaration, world reaction, lend-lease coalitions...
for pid, (attacker, progress) in real.world.contested.items():
    print(real.world.provinces[pid].name, f"{progress:.1%} taken by {attacker}")
```

Fronts move by hourly combat on the real province graph, at whatever pace the conditions
allow. The same rules give an exploitation dash against an empty front, WW2 division pace
in an even fight, and trench war against a fortified, drone-watched line. They are checked
against both phases of the war in Ukraine (`python tools/calibration/ukraine_2025.py [2022]`):

- **2025, from the 1 January 2026 front.** Russia takes 12.0 km² a day (DeepState: 11.9).
  It loses about 1,045 soldiers a day (UK MoD: 1,137), and Ukraine loses 0.54 as many
  (CSIS: 0.42–0.5). The attacks fall on the real axes.
- **2022, from the 2021 map.** Russia takes left-bank Kherson in days and nearly doubles its
  hold within five weeks (~78,000 km²; the real figure was ~165,000), but cannot take Kyiv.
  Four years on it holds ~125,000 km² (the real 2025 figure: ~116,000).
- **Foreign aid decides the long war.** With aid, Ukraine keeps fighting for years from
  either start. If the West walks away, it collapses within about a year.
- **Elsewhere.** An unaided Taiwan falls in about five months; with the US in, China cannot
  win the sea. A Kosovo-style air campaign forces concessions in ~80 days (78 in 1999).

Inspect a country's data with sources: `python -m wargame.data RUS UKR --year 2026`.

## Development

```
pip install -e ".[dev]"
pytest
```
