"""Run a set of real-world flashpoints and report how each war goes.

    python tools/scenarios/sweep.py [days]

Not a prediction: a smoke test of the whole engine on scenarios far from the one it was calibrated
on (Ukraine). It shows how wars end, how long they take, who joins and what changes hands, so
implausible behaviour stands out.
"""

from __future__ import annotations

import sys
import time

from wargame.scenarios import FLASHPOINTS, Flashpoint, from_preset


def run(fp: Flashpoint, days: int) -> str:
    sim = from_preset(fp)
    world = sim.world
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
    joined_at_start = sum(1 for e in war.events if e.kind == "pact_triggered" and e.hour < 24)
    if joined_at_start:
        lines.append(f"    day 0: {joined_at_start} allies honour their pacts")
    # Late entrants and exits matter most: who widened the war, who left it, and how it ended.
    notable = [e for e in war.events
               if e.kind in ("pact_triggered", "patron_intervention", "intervention", "opportunist", "separate_peace",
                             "nuclear_detonation", "escalation", "capitulation", "government_in_exile", "pursuit",
                             "offensive_halted", "withdrawal")
               and not (e.kind == "pact_triggered" and e.hour < 24)]
    for e in notable[:8]:
        lines.append(f"    day {e.hour // 24}: {e.message[:120]}")
    return "\n".join(lines)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    for fp in FLASHPOINTS:
        print(run(fp, days), flush=True)
