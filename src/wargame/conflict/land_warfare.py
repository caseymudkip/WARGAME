"""Land warfare: fronts, force allocation, hourly combat, encirclement and naval blockade.

Abstraction. A country's ground forces are a pool of combat power: land equipment
weighted by quality and combat value (wargame.data.profile.EQUIPMENT) plus infantry.
Every day a planner spreads that pool over the provinces it holds against the
enemy and picks which enemy provinces to assault; every hour each assaulted
province is fought over.

  planning     a share of the committed force attacks (motivation sets how much), the
               rest holds the line, weighted to valuable and threatened provinces.
               Targets are scored by value x war-goal relevance per defender; assaults
               expected to go in below MIN_ASSAULT_RATIO are called off and their troops
               given to the others, so armies concentrate instead of bleeding everywhere.
  ratio R      attacker power x effectiveness x crossing penalty (river, amphibious) /
               (defender power x effectiveness + local defence) x terrain x
               (1 + fortification) [x 0.5 if the defenders are encircled]
  advance      only when R > 1: ADVANCE_SCALE x (R - 1)^ADVANCE_EXPONENT km2/day,
               slowed by terrain. A province's progress bar is the share of its area
               taken, so a grinding advance shows up long before the province falls.
  casualties   a share of the engaged personnel per day; attackers bleed more when R is
               low, defenders when it is high, pockets far more. Defenders in contact are
               limited by the attackers' frontage and protected by terrain and works.

Calibration: tools/calibration/ukraine_2025.py runs Russia attacking Ukraine from the real
1 January 2026 front (Tier 2, aggressive, demanding the four oblasts it claims) for a year:
  ground taken         11.9 km2/day   DeepState: 4,336 km2 in 2025 (11.9/day)
  Russian casualties   ~1,200/day     UK MoD / CSIS: ~415,000 in 2025 (1,137/day)
  Ukrainian / Russian  ~0.45          CSIS (Jan 2026): 500-600k vs ~1.2M since 2022
and the attacks fall on the real 2025 axes (Pokrovsk-Kostiantynivka, Zaporizhzhia,
Kupiansk), not across the Dnipro. Checked by tests/test_land_warfare.py.

Also here because they are daily force-level effects: encirclement (provinces cut off
from the capital), fortification of static fronts, equipment attrition and
replacement, and naval blockade from fleet ratios (writes Country.blockade_interdiction,
which cuts seaborne imports and drives coercion war goals).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wargame.core.enums import Branch, ParticipantRole, ProvinceTag, Side
from wargame.core.mathutil import clamp
from wargame.conflict.war_goal import EXISTENTIAL_GOALS, PROVINCE_GOALS

if TYPE_CHECKING:
    from wargame.conflict.war import War
    from wargame.nation.country import Country
    from wargame.simulation import Simulation
    from wargame.world.world import World

# --- force pool -----------------------------------------------------------------------------
INFANTRY_POWER_PER_1000 = 2.0          # Combat power of 1,000 active personnel (one tank-equivalent ~ 1).
COMMITMENT_EXISTENTIAL_DEFENCE = 0.9
COMMITMENT_DEFENCE = 0.8
COMMITMENT_ATTACK = {"realistic_cautious": 0.45, "epic_aggressive": 0.6}  # Russia 2025: ~700k of 1.32M in Ukraine.
COMMITMENT_CO_BELLIGERENT = 0.5
EXPEDITIONARY_EFFICIENCY = 0.5         # Forces fighting from an ally's territory.
REDEPLOY_RATE = 0.25                   # Share of the gap to the planned deployment closed per day.

# --- offensives -------------------------------------------------------------------------------
OFFENSIVE_SHARE = {"realistic_cautious": 0.35, "epic_aggressive": 0.5}
OFFENSIVE_SHARE_HALTED = 0.1
COUNTERATTACK_SHARE = 0.15
COUNTERATTACK_MIN_RESOLVE = 0.5
MAX_TARGETS = 6
MIN_ASSAULT_RATIO = 1.1                # Planners don't send troops into assaults they expect to lose.

# --- combat --------------------------------------------------------------------------------------
ADVANCE_SCALE = 3.2                    # km2/day at R = 2 on open ground.
ADVANCE_EXPONENT = 2.0                 # Steep: a grind at R ~ 2, a collapse at R ~ 10.
MAX_ADVANCE_KM2_PER_DAY = 2_500.0      # Exploitation against nothing (Kharkiv, September 2022: ~1,000/day).
CASUALTY_RATE = 0.004                  # Share of engaged personnel lost per day at R = 1.
DEFENDER_FRONTAGE = 2.5                # Defenders in contact: at most attackers / 2.5 (holding takes fewer troops).
ENCIRCLED_DEFENCE = 0.5
ENCIRCLED_CASUALTIES = 2.0
LOCAL_DEFENCE_BASE = 5.0               # Territorial defence even where no army stands...
LOCAL_DEFENCE_PER_100K = 0.5           # ...growing with the population defending its home.
CAPTURE_DAMAGE = 0.15
CAPTURE_DAMAGE_URBAN = 0.3
AIR_EFFECT = 0.2                       # Full air dominance: +/-10% to ground combat.

# --- rivers ------------------------------------------------------------------------------------------
# Attack multiplier for assaults across a river border. An opposed crossing of a major river is among
# the hardest operations there is: Ukraine's Dnipro bridgehead at Krynky (Oct 2023 - Jul 2024) never broke out.
MAJOR_RIVER_MAX_SCALERANK = 4          # Natural Earth: Dnipro, Rhine, Oder 4; Danube 2; Vistula 5; Don 6.
MAJOR_RIVER_CROSSING = 0.5
RIVER_CROSSING = 0.7

# --- fortification -------------------------------------------------------------------------------
FORTIFICATION_GROWTH = 0.01
FORTIFICATION_DECAY = 0.01
FORTIFICATION_MAX = 0.6
ESTABLISHED_FRONT_FORTIFICATION = 0.5  # A front that already exists when the war starts is dug in.

# --- naval ----------------------------------------------------------------------------------------
NAVAL_SUPERIORITY = 1.5                # Needed for amphibious assaults and sea-supplied pockets.
AMPHIBIOUS_MAX_KM = 250
BRIDGE_KM = 10                         # Shorter sea links count as land for supply (Kerch bridge).
BLOCKADE_MIN_RATIO = 2.0
BLOCKADE_BASE = 0.2
BLOCKADE_PER_RATIO = 0.15
BLOCKADE_MAX = 0.8

# --- attrition --------------------------------------------------------------------------------------
EQUIPMENT_LOSS_PER_CASUALTY_SHARE = 0.6
EQUIPMENT_PRODUCTION_PER_DAY = 0.0004  # Share of pre-war stock produced or refurbished per day at full industry.
MAX_REPLACEMENT_SHARE_PER_DAY = 0.003  # Replacements per day as a share of active strength.


def amphibious_penalty(km: float) -> float:
    return 0.35 - 0.2 * min(km, AMPHIBIOUS_MAX_KM) / AMPHIBIOUS_MAX_KM


def crossing_penalty(world: World, origin: int, target: int, sea_km: float) -> float:
    """Attack multiplier for going from origin into target: amphibious landing, river crossing or none."""
    if sea_km:
        return amphibious_penalty(sea_km)
    rank = world.provinces[origin].river_rank(target)
    if rank is None:
        return 1.0
    return MAJOR_RIVER_CROSSING if rank <= MAJOR_RIVER_MAX_SCALERANK else RIVER_CROSSING


def ground_power(country: Country) -> float:
    return country.oob.branch_power(Branch.LAND) + INFANTRY_POWER_PER_1000 * country.oob.active_personnel / 1000


@dataclass
class Deployment:
    stationed: dict[int, float] = field(default_factory=dict)            # province -> power holding it
    attacks: dict[int, tuple[int, float, float]] = field(default_factory=dict)  # target -> (origin, power, sea km or 0)
    effectiveness: float = 1.0
    personnel_per_power: float = 0.0


class LandWarfare:
    def __init__(self) -> None:
        self.deployments: dict[str, Deployment] = {}
        self.fortification: dict[int, float] = {}
        self.encircled: set[int] = set()
        self.casualties_today: dict[str, float] = {}
        self._carry: dict[tuple[str, str], float] = {}
        self._equipment_carry: dict[tuple[str, str], float] = {}
        self._equipment_baseline: dict[tuple[str, str], int] = {}
        self._wars_seen: set[str] = set()
        self._blockaded: set[str] = set()

    # --- relationships -------------------------------------------------------------------------

    @staticmethod
    def _sides(wars: list[War]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        enemies: dict[str, set[str]] = {}
        allies: dict[str, set[str]] = {}
        for war in wars:
            for tag in war.participants:
                enemies.setdefault(tag, set()).update(war.enemies_of(tag))
                allies.setdefault(tag, set()).update(war.tags_on(war.side_of(tag)))
        return enemies, allies

    @staticmethod
    def _war_between(wars: list[War], a: str, b: str) -> War | None:
        for war in wars:
            if a in war.participants and b in war.participants and war.side_of(a) is not war.side_of(b):
                return war
        return None

    # --- daily ---------------------------------------------------------------------------------

    def daily(self, sim: Simulation) -> None:
        world, wars = sim.world, sim.active_wars
        enemies, allies = self._sides(wars)
        self._apply_attrition(world)
        self._blockade(world, wars)
        if not wars:
            self.deployments.clear()
            world.contested.clear()
            return
        for war in wars:
            if war.id not in self._wars_seen:
                self._wars_seen.add(war.id)
                for tag in war.participants:
                    for p in self._front(world, tag, enemies[tag]):
                        self.fortification[p] = max(self.fortification.get(p, 0.0), ESTABLISHED_FRONT_FORTIFICATION)
        self._encirclement(world, wars, enemies, allies)
        self._fortify(world, enemies)
        for tag in list(self.deployments):
            if tag not in enemies:
                del self.deployments[tag]
        for tag in [t for t in sorted(enemies) if t not in self.deployments]:
            self._plan(world, wars, tag, enemies, allies, sim.clock.hours_elapsed)  # Deploy first, so plans see each other.
        for tag in sorted(enemies):
            self._plan(world, wars, tag, enemies, allies, sim.clock.hours_elapsed)
        for pid in list(world.contested):
            if not any(pid in d.attacks for d in self.deployments.values()):
                del world.contested[pid]  # Nobody is pressing it any more.

    @staticmethod
    def _front(world: World, tag: str, foes: set[str]) -> list[int]:
        return sorted(p.id for p in world.controlled_by(tag)
                      if any(world.provinces[n].controller in foes for n in p.neighbors))

    @staticmethod
    def _branch(world: World, tags: set[str], branch: Branch) -> float:
        return sum(world.country(t).oob.branch_power(branch) for t in sorted(tags) if t in world.countries)

    def _commitment(self, wars: list[War], tag: str) -> float:
        best = 0.0
        for war in wars:
            p = war.participants.get(tag)
            if p is None:
                continue
            if p.role is ParticipantRole.CO_BELLIGERENT:
                share = COMMITMENT_CO_BELLIGERENT
            elif p.side is Side.DEFENDER:
                share = COMMITMENT_EXISTENTIAL_DEFENCE if war.is_existential_for(tag) else COMMITMENT_DEFENCE
            else:
                share = COMMITMENT_ATTACK.get(p.motivation.name, 0.6)
            best = max(best, share)
        return best

    def _offensive_share(self, wars: list[War], tag: str) -> float:
        share = 0.0
        for war in wars:
            p = war.participants.get(tag)
            if p is None:
                continue
            if p.side is Side.ATTACKER or p.claim is not None:
                s = OFFENSIVE_SHARE_HALTED if p.offensive_halted else OFFENSIVE_SHARE.get(p.motivation.name, 0.35)
            else:
                s = COUNTERATTACK_SHARE if p.resolve >= COUNTERATTACK_MIN_RESOLVE else 0.0
            share = max(share, s)
        return share

    def _effectiveness(self, world: World, wars: list[War], tag: str, enemies: dict[str, set[str]],
                       allies: dict[str, set[str]], now_hour: int) -> float:
        country = world.country(tag)
        morale = 0.85 + 0.3 * country.spirit.effective_war_support(now_hour)
        for war in wars:
            if tag in war.participants:
                morale += war.participants[tag].motivation.morale_bonus
                break
        own_air = self._branch(world, allies[tag], Branch.AIR)
        foe_air = self._branch(world, enemies[tag], Branch.AIR)
        share = own_air / (own_air + foe_air) if own_air + foe_air > 0 else 0.5
        return country.logistics.combat_effectiveness() * morale * (1 + AIR_EFFECT * (share - 0.5))

    def _relevance(self, world: World, wars: list[War], tag: str, target: int, capital_hops: dict[str, dict[int, int]]) -> float:
        q = world.provinces[target]
        war = self._war_between(wars, tag, q.controller)
        if war is None:
            return 0.0
        p = war.participants[tag]
        if p.claim is not None:
            return 4.0 if target in p.claim.province_ids else 0.2
        goal = war.goal
        if p.side is Side.DEFENDER:
            return 3.0 if world.provinces[target].owner in war.tags_on(p.side) else 0.5
        if goal.type in PROVINCE_GOALS:
            if target in goal.province_ids:
                return 4.0
            return 2.0 if any(n in goal.province_ids for n in q.neighbors) else 0.3
        if goal.type in EXISTENTIAL_GOALS:
            if target in goal.province_ids:
                return 4.0  # Stated territorial demands (Russia's four annexed oblasts).
            hops = capital_hops.get(goal.target, {}).get(target)
            return 1.0 + (3.0 / (1 + hops) if hops is not None else 0.0)
        return 0.3  # Coercion: prefer blockade and strikes to ground offensives.

    def _capital_hops(self, world: World, wars: list[War]) -> dict[str, dict[int, int]]:
        out: dict[str, dict[int, int]] = {}
        for war in wars:
            target = war.goal.target
            if war.goal.type in EXISTENTIAL_GOALS and target in world.countries and target not in out:
                start = world.country(target).capital_province_id
                dist = {start: 0}
                queue = deque([start])
                while queue:
                    pid = queue.popleft()
                    if dist[pid] >= 30:
                        continue
                    for n in world.provinces[pid].neighbors:
                        if n not in dist:
                            dist[n] = dist[pid] + 1
                            queue.append(n)
                out[target] = dist
        return out

    def _plan(self, world: World, wars: list[War], tag: str, enemies: dict[str, set[str]], allies: dict[str, set[str]],
              now_hour: int) -> None:
        country = world.country(tag)
        foes = enemies[tag]
        friends = allies[tag]
        pool = ground_power(country)
        dep = self.deployments.setdefault(tag, Deployment())
        dep.effectiveness = self._effectiveness(world, wars, tag, enemies, allies, now_hour)
        dep.personnel_per_power = country.oob.active_personnel / pool if pool > 0 else 0.0
        power = pool * self._commitment(wars, tag)

        # Only provinces with a supply line home can stage forces (not Transnistria, cut off from Russia).
        stations = [pid for pid in self._front(world, tag, foes) if pid not in self.encircled]
        efficiency = 1.0
        naval_edge = self._branch(world, friends, Branch.NAVAL) >= NAVAL_SUPERIORITY * max(self._branch(world, foes, Branch.NAVAL), 1.0)
        amphibious: list[tuple[int, int, int]] = []
        if naval_edge:
            for p in world.controlled_by(tag):
                if p.coastal and p.id not in self.encircled:
                    amphibious.extend((p.id, q, km) for q, km in p.sea_links
                                      if km <= AMPHIBIOUS_MAX_KM and world.provinces[q].controller in foes)
        if not stations:
            stations = sorted({p.id for ally in friends - {tag} for p in world.controlled_by(ally)
                               if any(world.provinces[n].controller in foes for n in p.neighbors)})
            efficiency = EXPEDITIONARY_EFFICIENCY
        if not stations and not amphibious:
            dep.stationed.clear()
            dep.attacks.clear()
            return
        power *= efficiency

        # Assault targets: enemy provinces next to our stations (or reachable by sea), best value per defender.
        offensive = power * self._offensive_share(wars, tag)
        hops = self._capital_hops(world, wars)
        defenders_at: dict[int, float] = {}
        for other, d in self.deployments.items():
            for pid, s in d.stationed.items():
                defenders_at[pid] = defenders_at.get(pid, 0.0) + s
        candidates: dict[int, tuple[float, int, float]] = {}
        routes = [(pid, n, 0.0) for pid in stations for n in world.provinces[pid].neighbors
                  if world.provinces[n].controller in foes]
        routes += [(origin, q, float(max(km, 1))) for origin, q, km in amphibious]  # 0 would read as a land route.
        for origin, q, km in routes:
            score = world.provinces[q].strategic_value() * self._relevance(world, wars, tag, q, hops)
            score *= crossing_penalty(world, origin, q, km)  # Prefer the dry-shod route.
            score /= 1.0 + defenders_at.get(q, 0.0) / (power + 1.0)
            if score > candidates.get(q, (0.0, 0, 0.0))[0]:
                candidates[q] = (score, origin, km)
        chosen = sorted(candidates.items(), key=lambda kv: (-kv[1][0], kv[0]))[:MAX_TARGETS]
        dep.attacks = {}
        # Concentrate: call off the assault least likely to succeed and give its troops to the rest,
        # until every remaining one is expected to go in at MIN_ASSAULT_RATIO or better.
        while chosen and offensive > 0:
            total = sum(s * s for _, (s, _, _) in chosen)
            alloc = {q: offensive * s * s / total for q, (s, _, _) in chosen}
            expected = {q: alloc[q] * dep.effectiveness * crossing_penalty(world, origin, q, km)
                        / max(self._defence(world, q, foes)[0], 1e-6) for q, (_, origin, km) in chosen}
            worst = min(chosen, key=lambda kv: (expected[kv[0]], kv[0]))[0]
            if expected[worst] >= MIN_ASSAULT_RATIO:
                dep.attacks = {q: (origin, alloc[q], km) for q, (_, origin, km) in chosen}
                break
            chosen = [kv for kv in chosen if kv[0] != worst]

        # Whatever isn't attacking holds the line, weighted towards valuable and threatened provinces.
        # Threat: enemy troops next door, and enemy assaults already coming in (as of yesterday).
        enemy_power_at: dict[int, float] = {}
        incoming: dict[int, float] = {}
        for foe in sorted(foes & set(self.deployments)):
            for pid, s in self.deployments[foe].stationed.items():
                enemy_power_at[pid] = enemy_power_at.get(pid, 0.0) + s
            for pid, (_, s, _) in self.deployments[foe].attacks.items():
                incoming[pid] = incoming.get(pid, 0.0) + s
        weights = {}
        for pid in stations:
            prov = world.provinces[pid]
            threat = sum(enemy_power_at.get(n, 0.0) for n in prov.neighbors) + incoming.get(pid, 0.0)
            weights[pid] = prov.strategic_value() * (1.0 + 3.0 * threat / (power + 1.0))
        wsum = sum(weights.values()) or 1.0
        defensive = power - sum(a for _, a, _ in dep.attacks.values())
        target_station = {pid: defensive * w / wsum for pid, w in weights.items()}

        if not dep.stationed:  # First day: forces are already in place.
            dep.stationed = dict(target_station)
        else:
            merged = {}
            for pid in set(dep.stationed) | set(target_station):
                old, new = dep.stationed.get(pid, 0.0), target_station.get(pid, 0.0)
                value = old if pid in self.encircled else old + (new - old) * REDEPLOY_RATE  # Pockets can't be reinforced.
                if value > 1e-3 and (pid in target_station or world.provinces[pid].controller in friends):
                    merged[pid] = value
            dep.stationed = merged

    def _encirclement(self, world: World, wars: list[War], enemies: dict[str, set[str]], allies: dict[str, set[str]]) -> None:
        self.encircled = set()
        for tag in sorted(enemies):
            friends = allies[tag]
            country = world.country(tag)
            roots = [pid for pid in (country.capital_province_id, country.government_seat_id)
                     if pid is not None and world.provinces[pid].controller in friends]
            if not roots:
                continue
            sea_supplied = self._branch(world, friends, Branch.NAVAL) >= self._branch(world, enemies[tag], Branch.NAVAL)
            reached = set(roots)
            queue = deque(roots)
            while queue:
                prov = world.provinces[queue.popleft()]
                links = list(prov.neighbors) + [q for q, km in prov.sea_links if km <= BRIDGE_KM]
                for n in links:
                    if n not in reached and world.provinces[n].controller in friends:
                        reached.add(n)
                        queue.append(n)
            for p in world.controlled_by(tag):
                if p.id not in reached and not (sea_supplied and p.coastal):
                    self.encircled.add(p.id)

    def _fortify(self, world: World, enemies: dict[str, set[str]]) -> None:
        front = {pid for tag, foes in enemies.items() for pid in self._front(world, tag, foes)}
        for pid in list(self.fortification):
            if pid not in front:
                self.fortification[pid] = max(0.0, self.fortification[pid] - FORTIFICATION_DECAY)
        for pid in front:
            self.fortification[pid] = min(FORTIFICATION_MAX, self.fortification.get(pid, 0.0) + FORTIFICATION_GROWTH)

    def _blockade(self, world: World, wars: list[War]) -> None:
        levels: dict[str, float] = {}
        for war in wars:
            for side in (Side.ATTACKER, Side.DEFENDER):
                own = self._branch(world, set(war.tags_on(side)), Branch.NAVAL)
                foe = self._branch(world, set(war.tags_on(side.opposite)), Branch.NAVAL)
                ratio = foe / max(own, 1.0)
                if ratio < BLOCKADE_MIN_RATIO:
                    continue
                level = min(BLOCKADE_MAX, BLOCKADE_BASE + BLOCKADE_PER_RATIO * (ratio - BLOCKADE_MIN_RATIO))
                for tag in war.tags_on(side):
                    levels[tag] = max(levels.get(tag, 0.0), level)
        for tag in self._blockaded - set(levels):
            if tag in world.countries:
                world.country(tag).blockade_interdiction = 0.0
        for tag, level in levels.items():
            world.country(tag).blockade_interdiction = level
        self._blockaded = set(levels)

    def _apply_attrition(self, world: World) -> None:
        for tag, cas in sorted(self.casualties_today.items()):
            if tag not in world.countries or cas <= 0:
                continue
            oob = world.country(tag).oob
            share = cas / max(oob.active_personnel + cas, 1.0)
            factor = world.country(tag).production_factor(world)
            for name, stock in oob.equipment.items():
                if stock.branch is not Branch.LAND:
                    continue
                base = self._equipment_baseline.setdefault((tag, name), stock.quantity)
                change = (-stock.quantity * share * EQUIPMENT_LOSS_PER_CASUALTY_SHARE
                          + base * EQUIPMENT_PRODUCTION_PER_DAY * factor + self._equipment_carry.get((tag, name), 0.0))
                whole = math.trunc(change)
                stock.quantity = max(0, stock.quantity + whole)
                self._equipment_carry[(tag, name)] = change - whole
            # Replacements: reserves first, then fresh conscripts from the mobilisable pool.
            want = min(cas, MAX_REPLACEMENT_SHARE_PER_DAY * max(oob.active_personnel, 1))
            from_reserve = min(oob.reserve_personnel, int(want))
            oob.reserve_personnel -= from_reserve
            pool = max(0, oob.mobilizable_manpower - oob.active_personnel - oob.reserve_personnel - oob.casualties_total)
            oob.active_personnel += from_reserve + min(pool, int(want) - from_reserve)
        self.casualties_today.clear()

    # --- hourly --------------------------------------------------------------------------------

    def hourly(self, sim: Simulation) -> None:
        world, wars = sim.world, sim.active_wars
        if not wars:
            return
        enemies, allies = self._sides(wars)
        assaults: dict[int, dict[str, tuple[float, float]]] = {}  # target -> tag -> (strength, personnel)
        for tag, dep in sorted(self.deployments.items()):
            if tag not in enemies:
                continue
            for target, (origin, power, km) in dep.attacks.items():
                if world.provinces[target].controller not in enemies[tag]:
                    continue
                if world.provinces[origin].controller not in allies[tag]:
                    continue
                strength, personnel = assaults.setdefault(target, {}).get(tag, (0.0, 0.0))
                assaults[target][tag] = (strength + power * dep.effectiveness * crossing_penalty(world, origin, target, km),
                                         personnel + power * dep.personnel_per_power)
        for target, attackers in sorted(assaults.items()):
            self._fight(world, wars, target, attackers, allies)

    def _defence(self, world: World, target: int, side: set[str]) -> tuple[float, dict[str, float]]:
        """Defensive strength of a province held by `side`, and each defender's effective power in it."""
        prov = world.provinces[target]
        defenders: dict[str, float] = {}
        for tag in sorted(side):
            dep = self.deployments.get(tag)
            if dep and target in dep.stationed:
                defenders[tag] = dep.stationed[target] * dep.effectiveness
        local = LOCAL_DEFENCE_BASE + LOCAL_DEFENCE_PER_100K * prov.population / 100_000
        defence = (sum(defenders.values()) + local) * prov.terrain_profile.defense_multiplier
        defence *= 1.0 + self.fortification.get(target, 0.0)
        if target in self.encircled:
            defence *= ENCIRCLED_DEFENCE
        return defence, defenders

    def _fight(self, world: World, wars: list[War], target: int, attackers: dict[str, tuple[float, float]],
               allies: dict[str, set[str]]) -> None:
        prov = world.provinces[target]
        holder = prov.controller
        lead = max(sorted(attackers), key=lambda t: attackers[t][0])
        defence, defenders = self._defence(world, target, allies.get(holder, {holder}))
        pocket = target in self.encircled
        attack = sum(strength for strength, _ in attackers.values())
        ratio = attack / max(defence, 1e-6)

        advance = 0.0
        if ratio > 1.0:
            advance = min(MAX_ADVANCE_KM2_PER_DAY, ADVANCE_SCALE * (ratio - 1.0) ** ADVANCE_EXPONENT)
            advance /= prov.terrain_profile.movement_cost
        _, progress = world.contested.get(target, (lead, 0.0))
        progress += advance / 24.0 / max(prov.area_km2, 1.0)
        world.contested[target] = (lead, min(progress, 1.0))

        # Casualties this hour. Attackers bleed more against strong defences; defenders in contact are
        # limited by the attackers' frontage and protected by terrain and fortification.
        bounded = clamp(ratio, 0.2, 5.0)
        protection = prov.terrain_profile.defense_multiplier * (1.0 + self.fortification.get(target, 0.0))
        attacking_personnel = 0.0
        for tag, (_, personnel) in attackers.items():
            attacking_personnel += personnel
            self._casualties(world, wars, tag, holder, CASUALTY_RATE / 24.0 * personnel / math.sqrt(bounded))
        stationed = {tag: s / max(self.deployments[tag].effectiveness, 1e-6) * self.deployments[tag].personnel_per_power
                     for tag, s in defenders.items()}
        in_contact = min(sum(stationed.values()), attacking_personnel / DEFENDER_FRONTAGE)
        total_stationed = sum(stationed.values()) or 1.0
        rate = CASUALTY_RATE / 24.0 * math.sqrt(bounded) / protection * (ENCIRCLED_CASUALTIES if pocket else 1.0)
        for tag, personnel in stationed.items():
            self._casualties(world, wars, tag, lead, rate * in_contact * personnel / total_stationed)

        if progress >= 1.0:
            world.set_controller(target, lead)
            self.fortification[target] = 0.0
            prov.damage = min(1.0, prov.damage + (CAPTURE_DAMAGE_URBAN if ProvinceTag.URBAN_CENTER in prov.tags else CAPTURE_DAMAGE))
            for tag in defenders:
                self.deployments[tag].stationed.pop(target, None)

    def _casualties(self, world: World, wars: list[War], tag: str, opponent: str, amount: float) -> None:
        war = self._war_between(wars, tag, opponent)
        if war is None or tag not in world.countries:
            return
        key = (war.id, tag)
        total = self._carry.get(key, 0.0) + amount
        whole = int(total)
        self._carry[key] = total - whole
        if whole:
            war.record_casualties(world, tag, whole)
            self.casualties_today[tag] = self.casualties_today.get(tag, 0.0) + whole
