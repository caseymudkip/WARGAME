"""Ready-made scenarios on the real map, and a builder for custom ones.

The flashpoints are real-world crises at one of the two start dates. They double as the engine's smoke test
(tools/scenarios/sweep.py runs each for a year) and as the spectator app's presets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from wargame.conflict.war_goal import PROVINCE_GOALS, WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.data.world_map import build_real_world
from wargame.simulation import ScenarioConfig, Simulation
from wargame.world.world import World

START_DATES = {2021: datetime(2022, 2, 24), 2026: datetime(2026, 1, 1)}  # The 2021 data set opens on invasion day.
AUTO_CLAIM = {WarGoalType.BORDER_SKIRMISH: 3, WarGoalType.TERRITORIAL_CONQUEST: 6}


@dataclass(frozen=True)
class Flashpoint:
    key: str
    name: str
    year: int
    attacker: str
    defender: str
    goal: WarGoalType
    tier: EscalationTier
    provinces: Callable[[World], frozenset[int]] = field(default=lambda w: frozenset())
    nuclear: bool = False
    motivation: Motivation = Motivation.CAUTIOUS
    blurb: str = ""


def named(*names: str, owner: str) -> Callable[[World], frozenset[int]]:
    return lambda w: frozenset(p.id for p in w.owned_by(owner) if p.name in names)


def everything(owner: str) -> Callable[[World], frozenset[int]]:
    return lambda w: frozenset(p.id for p in w.owned_by(owner))


def _ukraine_demands(w: World) -> frozenset[int]:
    return frozenset(p.id for p in w.owned_by("UKR") if p.name.startswith(("Donetsk", "Luhansk", "Zaporizhzhia", "Kherson")))


FLASHPOINTS = [
    Flashpoint("ukraine-2022", "Russia invades Ukraine (2022)", 2021, "RUS", "UKR", WarGoalType.REGIME_CHANGE,
               EscalationTier.PROXY_WAR, _ukraine_demands, motivation=Motivation.AGGRESSIVE,
               blurb="24 February 2022 from the 2021 map: regime change plus the four oblasts Russia later "
                     "declared annexed. The West arms Ukraine."),
    Flashpoint("ukraine-2026", "Russia-Ukraine war goes on (2026)", 2026, "RUS", "UKR", WarGoalType.REGIME_CHANGE,
               EscalationTier.PROXY_WAR, _ukraine_demands, motivation=Motivation.AGGRESSIVE,
               blurb="The real 1 January 2026 front: fortified, drone-saturated, Ukraine dependent on Western aid."),
    Flashpoint("taiwan", "China invades Taiwan", 2026, "CHN", "TWN", WarGoalType.REGIME_CHANGE,
               EscalationTier.PROXY_WAR, motivation=Motivation.AGGRESSIVE,
               blurb="A cross-strait invasion with the US staying out of the fighting."),
    Flashpoint("taiwan-us", "China invades Taiwan, US fights", 2026, "CHN", "TWN", WarGoalType.REGIME_CHANGE,
               EscalationTier.UNRESTRICTED, nuclear=True, motivation=Motivation.AGGRESSIVE,
               blurb="Unrestricted war: alliances trigger and the US Navy contests the strait."),
    Flashpoint("korea", "North Korea invades the South", 2026, "PRK", "KOR", WarGoalType.REGIME_CHANGE,
               EscalationTier.UNRESTRICTED, nuclear=True, motivation=Motivation.AGGRESSIVE,
               blurb="Across the most fortified border on earth, with the US treaty ally in the war."),
    Flashpoint("estonia", "Russia seizes Estonia (NATO)", 2026, "RUS", "EST", WarGoalType.TERRITORIAL_CONQUEST,
               EscalationTier.UNRESTRICTED, everything("EST"), nuclear=True,
               blurb="A land grab against a NATO member: Article 5 brings in the Alliance."),
    Flashpoint("kashmir", "India-Pakistan Kashmir clash", 2026, "IND", "PAK", WarGoalType.BORDER_SKIRMISH,
               EscalationTier.VACUUM, named("Azad Kashmir", owner="PAK"), nuclear=True,
               blurb="A limited war between nuclear powers over Azad Kashmir."),
    Flashpoint("syunik", "Azerbaijan takes Syunik", 2026, "AZE", "ARM", WarGoalType.TERRITORIAL_CONQUEST,
               EscalationTier.PROXY_WAR, named("Syunik", owner="ARM"),
               blurb="A corridor to Nakhchivan through Armenia's mountainous south."),
    Flashpoint("eritrea", "Ethiopia seeks the sea (Eritrea)", 2026, "ETH", "ERI", WarGoalType.TERRITORIAL_CONQUEST,
               EscalationTier.VACUUM, named("Southern Red Sea", owner="ERI"),
               blurb="Landlocked Ethiopia reaches for the port of Assab."),
    Flashpoint("venezuela", "US regime change in Venezuela", 2026, "USA", "VEN", WarGoalType.REGIME_CHANGE,
               EscalationTier.PROXY_WAR,
               blurb="Power projection across the Caribbean: carriers, landings, a government that may fight on."),
    Flashpoint("iran", "Israel coerces Iran", 2026, "ISR", "IRN", WarGoalType.COERCION, EscalationTier.PROXY_WAR,
               blurb="An air campaign with no ground war: strikes until Tehran concedes."),
    Flashpoint("lac", "China-India border war", 2026, "CHN", "IND", WarGoalType.BORDER_SKIRMISH,
               EscalationTier.VACUUM, named("Arunachal Pradesh", owner="IND"), nuclear=True,
               blurb="A Himalayan border war over Arunachal Pradesh."),
]
PRESETS = {fp.key: fp for fp in FLASHPOINTS}


def from_preset(fp: Flashpoint, seed: int = 0) -> Simulation:
    world = build_real_world(fp.year).world
    goal = WarGoal(fp.goal, fp.attacker, fp.defender, fp.provinces(world))
    return Simulation(world, ScenarioConfig(fp.name, START_DATES[fp.year], goal, fp.tier, nuclear_weapons_enabled=fp.nuclear,
                                            attacker_motivation=fp.motivation, seed=seed))


def border_claims(world: World, attacker: str, defender: str, count: int) -> frozenset[int]:
    """The defender's most valuable provinces on the attacker's border (or coast, if they share none)."""
    border = [p for p in world.owned_by(defender)
              if any(world.provinces[n].owner == attacker for n in p.neighbors)]
    pool = border or [p for p in world.owned_by(defender) if p.coastal] or list(world.owned_by(defender))
    return frozenset(p.id for p in sorted(pool, key=lambda p: (-p.strategic_value(), p.id))[:count])


def custom(year: int, attacker: str, defender: str, goal: WarGoalType, tier: EscalationTier, *, nuclear: bool = False,
           attacker_motivation: Motivation = Motivation.CAUTIOUS, defender_motivation: Motivation = Motivation.CAUTIOUS,
           provinces: frozenset[int] | None = None, seed: int = 0) -> Simulation:
    """A scenario the player sets up. Province goals without a list claim the most valuable border provinces."""
    world = build_real_world(year).world
    for tag in (attacker, defender):
        if tag not in world.countries:
            raise ValueError(f"unknown country {tag}")
    if attacker == defender:
        raise ValueError("a country cannot go to war with itself")
    claimed = frozenset(pid for pid in provinces or () if pid in world.provinces and world.provinces[pid].owner == defender)
    if goal in PROVINCE_GOALS and not claimed:
        claimed = border_claims(world, attacker, defender, AUTO_CLAIM.get(goal, 3))
    name = f"{world.country(attacker).name} vs {world.country(defender).name}"
    return Simulation(world, ScenarioConfig(name, START_DATES[year], WarGoal(goal, attacker, defender, claimed), tier,
                                            nuclear_weapons_enabled=nuclear, attacker_motivation=attacker_motivation,
                                            defender_motivation=defender_motivation, seed=seed))
