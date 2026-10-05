"""Run a set of real-world flashpoints and report how each war goes.

    python tools/scenarios/sweep.py [days]

Not a prediction: a smoke test of the whole engine on scenarios far from the one it was calibrated
on (Ukraine). It shows how wars end, how long they take, who joins and what changes hands, so
implausible behaviour stands out.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.data.world_map import build_real_world
from wargame.simulation import ScenarioConfig, Simulation
from wargame.world.world import World


@dataclass(frozen=True)
class Flashpoint:
    name: str
    year: int
    attacker: str
    defender: str
    goal: WarGoalType
    tier: EscalationTier
    provinces: Callable[[World], frozenset[int]] = lambda w: frozenset()
    nuclear: bool = False
    motivation: Motivation = Motivation.CAUTIOUS


def named(*names: str, owner: str) -> Callable[[World], frozenset[int]]:
    return lambda w: frozenset(p.id for p in w.owned_by(owner) if p.name in names)


def everything(owner: str) -> Callable[[World], frozenset[int]]:
    return lambda w: frozenset(p.id for p in w.owned_by(owner))


FLASHPOINTS = [
    Flashpoint("Russia invades Ukraine (2022)", 2021, "RUS", "UKR", WarGoalType.REGIME_CHANGE, EscalationTier.PROXY_WAR,
               lambda w: frozenset(p.id for p in w.owned_by("UKR") if p.name.startswith(("Donetsk", "Luhansk", "Zaporizhzhia", "Kherson"))),
               motivation=Motivation.AGGRESSIVE),
    Flashpoint("China invades Taiwan", 2026, "CHN", "TWN", WarGoalType.REGIME_CHANGE, EscalationTier.PROXY_WAR,
               motivation=Motivation.AGGRESSIVE),
    Flashpoint("China invades Taiwan, US fights", 2026, "CHN", "TWN", WarGoalType.REGIME_CHANGE, EscalationTier.UNRESTRICTED,
               nuclear=True, motivation=Motivation.AGGRESSIVE),
    Flashpoint("North Korea invades the South", 2026, "PRK", "KOR", WarGoalType.REGIME_CHANGE, EscalationTier.UNRESTRICTED,
               nuclear=True, motivation=Motivation.AGGRESSIVE),
    Flashpoint("Russia seizes Estonia (NATO)", 2026, "RUS", "EST", WarGoalType.TERRITORIAL_CONQUEST, EscalationTier.UNRESTRICTED,
               everything("EST"), nuclear=True),
    Flashpoint("India-Pakistan Kashmir clash", 2026, "IND", "PAK", WarGoalType.BORDER_SKIRMISH, EscalationTier.VACUUM,
               named("Azad Kashmir", owner="PAK"), nuclear=True),
    Flashpoint("Azerbaijan takes Syunik", 2026, "AZE", "ARM", WarGoalType.TERRITORIAL_CONQUEST, EscalationTier.PROXY_WAR,
               named("Syunik", owner="ARM")),
    Flashpoint("Ethiopia seeks the sea (Eritrea)", 2026, "ETH", "ERI", WarGoalType.TERRITORIAL_CONQUEST, EscalationTier.VACUUM,
               named("Southern Red Sea", owner="ERI")),
    Flashpoint("US regime change in Venezuela", 2026, "USA", "VEN", WarGoalType.REGIME_CHANGE, EscalationTier.PROXY_WAR),
    Flashpoint("Israel coerces Iran", 2026, "ISR", "IRN", WarGoalType.COERCION, EscalationTier.PROXY_WAR),
    Flashpoint("China-India border war", 2026, "CHN", "IND", WarGoalType.BORDER_SKIRMISH, EscalationTier.VACUUM,
               named("Arunachal Pradesh", owner="IND"), nuclear=True),
]


def run(fp: Flashpoint, days: int) -> str:
    world = build_real_world(fp.year).world
    goal = WarGoal(fp.goal, fp.attacker, fp.defender, fp.provinces(world))
    start = datetime(2022, 2, 24) if fp.year == 2021 else datetime(2026, 1, 1)
    sim = Simulation(world, ScenarioConfig(fp.name, start, goal, fp.tier, nuclear_weapons_enabled=fp.nuclear,
                                           attacker_motivation=fp.motivation))
    held_before = {p.id: p.controller for p in world.provinces.values()}
    t0 = time.perf_counter()
    for _ in range(days // 10):
        sim.run_days(10)
        if sim.finished:
            break
    took = time.perf_counter() - t0
    war = sim.wars[0]
    elapsed = sim.clock.hours_elapsed // 24
    moved = [p for p in world.provinces.values() if p.controller != held_before[p.id] or p.owner != world.provinces[p.id].owner]
    changed = {}
    for p in world.provinces.values():
        if p.controller != held_before[p.id]:
            changed.setdefault(f"{held_before[p.id]}->{p.controller}", 0.0)
            changed[f"{held_before[p.id]}->{p.controller}"] += p.area_km2
    sides = {side: sorted(war.tags_on(side)) for side in {p.side for p in war.participants.values()}}
    nukes = len(war.nuclear_strikes)
    casualties = {t: world.country(t).oob.casualties_total for t in sorted(war.participants) if t in world.countries}
    ending = war.treaty.reason if war.treaty else "still fighting"
    terms = ", ".join(sorted({t.type.value for t in war.treaty.terms})) if war.treaty else ""
    lines = [f"{fp.name}: {ending} after {elapsed} days ({took:.1f}s){'; terms: ' + terms if terms else ''}",
             f"    sides {dict((s.value, t) for s, t in sides.items())}; nuclear strikes {nukes}",
             f"    ground changed: {', '.join(f'{k} {v:,.0f} km2' for k, v in sorted(changed.items(), key=lambda kv: -kv[1])[:4]) or 'none'}",
             f"    casualties {casualties}"]
    del moved
    notable = [e for e in war.events if e.kind in ("pact_triggered", "nuclear_strike", "withdrawal", "capitulation",
                                                    "government_in_exile", "offensive_halted", "escalation", "intervention")]
    for e in notable[:5]:
        lines.append(f"    day {e.hour // 24}: {e.message[:120]}")
    return "\n".join(lines)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    for fp in FLASHPOINTS:
        print(run(fp, days), flush=True)
