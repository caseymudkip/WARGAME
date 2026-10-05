"""A running war for the spectator app: one simulation, stepped by a background thread at the chosen speed.

The spectator contract holds here too: the scenario is fixed when the session starts, and the only thing a
viewer can change afterwards is the playback speed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from wargame import scenarios
from wargame.data.world_map import build_real_world
from wargame.core.clock import TimeScale
from wargame.core.enums import EscalationTier, Motivation, Side, WarGoalType
from wargame.simulation import Simulation
from wargame.world.province import Province

FRAME_SECONDS = 0.05
SPEEDS = {s.name: s for s in TimeScale}


@dataclass(frozen=True)
class Setup:
    """What the player chose on the setup screen."""

    preset: str | None = None
    year: int = 2026
    attacker: str = ""
    defender: str = ""
    goal: str = WarGoalType.REGIME_CHANGE.value
    tier: int = EscalationTier.PROXY_WAR.value
    nuclear: bool = False
    attacker_motivation: str = Motivation.CAUTIOUS.name
    defender_motivation: str = Motivation.CAUTIOUS.name
    provinces: tuple[int, ...] = ()
    seed: int = 0

    @staticmethod
    def from_json(data: dict[str, Any]) -> Setup:
        return Setup(
            preset=data.get("preset") or None,
            year=int(data.get("year", 2026)),
            attacker=str(data.get("attacker", "")).upper(),
            defender=str(data.get("defender", "")).upper(),
            goal=str(data.get("goal", WarGoalType.REGIME_CHANGE.value)),
            tier=int(data.get("tier", EscalationTier.PROXY_WAR.value)),
            nuclear=bool(data.get("nuclear", False)),
            attacker_motivation=str(data.get("attacker_motivation", Motivation.CAUTIOUS.name)),
            defender_motivation=str(data.get("defender_motivation", Motivation.CAUTIOUS.name)),
            provinces=tuple(int(p) for p in data.get("provinces", ())),
            seed=int(data.get("seed", 0)),
        )

    def build(self) -> Simulation:
        if self.preset:
            if self.preset not in scenarios.PRESETS:
                raise ValueError(f"unknown preset {self.preset}")
            return scenarios.from_preset(scenarios.PRESETS[self.preset], seed=self.seed)
        if self.year not in scenarios.START_DATES:
            raise ValueError("the start year must be 2021 or 2026")
        motivations = {m.name: m for m in Motivation}
        for name in (self.attacker_motivation, self.defender_motivation):
            if name not in motivations:
                raise ValueError(f"unknown motivation {name}")
        return scenarios.custom(
            self.year, self.attacker, self.defender, WarGoalType(self.goal), EscalationTier(self.tier),
            nuclear=self.nuclear, attacker_motivation=motivations[self.attacker_motivation],
            defender_motivation=motivations[self.defender_motivation],
            provinces=frozenset(self.provinces), seed=self.seed,
        )


class Session:
    def __init__(self, sim: Simulation, setup: Setup) -> None:
        self.sim = sim
        self.setup = setup
        self.lock = threading.Lock()
        self.sim.set_speed(TimeScale.PAUSED)
        self.map_version = 0
        self._map_key = self._map_signature()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="wargame-sim", daemon=True)
        self._thread.start()

    # --- control ---------------------------------------------------------------------------------

    def set_speed(self, name: str) -> None:
        if name not in SPEEDS:
            raise ValueError(f"unknown speed {name}")
        with self.lock:
            self.sim.set_speed(SPEEDS[name])

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            time.sleep(FRAME_SECONDS)
            now = time.perf_counter()
            with self.lock:
                if not self.sim.finished:
                    self.sim.advance_frame(min(now - last, 0.5))  # A slow frame never runs away with the clock.
                    key = self._map_signature()
                    if key != self._map_key:
                        self._map_key, self.map_version = key, self.map_version + 1
            last = now

    def _map_signature(self) -> int:
        return hash(tuple((p.owner, p.controller) for p in self.sim.world.provinces.values()))

    # --- views -----------------------------------------------------------------------------------

    def state(self, since_event: int = 0, map_version: int = -1) -> dict[str, Any]:
        with self.lock:
            sim, world = self.sim, self.sim.world
            events = sorted(sim.events(), key=lambda e: e.hour)
            out: dict[str, Any] = {
                "scenario": sim.scenario.name,
                "date": sim.clock.now.strftime("%d %b %Y"),
                "day": sim.clock.day,
                "speed": sim.speed.scale.name,
                "finished": sim.finished,
                "event_count": len(events),
                "events": [{"day": e.hour // 24, "kind": e.kind, "message": e.message} for e in events[since_event:]],
                "contested": [[pid, tag, round(progress, 3)] for pid, (tag, progress) in sorted(world.contested.items())
                              if progress > 0.001],
                "attacks": [[origin, target, tag, round(power, 1), bool(km)]
                            for tag, dep in sorted(sim.land.deployments.items())
                            for target, (origin, power, km) in sorted(dep.attacks.items())],
                "wars": [self._war(w) for w in sim.wars],
                "names": {tag: world.country(tag).name for w in sim.wars for tag in sorted({*w.participants, *w.exited})
                          if tag in world.countries},
                "map_version": self.map_version,
            }
            if map_version != self.map_version:
                out["provinces"] = [[p.owner, p.controller] for p in sorted(world.provinces.values(), key=lambda p: p.id)]
            return out

    def _war(self, war: Any) -> dict[str, Any]:
        world, land, air = self.sim.world, self.sim.land, self.sim.air
        sides = {}
        for side in (Side.ATTACKER, Side.DEFENDER):
            members = []
            for tag in sorted(war.tags_on(side)):
                country = world.country(tag)
                part = war.participants[tag]
                assessment = war.assessments.get(tag)
                members.append({
                    "tag": tag,
                    "name": country.name,
                    "role": part.role.value,
                    "casualties": country.oob.casualties_total,
                    "active": country.oob.active_personnel,
                    "resolve": round(part.resolve, 2),
                    "pressure": round(assessment.pressure, 2) if assessment else None,
                    "threshold": round(assessment.threshold, 2) if assessment else None,
                    "main_driver": max(assessment.components, key=lambda k: assessment.components[k])
                    if assessment and assessment.components else None,
                    "posture": land.posture[tag].value if tag in land.posture else None,
                    "halted": part.offensive_halted,
                    "drones": round(country.drone_saturation, 2),
                    "air": round(max((s for (t, _), s in air.superiority.items() if t == tag), default=0.0), 2),
                    "strategic_damage": round(country.strategic_damage, 2),
                    "nuclear": country.nuclear.warheads,
                    "claim": sorted(part.claim.province_ids) if part.claim else [],
                })
            sides[side.value] = members
        treaty = war.treaty
        return {
            "goal": war.goal.type.value,
            "holder": war.goal.holder,
            "target": war.goal.target,
            "goal_provinces": sorted(war.goal.province_ids),
            "tier": war.escalation_tier.value,
            "war_score": round(war.war_score, 1),
            "ended": war.ended,
            "sides": sides,
            "supporters": {tag: sorted({f.supporter for f in war.external_support if f.recipient == tag})
                           for tag in sorted({f.recipient for f in war.external_support})},
            "nuclear_strikes": [{"day": s.hour // 24, "user": s.user, "province": s.province_id,
                                 "intercepted": s.intercepted} for s in war.nuclear_strikes],
            "treaty": None if treaty is None else {
                "reason": treaty.reason, "winner": treaty.winner, "loser": treaty.loser,
                "terms": [{"type": t.type.value, "beneficiary": t.beneficiary, "target": t.target,
                           "provinces": len(t.province_ids)} for t in treaty.terms],
            },
        }

    def province(self, pid: int) -> dict[str, Any]:
        with self.lock:
            world, land = self.sim.world, self.sim.land
            if pid not in world.provinces:
                raise KeyError(pid)
            p: Province = world.provinces[pid]
            stationed = {tag: round(dep.stationed[pid], 1) for tag, dep in sorted(land.deployments.items())
                         if pid in dep.stationed}
            attacker, progress = world.contested.get(pid, (None, 0.0))
            return {
                "id": p.id, "name": p.name, "owner": p.owner, "controller": p.controller,
                "owner_name": world.country(p.owner).name if p.owner in world.countries else p.owner,
                "controller_name": world.country(p.controller).name if p.controller in world.countries else p.controller,
                "terrain": p.terrain.value, "population": p.population, "area_km2": round(p.area_km2),
                "tags": sorted(t.value for t in p.tags), "value": round(p.strategic_value(), 2),
                "damage": round(p.damage, 2), "fortification": round(land.fortification.get(pid, 0.0), 2),
                "stationed": stationed, "contested_by": attacker, "progress": round(progress, 3),
                "encircled": pid in land.encircled,
            }


def countries(year: int) -> list[dict[str, Any]]:
    """Every playable country at a start date, strongest first within a name sort the UI can use."""
    world = build_real_world(year).world
    return [{"tag": tag, "name": c.name, "power": round(c.military_power()), "nuclear": c.nuclear.is_nuclear_power}
            for tag, c in sorted(world.countries.items(), key=lambda kv: kv[1].name)
            if next(world.owned_by(tag), None) is not None]
