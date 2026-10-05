"""The air war: who controls the sky, what that does on the ground, and strategic strikes.

Each day, for every pair of warring sides:

  superiority  s = A / (A + A_enemy + G_enemy). A is air power (aircraft weighted by quality,
               scaled by airfields still usable); G is ground-based air defence: long-range SAM
               battalions (curated, data/curated/missile_defense.json) plus the short-range air
               defence every army carries. Anchor: Russia's ~1,500 combat aircraft never won the sky
               over a Ukraine fielding ~30 long-range SAM battalions in 2022, so one battalion weighs
               about as much as 50 combat aircraft (AIR_DEFENCE_PER_BATTERY).
  ground       combat effectiveness x (1 + AIR_GROUND_MAX x net^2 signed), net = s_own - s_enemy.
               Contested skies give a modest edge (Russia's glide bombs, 2024-25); dominance is
               decisive (1991: the coalition's air campaign broke Iraq's army before the ground war).
  strikes      a share of the sorties, getting through at s^3, hits the enemy's most valuable
               provinces in range (industry, energy, airfields), raising Province.damage: output
               falls, airfields fly fewer sorties; damage is repaired at REPAIR_RATE. Calibrated so
               that a Kosovo-style campaign (NATO against Serbia, 1999) forces concessions in about
               eleven weeks, while Russia's against Ukraine's air defences degrades but cannot cripple.
  attrition    aircraft are lost in proportion to sorties flown into defended sky.

Not modelled yet: conventional ballistic and cruise missiles (no inventory data), drones as a strike
arm (they appear as drone saturation on the ground), naval aviation as separate from air power.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wargame.conflict.land_warfare import NAVAL_SUPERIORITY, LandWarfare
from wargame.core.enums import Branch, ProvinceTag
from wargame.core.mathutil import clamp

if TYPE_CHECKING:
    from wargame.conflict.war import War
    from wargame.nation.country import Country
    from wargame.simulation import Simulation
    from wargame.world.province import Province
    from wargame.world.world import World

# Long-range SAM systems that also fight aircraft (BMD-only systems such as GMD, THAAD and Arrow do not).
AIR_DEFENCE_SYSTEMS = frozenset({
    "Patriot PAC-3", "S-400", "S-300 (legacy)", "HQ-9", "SAMP/T", "KM-SAM II", "Tien Kung III", "Bavar-373",
    "David's Sling", "Aegis BMD (SM-3)",
})
AIR_DEFENCE_PER_BATTERY = 100.0      # Air-power units one long-range SAM battalion denies (see docstring).
ORGANIC_AIR_DEFENCE = 0.05           # x land power: the short-range air defence every army carries.
AIR_GROUND_MAX = 0.5                 # x1.5 / x0.67 to ground combat at total air dominance either way.
STRIKE_SHARE = 0.3                   # Share of air power flying strikes rather than air superiority or support.
STRIKE_TARGETS = 5                   # Most valuable enemy provinces in range, struck each day.
STRIKE_RANGE_KM = 2_000.0            # Combat radius with tanking (Israel to Iran ~1,600 km).
STRIKE_DAMAGE = 0.029                # Most damage a province takes in a day (Kosovo 1999: concessions after ~80 days)
STRIKE_HARDNESS = 100.0              # ...half of it from this much strike power (diminishing returns).
STRIKE_CASUALTIES_PER_POWER = 0.02   # Killed and wounded per day per unit of strike power that gets through.
STRIKE_PENETRATION_EXPONENT = 3.0    # Strikes get through layered defences at s^3: Ukraine downs ~80% of Russia's
                                     # missiles and drones (s 0.6 -> 0.22); Serbia, 1999, almost nothing (0.99 -> 0.97).
REPAIR_RATE = 0.015                  # Share of damage repaired a day (Ukraine restores its grid within months).
AIR_LOSS_RATE = 0.0004               # Aircraft lost per day, as a share of the fleet, flying into fully defended sky.
LEVERAGE_PROVINCES = 5               # Coercion leverage: mean damage of the target's most valuable provinces.
BASING_RELATION = 0.7                # Allies this close lend their airfields (the US flew from Aviano, 1999).


@dataclass
class AirWar:
    superiority: dict[tuple[str, str], float] = field(default_factory=dict)  # (tag, enemy) -> s over the enemy
    ground: dict[str, float] = field(default_factory=dict)                   # tag -> ground combat multiplier
    _loss_carry: dict[tuple[str, str], float] = field(default_factory=dict)

    def daily(self, sim: Simulation) -> None:
        world, wars = sim.world, sim.active_wars
        self.repair(world)
        self.superiority.clear()
        self.ground.clear()
        if not wars:
            return
        pairs: set[tuple[frozenset[str], frozenset[str]]] = set()
        for war in wars:
            for tag in war.participants:
                pairs.add((war.tags_on(war.side_of(tag)), war.enemies_of(tag)))
        for friends, foes in sorted(pairs, key=lambda fp: (sorted(fp[0]), sorted(fp[1]))):
            s_own = self._superiority(world, friends, foes)
            s_enemy = self._superiority(world, foes, friends)
            net = s_own - s_enemy
            multiplier = 1.0 + AIR_GROUND_MAX * math.copysign(net * net, net)
            for tag in friends:
                for foe in foes:
                    self.superiority[(tag, foe)] = s_own
                self.ground[tag] = min(self.ground.get(tag, multiplier), multiplier) if tag in self.ground else multiplier
            self._strike(world, wars, friends, foes, s_own)
            self._attrition(world, friends, s_own)
        for war in wars:
            target = world.country(war.goal.target)
            target.strategic_damage = self.leverage(world, war.goal.target)

    @staticmethod
    def repair(world: World) -> None:
        """Damage heals: grids are patched, factories rebuilt (runs every day, war or peace)."""
        for p in world.provinces.values():
            if p.damage > 0:
                p.damage = max(0.0, p.damage * (1.0 - REPAIR_RATE) - 1e-4)

    # --- the contest for the sky ------------------------------------------------------------------

    @staticmethod
    def air_power(world: World, tags: frozenset[str]) -> float:
        total = 0.0
        for t in sorted(tags):
            country = world.countries.get(t)
            if country is None:
                continue
            fields = [p for p in world.controlled_by(t) if p.has(ProvinceTag.AIRFIELD)]
            usable = sum(1.0 - p.damage for p in fields) / len(fields) if fields else 1.0
            total += country.oob.branch_power(Branch.AIR) * usable
        return total

    @staticmethod
    def air_defence(world: World, tags: frozenset[str]) -> float:
        total = 0.0
        for t in sorted(tags):
            country = world.countries.get(t)
            if country is None:
                continue
            batteries = sum(d.batteries for d in country.nuclear.missile_defenses if d.name in AIR_DEFENCE_SYSTEMS)
            total += AIR_DEFENCE_PER_BATTERY * batteries + ORGANIC_AIR_DEFENCE * country.oob.branch_power(Branch.LAND)
        return total

    def _superiority(self, world: World, friends: frozenset[str], foes: frozenset[str]) -> float:
        own = self.air_power(world, friends)
        contest = own + self.air_power(world, foes) + self.air_defence(world, foes)
        return own / contest if contest > 0 else 0.5

    def ground_multiplier(self, tag: str) -> float:
        return self.ground.get(tag, 1.0)

    # --- strikes -------------------------------------------------------------------------------------

    def _strike(self, world: World, wars: list[War], friends: frozenset[str], foes: frozenset[str], s: float) -> None:
        power = STRIKE_SHARE * self.air_power(world, friends) * math.pow(s, STRIKE_PENETRATION_EXPONENT)
        if power <= 0:
            return
        bases = [p for t in sorted(self._basing(world, friends, foes)) for p in world.controlled_by(t) if p.has(ProvinceTag.AIRFIELD)]
        carriers = self._carrier_reach(world, friends, foes)
        if not bases and not carriers:
            return
        targets = sorted((p for t in sorted(foes) for p in world.controlled_by(t)
                          if p.owner in foes and p.damage < 1.0 and (_in_range(p, bases) or _in_range(p, carriers))),
                         key=lambda p: (-p.strategic_value() * (1.0 - p.damage), p.id))[:STRIKE_TARGETS]
        if not targets:
            return
        share = power / len(targets)
        for p in targets:
            p.damage = clamp(p.damage + STRIKE_DAMAGE * share / (share + STRIKE_HARDNESS))
        victim = targets[0].controller
        for war in wars:
            if victim in war.participants and any(t in war.participants for t in friends):
                war.record_casualties(world, victim, max(1, round(STRIKE_CASUALTIES_PER_POWER * power)), offensive=False)
                break

    @staticmethod
    def _basing(world: World, friends: frozenset[str], foes: frozenset[str]) -> set[str]:
        """Our side, and friends who lend airfields: treaty allies or close partners not at war with us."""
        out = set(friends)
        for t in friends:
            country = world.countries.get(t)
            if country is None:
                continue
            for other, c in world.countries.items():
                if other in foes or other in out:
                    continue
                if other in country.defensive_pacts or c.relations.get(t, 0.0) >= BASING_RELATION:
                    out.add(other)
        return out

    @staticmethod
    def _carrier_reach(world: World, friends: frozenset[str], foes: frozenset[str]) -> list[Province]:
        """With carriers and command of the sea, the enemy's own coast is the launch point."""
        if not any(t in world.countries and LandWarfare.blue_water(world.country(t)) for t in friends):
            return []
        own = sum(world.country(t).oob.branch_power(Branch.NAVAL) for t in friends if t in world.countries)
        enemy = sum(world.country(t).oob.branch_power(Branch.NAVAL) for t in foes if t in world.countries)
        if own < NAVAL_SUPERIORITY * max(enemy, 1.0):
            return []
        return [p for t in sorted(foes) for p in world.controlled_by(t) if p.coastal]

    @staticmethod
    def leverage(world: World, tag: str) -> float:
        """How badly the country's most valuable provinces are damaged: the coercive weight of strikes."""
        top = sorted(world.owned_by(tag), key=lambda p: (-p.strategic_value(), p.id))[:LEVERAGE_PROVINCES]
        return sum(p.damage for p in top) / len(top) if top else 0.0

    # --- losses --------------------------------------------------------------------------------------

    def _attrition(self, world: World, friends: frozenset[str], s: float) -> None:
        for t in sorted(friends):
            country: Country | None = world.countries.get(t)
            if country is None:
                continue
            for name, stock in country.oob.equipment.items():
                if stock.branch is not Branch.AIR or stock.quantity <= 0:
                    continue
                lost = stock.quantity * AIR_LOSS_RATE * (1.0 - s) + self._loss_carry.get((t, name), 0.0)
                whole = int(lost)
                self._loss_carry[(t, name)] = lost - whole
                stock.quantity = max(0, stock.quantity - whole)


def _in_range(target: Province, bases: list[Province]) -> bool:
    return any(target.distance_km(b) <= STRIKE_RANGE_KM for b in bases)
