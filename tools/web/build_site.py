"""Build the browser edition of WARGAME: the flashpoints, recorded day by day, and a viewer that replays them.

    python tools/web/build_site.py OUT_DIR [--days 730] [--only taiwan,korea]

The engine runs here, in Python, exactly as in the spectator app; the browser only plays the record back.
A spectator can't change a war once it starts, so a replay of the same scenario and seed is the same war.
Custom wars need the engine itself and stay in the local app (`wargame`).

OUT_DIR receives index.html (the viewer, from tools/web/viewer.html) and data/: meta.json, world-2021.json,
world-2026.json, geometry.json and replays/<flashpoint>.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from wargame import __version__, scenarios
from wargame.conflict.war import War
from wargame.core.enums import Side
from wargame.data.snapshot import DEFAULT_ROOT
from wargame.data.world_map import build_real_world
from wargame.simulation import Simulation

HERE = Path(__file__).resolve().parent
POSTURES = ["offensive", "halted", "counteroffensive", "active_defence", "defence"]


def world_file(year: int) -> dict[str, Any]:
    world = build_real_world(year).world
    return {
        "year": year,
        "countries": {tag: c.name for tag, c in sorted(world.countries.items())},
        # name, owner, controller, lat, lon, area km2, terrain, population
        "provinces": [[p.name, p.owner, p.controller, p.lat, p.lon, round(p.area_km2), p.terrain.value, p.population]
                      for p in sorted(world.provinces.values(), key=lambda p: p.id)],
    }


class Recorder:
    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.tags: list[str] = []
        self._index: dict[str, int] = {}
        self.drivers: list[str] = []
        self.frames: list[dict[str, Any]] = []
        world = sim.world
        self._control = {p.id: (p.owner, p.controller) for p in world.provinces.values()}
        self._supporters: Any = None
        self._tier: int | None = None

    def tag(self, t: str) -> int:
        if t not in self._index:
            self._index[t] = len(self.tags)
            self.tags.append(t)
        return self._index[t]

    def driver(self, name: str | None) -> int:
        if name is None:
            return -1
        if name not in self.drivers:
            self.drivers.append(name)
        return self.drivers.index(name)

    def frame(self) -> None:
        sim, world, land, air = self.sim, self.sim.world, self.sim.land, self.sim.air
        changes = []
        for p in world.provinces.values():
            now = (p.owner, p.controller)
            if self._control[p.id] != now:
                changes.append([p.id, self.tag(p.owner), self.tag(p.controller)])
                self._control[p.id] = now
        war = sim.wars[0]
        sides = []
        for side in (Side.ATTACKER, Side.DEFENDER):
            members = []
            for t in sorted(war.tags_on(side)):
                country, part = world.country(t), war.participants[t]
                a = war.assessments.get(t)
                driver = max(a.components, key=lambda k: a.components[k]) if a and a.components else None
                posture = land.posture.get(t)
                members.append([
                    self.tag(t), country.oob.casualties_total, country.oob.active_personnel,
                    round(part.resolve * 100), round(min(1.0, a.pressure / a.threshold) * 100) if a and a.threshold else 0,
                    POSTURES.index(posture.value) if posture else -1,
                    round(max((s for (x, _), s in air.superiority.items() if x == t), default=0.0) * 100),
                    round(country.drone_saturation * 100), round(country.strategic_damage * 100), self.driver(driver),
                    1 if part.role.value == "primary" else 0, country.nuclear.warheads,
                ])
            sides.append(members)
        frame: dict[str, Any] = {
            "d": sim.clock.day,
            "k": [[pid, self.tag(t), round(progress * 1000)] for pid, (t, progress) in sorted(world.contested.items())
                  if progress > 0.001],
            "a": [[origin, target, self.tag(t), round(power), 1 if km else 0]
                  for t, dep in sorted(land.deployments.items()) for target, (origin, power, km) in sorted(dep.attacks.items())],
            "s": sides,
            "w": round(war.war_score, 1),
        }
        if changes:
            frame["c"] = changes
        tier = int(war.escalation_tier.value)
        if tier != self._tier:
            frame["t"] = tier
            self._tier = tier
        supporters = {self.tag(r): sorted(self.tag(f.supporter) for f in war.external_support if f.recipient == r)
                      for r in sorted({f.recipient for f in war.external_support})}
        if supporters != self._supporters:
            frame["sup"] = supporters
            self._supporters = supporters
        self.frames.append(frame)


def record(fp: scenarios.Flashpoint, max_days: int) -> dict[str, Any]:
    sim = scenarios.from_preset(fp)
    goal = sim.wars[0].goal  # As declared: pursuing a government in exile changes it later.
    rec = Recorder(sim)
    for t in sorted(sim.world.countries):
        rec.tag(t)
    rec.frame()
    while sim.clock.day < max_days and not sim.finished:
        sim.run_days(1)
        rec.frame()
    war: War = sim.wars[0]
    events = sorted((e for w in sim.wars for e in w.events), key=lambda e: e.hour)
    treaty = war.treaty
    names = {t: sim.world.country(t).name for t in rec.tags if t in sim.world.countries}
    return {
        "key": fp.key, "name": fp.name, "year": fp.year, "start": sim.scenario.start.date().isoformat(),
        "blurb": fp.blurb, "nuclear": fp.nuclear,
        "goal": {"type": goal.type.value, "holder": goal.holder, "target": goal.target,
                 "provinces": sorted(goal.province_ids)},
        "tags": rec.tags, "names": names, "drivers": rec.drivers, "postures": POSTURES,
        "frames": rec.frames,
        "events": [[e.hour // 24, e.kind, e.message] for e in events],
        "nukes": [[s.hour // 24, s.user, s.province_id, s.intercepted] for s in war.nuclear_strikes],
        "ended": sim.finished,
        "treaty": None if treaty is None else {
            "day": treaty.signed_hour // 24, "reason": treaty.reason, "winner": treaty.winner, "loser": treaty.loser,
            "terms": [[t.type.value, t.beneficiary, t.target, len(t.province_ids)] for t in treaty.terms],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    out: Path = args.out
    (out / "data" / "replays").mkdir(parents=True, exist_ok=True)
    keys = [k for k in args.only.split(",") if k] or [fp.key for fp in scenarios.FLASHPOINTS]

    compact = {"separators": (",", ":"), "ensure_ascii": False}
    for year in sorted(scenarios.START_DATES):
        (out / "data" / f"world-{year}.json").write_text(json.dumps(world_file(year), **compact))
    shutil.copyfile(DEFAULT_ROOT / "map" / "geometry.json", out / "data" / "geometry.json")
    presets = []
    for fp in scenarios.FLASHPOINTS:
        if fp.key not in keys:
            continue
        t0 = time.perf_counter()
        replay = record(fp, args.days)
        path = out / "data" / "replays" / f"{fp.key}.json"
        path.write_text(json.dumps(replay, **compact))
        last = replay["frames"][-1]["d"]
        ending = replay["treaty"]["reason"] if replay["treaty"] else f"still fighting on day {last}"
        print(f"{fp.key:14s} {len(replay['frames']):4d} days, {path.stat().st_size / 1e6:4.1f} MB, "
              f"{time.perf_counter() - t0:5.1f}s: {ending}", flush=True)
        presets.append({"key": fp.key, "name": fp.name, "year": fp.year, "attacker": fp.attacker, "defender": fp.defender,
                        "blurb": fp.blurb, "days": last, "ending": ending})
    (out / "data" / "meta.json").write_text(json.dumps({"version": __version__, "presets": presets}, **compact))
    shutil.copyfile(HERE / "viewer.html", out / "index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
