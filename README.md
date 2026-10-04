# WARGAME

An autonomous, modern-era (2021 / 2026 start) grand strategy war simulator.
You set up the scenario: belligerents, war goal, escalation tier, nuclear
toggle and motivation. Then the engine runs the war on its own while you watch
at the speed you choose.

This repository currently contains the **simulation engine foundation**: data
model and daily logic loops in pure Python (3.11+, no runtime dependencies).
See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design, the GDD
pillar map and open design questions.

## Layout

```
src/wargame/
  core/        enums, clock (fixed 1h tick), modifiers, escalation tier rule table
  world/       Province (terrain, infrastructure, strategic tags), World registry
  nation/      Country, NationalSpirit, LogisticsStockpile, OrderOfBattle, NuclearPosture/BMD
  conflict/    War, WarGoal, PeaceTreaty
  simulation.py  ScenarioConfig + Simulation tick loop
tests/         behaviour tests on a small fictional map
```

## Usage sketch

```python
from datetime import datetime
from wargame.conflict import WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.simulation import ScenarioConfig, Simulation

scenario = ScenarioConfig(
    name="Border crisis",
    start=datetime(2026, 1, 1),
    war_goal=WarGoal(WarGoalType.BORDER_SKIRMISH, holder="ARD", target="BOR", province_ids=frozenset({1, 2})),
    escalation_tier=EscalationTier.PROXY_WAR,
    nuclear_weapons_enabled=False,
    attacker_motivation=Motivation.CAUTIOUS,
)
sim = Simulation(world, scenario)   # `world` built by a scenario loader (next task)
while not sim.finished:
    sim.run_days(1)
for event in sim.events():
    print(event.hour // 24, event.message)
```

## Development

```
pip install -e ".[dev]"
pytest
```
