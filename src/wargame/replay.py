"""Wars as day-by-day records: what the browser edition plays back, and what a live war in the browser streams.

A frame is one day: province control that changed, ground being taken, assaults under way, each belligerent's
figures, the war score, and (when they change) the escalation tier and who arms whom. Countries are referred
to by index into the record's `tags` list, which only ever grows.
"""

from __future__ import annotations

from typing import Any

from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import Side
from wargame.simulation import Simulation

POSTURES = ["offensive", "halted", "counteroffensive", "active_defence", "defence"]


class Recorder:
    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.goal: WarGoal = sim.wars[0].goal  # As declared: pursuing a government in exile changes it later.
        self.tags: list[str] = []
        self._index: dict[str, int] = {}
        self.drivers: list[str] = []
        self.frames: list[dict[str, Any]] = []
        self._control = {p.id: (p.owner, p.controller) for p in sim.world.provinces.values()}
        self._supporters: Any = None
        self._tier: int | None = None
        for t in sorted(sim.world.countries):
            self.tag(t)

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

    def frame(self) -> dict[str, Any]:
        """Record today and return the frame."""
        sim, world, land, air = self.sim, self.sim.world, self.sim.land, self.sim.air
        war = sim.wars[0]
        changes = []
        for p in world.provinces.values():
            now = (p.owner, p.controller)
            if self._control[p.id] != now:
                changes.append([p.id, self.tag(p.owner), self.tag(p.controller)])
                self._control[p.id] = now
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
        return frame

    def names(self) -> dict[str, str]:
        world = self.sim.world
        return {t: world.country(t).name for t in self.tags if t in world.countries}

    def events(self) -> list[list[Any]]:
        evs = sorted((e for w in self.sim.wars for e in w.events), key=lambda e: e.hour)
        return [[e.hour // 24, e.kind, e.message] for e in evs]

    def outcome(self) -> dict[str, Any]:
        """What changes as the war goes on, beyond the frames: tables, diary, strikes, the peace."""
        war = self.sim.wars[0]
        treaty = war.treaty
        return {
            "tags": self.tags, "names": self.names(), "drivers": self.drivers,
            "events": self.events(),
            "nukes": [[s.hour // 24, s.user, s.province_id, s.intercepted] for s in war.nuclear_strikes],
            "ended": self.sim.finished,
            "treaty": None if treaty is None else {
                "day": treaty.signed_hour // 24, "reason": treaty.reason, "winner": treaty.winner, "loser": treaty.loser,
                "frozen": treaty.frozen,
                "terms": [[t.type.value, t.beneficiary, t.target, len(t.province_ids)] for t in treaty.terms],
            },
        }

    def header(self, key: str, year: int, blurb: str = "", nuclear: bool = False) -> dict[str, Any]:
        """The whole record so far. `year` is the data set the war started from (2021 or 2026)."""
        sim = self.sim
        return {
            "key": key, "name": sim.scenario.name, "year": year,
            "start": sim.scenario.start.date().isoformat(), "blurb": blurb, "nuclear": nuclear,
            "goal": {"type": self.goal.type.value, "holder": self.goal.holder, "target": self.goal.target,
                     "provinces": sorted(self.goal.province_ids)},
            "postures": POSTURES, "frames": self.frames, **self.outcome(),
        }


def record(sim: Simulation, key: str, year: int, max_days: int, blurb: str = "", nuclear: bool = False) -> dict[str, Any]:
    """Run a war to its end (or `max_days`) and return the whole record."""
    rec = Recorder(sim)
    rec.frame()
    while sim.clock.day < max_days and not sim.finished:
        sim.run_days(1)
        rec.frame()
    return rec.header(key, year, blurb, nuclear)
