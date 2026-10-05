"""The simulation driver.

The spectator contract is enforced by the API surface: a ScenarioConfig is
frozen at construction, and the only runtime control is `set_speed`. Nothing
a viewer does can change the outcome.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from wargame.conflict.air_war import AirWar
from wargame.conflict.land_warfare import LandWarfare
from wargame.conflict.war import War, WarEvent
from wargame.conflict.war_goal import WarGoal
from wargame.core.clock import Cadence, SimClock, SpeedController, TimeScale
from wargame.core.enums import EscalationTier, Motivation
from wargame.core.escalation import EscalationPolicy
from wargame.nation.country import DailyContext
from wargame.world.world import World

System = Callable[["Simulation"], None]


@dataclass(frozen=True)
class ScenarioConfig:
    """Everything the player decides. Locked once the simulation starts."""

    name: str
    start: datetime
    war_goal: WarGoal
    escalation_tier: EscalationTier
    nuclear_weapons_enabled: bool = False
    attacker_motivation: Motivation = Motivation.CAUTIOUS
    defender_motivation: Motivation = Motivation.CAUTIOUS
    seed: int = 0
    land_combat: bool = True  # Land and air combat. False for scripted scenarios that move fronts themselves.


class Simulation:
    def __init__(self, world: World, scenario: ScenarioConfig) -> None:
        self.world = world
        self.scenario = scenario
        self.clock = SimClock(scenario.start)
        self.speed = SpeedController()
        self.rng = random.Random(scenario.seed)
        self.wars: list[War] = [
            War.declare(
                world,
                scenario.war_goal,
                scenario.escalation_tier,
                nuclear_weapons_enabled=scenario.nuclear_weapons_enabled,
                attacker_motivation=scenario.attacker_motivation,
                defender_motivation=scenario.defender_motivation,
                now_hour=0,
            )
        ]
        self._systems: dict[Cadence, list[System]] = {
            Cadence.HOURLY: [],
            Cadence.DAILY: [Simulation._countries_daily, Simulation._wars_daily],
            Cadence.WEEKLY: [Simulation._diplomacy_weekly],
        }
        self.land = LandWarfare()
        self.air = AirWar()
        if scenario.land_combat:
            self._systems[Cadence.HOURLY].append(lambda sim: sim.land.hourly(sim))
            self._systems[Cadence.DAILY].append(lambda sim: sim.air.daily(sim))
            self._systems[Cadence.DAILY].append(lambda sim: sim.land.daily(sim))
            self.air.daily(self)
            self.land.daily(self)  # Forces start deployed.

    # --- spectator controls -------------------------------------------------

    def set_speed(self, scale: TimeScale) -> None:
        self.speed.scale = scale

    def advance_frame(self, real_seconds: float) -> int:
        """Called by the renderer each frame. Returns ticks simulated."""
        ticks = self.speed.ticks_for_frame(real_seconds)
        for _ in range(ticks):
            self.step()
        return ticks

    # --- engine ---------------------------------------------------------------

    def register_system(self, cadence: Cadence, system: System) -> None:
        self._systems[cadence].append(system)

    def step(self) -> None:
        for cadence in self.clock.advance():
            for system in self._systems[cadence]:
                system(self)

    def run_hours(self, hours: int) -> None:
        for _ in range(hours):
            self.step()

    def run_days(self, days: int) -> None:
        self.run_hours(days * 24)

    @property
    def active_wars(self) -> list[War]:
        return [w for w in self.wars if not w.ended]

    @property
    def finished(self) -> bool:
        return not self.active_wars

    def events(self) -> Iterator[WarEvent]:
        for war in self.wars:
            yield from war.events

    def daily_context_for(self, tag: str) -> DailyContext:
        hostile: set[str] = set()
        policy: EscalationPolicy | None = None
        existential = False
        for war in self.active_wars:
            if tag in war.participants:
                hostile |= war.enemies_of(tag)
                policy = war.policy
                existential = existential or war.is_existential_for(tag)
        return DailyContext(self.clock.hours_elapsed, frozenset(hostile), policy, existential)

    # --- built-in systems -------------------------------------------------------

    def _countries_daily(self) -> None:
        for tag, country in self.world.countries.items():
            if next(self.world.owned_by(tag), None) is None:
                continue  # Annexed.
            country.on_daily_tick(self.world, self.daily_context_for(tag))

    def _wars_daily(self) -> None:
        for war in self.active_wars:
            war.on_daily_tick(self.world, self.clock.hours_elapsed, self.rng)

    def _diplomacy_weekly(self) -> None:
        for war in self.active_wars:
            war.review_external_support(self.world, self.clock.hours_elapsed)
