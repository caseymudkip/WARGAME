"""Land warfare: fronts, force allocation, hourly combat, encirclement and naval blockade.

Abstraction. A country's ground forces are a pool of combat power: land equipment with units
(stored equipment counts only once refurbished) weighted by quality and combat value, plus
infantry. Every day a planner spreads that pool over the provinces it holds against the enemy and
picks which enemy provinces to assault; every hour each assaulted province is fought over.

  planning     a share of the committed force attacks (motivation sets how much; a defender that
               comes to outnumber the invader counterattacks), the rest holds the line, weighted to
               valuable and threatened provinces. Assaults expected to go in below MIN_ASSAULT_RATIO
               are called off and their troops given to the others: armies concentrate.
  ratio R      attack x effectiveness x crossing (river, amphibious) x operational reach x surprise /
               (defenders x effectiveness + local defence) x terrain x (1 + fortification)
               [x 0.5 in a pocket]
  advance      depth x frontage. Depth (km/day) = 1.2 x (R - 1)^2 up to 40: Dupuy's WW2 division
               rates at R ~2.3, mechanised exploitation at R 7+. It is cut by terrain, fieldworks and,
               most of all, a defender's drone-watched kill zone. Frontage is the border with the
               attacker's ground, limited by the troops attacking (wider in pursuit).
  casualties   a share of the engaged personnel per day; attackers bleed more when R is low,
               defenders when it is high, pockets far more.

The same rules produce both paces of the war in Ukraine (tools/calibration/ukraine_2025.py):
  2022, from the 2021 map: ~97,000 km2 held after 36 days (real ~165,000), Kyiv holds. Surprise,
       open borders, an unmobilised defender, attacks from Belarus.
  2025, from the 2026 front: 11.1 km2/day (DeepState 11.9), ~1,170 Russian casualties/day
       (UK MoD 1,137), Ukrainian losses 0.46x (CSIS 0.42-0.5). A fortified, drone-saturated front.

Also here because they are daily force-level effects: encirclement, fortification of static fronts,
operational reach, mobilisation, equipment attrition and refurbishment, and naval blockade from
fleet ratios (writes Country.blockade_interdiction, which cuts seaborne imports).
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
# Share of ground power (mostly equipment) sent against the enemy. Russia went in with ~120 of ~170
# battalion tactical groups in February 2022, most of its ready equipment though only ~190,000 men;
# in 2025 ~700,000 of 1.32 million. Escalation after that comes from mobilisation and from refurbishing
# stored equipment, not from a bigger share.
COMMITMENT_ATTACK = {"realistic_cautious": 0.45, "epic_aggressive": 0.6}
COMMITMENT_CO_BELLIGERENT = 0.5
EXPEDITIONARY_EFFICIENCY = 0.5         # Forces fighting from an ally's territory.
REDEPLOY_RATE = 0.25                   # Share of the gap to the planned deployment closed per day.
PEACETIME_LINE_WEIGHT = 20.0           # x pre-war fortification: how heavily a peacetime army mans its lines.

# --- offensives -------------------------------------------------------------------------------
OFFENSIVE_SHARE = {"realistic_cautious": 0.35, "epic_aggressive": 0.5}
OFFENSIVE_SHARE_HALTED = 0.1
COUNTERATTACK_SHARE = 0.15
COUNTERATTACK_MIN_RESOLVE = 0.5
# A defender that comes to outnumber the invader goes over to the offensive (Kharkiv and Kherson,
# autumn 2022): from COUNTERATTACK_SHARE at 1.2x the enemy's committed power to a full offensive at 2x.
COUNTEROFFENSIVE_FROM = 1.2
COUNTEROFFENSIVE_FULL = 2.0
MAX_TARGETS = 6
MIN_ASSAULT_RATIO = 1.1                # Planners don't send troops into assaults they expect to lose.

# --- combat --------------------------------------------------------------------------------------
# Advance = depth (km/day) x frontage (km). Depth follows the force ratio, on the scale of Dupuy's
# division-level data (WW2 opposed attacks: 1.8 km/day in the West, 4.5 in the East, 4-11 once
# through the line), and is cut by terrain, fieldworks and a defender's drone-watched kill zone.
DEPTH_KM_AT_R2 = 1.2                   # km/day at R = 2: open ground, no works, no drones.
DEPTH_EXPONENT = 2.0                   # R 1.5: 0.3 km/day; R 3: 4.8; R 5+: exploitation.
MAX_DEPTH_KM_PER_DAY = 40.0            # Mechanised exploitation (3rd ID to Baghdad 2003 averaged ~25).
ATTACK_PERSONNEL_PER_KM = 1_500        # Attack frontage: a ~15,000-strong division on ~10 km.
AMPHIBIOUS_FRONTAGE_KM = 20.0          # A beachhead.
FORTIFICATION_ADVANCE_DRAG = 2.5       # Dupuy: advance rates vary inversely with fortification.
DRONE_ADVANCE_DRAG = 0.88             # Full saturation: the 2025 front, slower than the Somme (CSIS).
CASUALTY_RATE = 0.004                  # Share of engaged personnel lost per day at R = 1.
DEFENDER_FRONTAGE = 2.5                # Defenders in contact: at most attackers / 2.5 (holding takes fewer troops).
ENCIRCLED_DEFENCE = 0.5
ENCIRCLED_CASUALTIES = 2.0
LOCAL_DEFENCE_BASE = 5.0               # Territorial defence even where no army stands...
LOCAL_DEFENCE_PER_100K = 0.5           # ...growing with the population defending its home.
CAPTURE_DAMAGE = 0.15
CAPTURE_DAMAGE_URBAN = 0.3
AIR_EFFECT = 0.2                       # Full air dominance: +/-10% to ground combat.
# Surprise (Dupuy, QJM): x2.24 combat power for complete surprise, x1.10 for minor, lasting three days and
# losing a third each day. A defender not yet on a war footing suffers substantial surprise (warned but
# not mobilised: Ukraine, 24 February 2022).
SURPRISE_UNMOBILISED = 1.6
SURPRISE_DAYS = 3
HOME_SOIL_MORALE = 0.2                 # x patriotism (share willing to fight): defenders fight for home.

# --- rivers ------------------------------------------------------------------------------------------
# Attack multiplier for assaults across a river border. An opposed crossing of a major river is among
# the hardest operations there is: Ukraine's Dnipro bridgehead at Krynky (Oct 2023 - Jul 2024) never broke out.
MAJOR_RIVER_MAX_SCALERANK = 4          # Natural Earth: Dnipro, Rhine, Oder 4; Danube 2; Vistula 5; Don 6.
MAJOR_RIVER_CROSSING = 0.5
RIVER_CROSSING = 0.7

# --- fortification -------------------------------------------------------------------------------
FORTIFICATION_GROWTH = 0.004          # A static front is fully dug in after ~5 months (the Surovikin line).
FORTIFICATION_DECAY = 0.01
FORTIFICATION_MAX = 0.6                # QJM's "fortified" posture: x1.6.
# Lines dug before the war (the Korean DMZ, the 2015-2022 Donbas line) come from World.fortified_lines.

# --- naval ----------------------------------------------------------------------------------------
NAVAL_SUPERIORITY = 1.5                # Needed for amphibious assaults and sea-supplied pockets.
AMPHIBIOUS_MAX_KM = 250
BRIDGE_KM = 10                         # Shorter sea links count as land for supply (Kerch bridge).
FRIENDLY_SUPPLY_RELATION = 0.5         # A neutral neighbour this friendly keeps a cut-off region supplied.
BLOCKADE_MIN_RATIO = 2.0
BLOCKADE_BASE = 0.2
BLOCKADE_PER_RATIO = 0.15
BLOCKADE_MAX = 0.8

# --- attrition --------------------------------------------------------------------------------------
EQUIPMENT_LOSS_PER_CASUALTY_SHARE = 0.6
EQUIPMENT_PRODUCTION_PER_DAY = 0.0004  # Share of pre-war stock produced or refurbished per day at full industry.
MAX_REPLACEMENT_SHARE_PER_DAY = 0.003  # Replacements per day as a share of active strength.
REACTIVATION_PER_DAY = 0.0005          # Stored equipment refurbished per day at full industry: Russia
                                       # drew ~7,300 stored tanks down to ~3,500 in 2022-24 (IISS).

# --- operational reach ----------------------------------------------------------------------------------
# Armies run on supply lines. Russia's 2022 columns, tied to railheads, culminated ~100 km in. An assault
# launched from ground taken less than CONSOLIDATION_DAYS ago (rail not yet restored) loses strength per
# province of distance from the consolidated rear: owned soil, a host's soil, or ground held long enough.
REACH_PER_HOP = 0.6
CONSOLIDATION_DAYS = 90

# --- mobilisation ------------------------------------------------------------------------------------
# Calling up reserves and volunteers, as a share of pre-war active strength. Ukraine 2022: ~250,000 to
# ~700,000 by May (Zelensky). Countries already on a war footing (Country.mobilised) only replace losses.
MOBILISATION_RATE_EXISTENTIAL = 0.02
MOBILISATION_CEILING_EXISTENTIAL = 3.5
MOBILISATION_RATE = 0.003
MOBILISATION_CEILING = 1.3


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
        self._carry: dict[tuple[str, str, bool], float] = {}
        self._equipment_carry: dict[tuple[str, str], float] = {}
        self._equipment_baseline: dict[tuple[str, str], int] = {}
        self._wars_seen: set[str] = set()
        self._prewar_active: dict[str, int] = {}
        self._taken_hour: dict[int, int] = {}            # province -> hour it last changed hands in combat
        self.reach: dict[str, dict[int, int]] = {}       # tag -> province -> hops from its consolidated rear
        self._hosts: dict[str, set[str]] = {}            # tag -> countries currently letting it attack from their soil
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

    def _staging(self, world: World, tag: str, allies: dict[str, set[str]]) -> set[str]:
        """Countries whose soil `tag` can attack from: its side, and anyone hosting it."""
        return allies.get(tag, {tag}) | self._hosts.get(tag, set())

    def _update_hosts(self, world: World, wars: list[War], now_hour: int) -> None:
        self._hosts = {}
        for war in wars:
            for tag, part in war.participants.items():
                days = (now_hour - part.joined_hour) / 24.0
                for host, c in world.countries.items():
                    if tag in c.hosts and host not in war.participants and (c.hosting_days is None or days <= c.hosting_days):
                        self._hosts.setdefault(tag, set()).add(host)

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
            self._hosts.clear()
            world.contested.clear()
            return
        for war in wars:
            if war.id not in self._wars_seen:
                self._wars_seen.add(war.id)
                self._dig_in_prewar_lines(world, war)
        self._mobilise(world, wars)
        self._update_hosts(world, wars, sim.clock.hours_elapsed)
        self._encirclement(world, wars, enemies, allies)
        self._fortify(world, enemies)
        self._operational_reach(world, enemies, allies, sim.clock.hours_elapsed)
        for tag in list(self.deployments):
            if tag not in enemies:
                del self.deployments[tag]
        for tag in [t for t in sorted(enemies) if t not in self.deployments]:
            self._plan(world, wars, tag, enemies, allies, sim.clock.hours_elapsed)  # Deploy first, so plans see each other.
        for tag in sorted(enemies):
            self._plan(world, wars, tag, enemies, allies, sim.clock.hours_elapsed)
        for pid, (attacker, _) in list(world.contested.items()):
            if attacker not in enemies or world.provinces[pid].controller not in enemies[attacker]:
                del world.contested[pid]  # That war is over; ground taken but unpressed otherwise stays held.

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

    def _offensive_share(self, wars: list[War], tag: str, power: float, foes: set[str]) -> float:
        share = 0.0
        for war in wars:
            p = war.participants.get(tag)
            if p is None:
                continue
            full = OFFENSIVE_SHARE.get(p.motivation.name, 0.35)
            if p.side is Side.ATTACKER or p.claim is not None:
                s = OFFENSIVE_SHARE_HALTED if p.offensive_halted else full
            elif p.resolve >= COUNTERATTACK_MIN_RESOLVE:
                enemy = sum(sum(d.stationed.values()) + sum(a for _, a, _ in d.attacks.values())
                            for t, d in self.deployments.items() if t in foes)
                edge = clamp((power / max(enemy, 1.0) - COUNTEROFFENSIVE_FROM) / (COUNTEROFFENSIVE_FULL - COUNTEROFFENSIVE_FROM))
                s = COUNTERATTACK_SHARE + (max(full, COUNTERATTACK_SHARE) - COUNTERATTACK_SHARE) * edge
            else:
                s = 0.0
            share = max(share, s)
        return share

    def _effectiveness(self, world: World, wars: list[War], tag: str, enemies: dict[str, set[str]],
                       allies: dict[str, set[str]], now_hour: int) -> float:
        country = world.country(tag)
        morale = 0.85 + 0.3 * country.spirit.effective_war_support(now_hour)
        for war in wars:
            if tag in war.participants:
                morale += war.participants[tag].motivation.morale_bonus
                if war.participants[tag].side is Side.DEFENDER:
                    morale += HOME_SOIL_MORALE * country.spirit.patriotism
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

        # Only provinces with a supply line home can stage forces (not Transnistria, cut off from Russia),
        # plus the soil of countries that let us attack from it (Belarus, February 2022).
        # A border with a country hosting the enemy is a front too (Ukraine's border with Belarus, 2022).
        hostile_soil = foes | {h for foe in foes for h in self._hosts.get(foe, set())}
        stations = [pid for pid in self._front(world, tag, hostile_soil) if pid not in self.encircled]
        for host in sorted(self._hosts.get(tag, set()) - foes):
            stations += [pid for pid in self._front(world, host, foes) if pid not in stations]
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
        offensive = power * self._offensive_share(wars, tag, power, foes)
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
            score *= crossing_penalty(world, origin, q, km) * self._reach_factor(tag, origin)  # Dry-shod, well supplied.
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
            expected = {q: alloc[q] * dep.effectiveness * crossing_penalty(world, origin, q, km) * self._reach_factor(tag, origin)
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
        # A country caught at peace (not mobilised) starts in its peacetime posture: garrisons by value,
        # massed on lines it already held dug in (Ukraine, February 2022: the Donbas, not the south or north).
        surprised = not dep.stationed and not country.mobilised
        weights = {}
        for pid in stations:
            prov = world.provinces[pid]
            if surprised:
                weights[pid] = prov.strategic_value() * (1.0 + PEACETIME_LINE_WEIGHT * self.fortification.get(pid, 0.0))
                continue
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

    def _dig_in_prewar_lines(self, world: World, war: War) -> None:
        for line in world.fortified_lines:
            a, b = line.between
            if a not in war.participants or b not in war.participants or war.side_of(a) is war.side_of(b):
                continue
            for tag in sorted(line.fortify):
                other = b if tag == a else a
                for p in world.controlled_by(tag):
                    if line.provinces is not None and p.id not in line.provinces:
                        continue
                    if any(world.provinces[n].controller == other for n in p.neighbors):
                        self.fortification[p.id] = max(self.fortification.get(p.id, 0.0), line.level)

    def _mobilise(self, world: World, wars: list[War]) -> None:
        for war in wars:
            for tag, part in sorted(war.participants.items()):
                country = world.country(tag)
                prewar = self._prewar_active.setdefault(tag, country.oob.active_personnel)
                if country.mobilised:
                    continue
                existential = part.side is Side.DEFENDER and war.is_existential_for(tag)
                rate = MOBILISATION_RATE_EXISTENTIAL if existential else MOBILISATION_RATE
                ceiling = MOBILISATION_CEILING_EXISTENTIAL if existential else MOBILISATION_CEILING
                oob = country.oob
                want = min(int(rate * prewar), int(ceiling * prewar) - oob.active_personnel)
                if want <= 0:
                    continue
                from_reserve = min(oob.reserve_personnel, want)
                pool = max(0, oob.mobilizable_manpower - oob.active_personnel - oob.reserve_personnel - oob.casualties_total)
                oob.reserve_personnel -= from_reserve
                oob.active_personnel += from_reserve + min(pool, want - from_reserve)

    def _operational_reach(self, world: World, enemies: dict[str, set[str]], allies: dict[str, set[str]], now_hour: int) -> None:
        self.reach = {}
        consolidated = now_hour - CONSOLIDATION_DAYS * 24
        for tag in sorted(enemies):
            staging = self._staging(world, tag, allies)
            rear = [p.id for t in sorted(staging) for p in world.controlled_by(t)
                    if p.owner in staging or self._taken_hour.get(p.id, consolidated) <= consolidated]
            hops = {pid: 0 for pid in rear}
            queue = deque(rear)
            while queue:
                pid = queue.popleft()
                for n in world.provinces[pid].neighbors:
                    if n not in hops and world.provinces[n].controller in staging:
                        hops[n] = hops[pid] + 1
                        queue.append(n)
            self.reach[tag] = hops

    def _reach_factor(self, tag: str, origin: int) -> float:
        return REACH_PER_HOP ** self.reach.get(tag, {}).get(origin, 0)

    def _encirclement(self, world: World, wars: list[War], enemies: dict[str, set[str]], allies: dict[str, set[str]]) -> None:
        """A pocket is ground cut off from the country's main body and from friendly borders.

        Supply comes from the largest connected body of held territory (a surrounded capital is the
        pocket, not the rest of the country, as Sarajevo was) and across the borders of friendly
        neutral states (Western aid into Ukraine through Poland and Romania)."""
        self.encircled = set()
        for tag in sorted(enemies):
            friends = allies[tag]
            held = {p.id for t in friends for p in world.controlled_by(t)}
            components: list[set[int]] = []
            seen: set[int] = set()
            for start in sorted(held):
                if start in seen:
                    continue
                part = {start}
                queue = deque([start])
                while queue:
                    prov = world.provinces[queue.popleft()]
                    links = list(prov.neighbors) + [q for q, km in prov.sea_links if km <= BRIDGE_KM]
                    for n in links:
                        if n in held and n not in part:
                            part.add(n)
                            queue.append(n)
                seen |= part
                components.append(part)
            if not components:
                continue
            main = max(components, key=lambda c: (sum(world.provinces[i].strategic_value() for i in c), -min(c)))
            sea_supplied = self._branch(world, friends, Branch.NAVAL) >= self._branch(world, enemies[tag], Branch.NAVAL)
            for part in components:
                if part is main:
                    continue
                if any(world.provinces[n].controller not in enemies[tag] | friends
                       and world.country(world.provinces[n].controller).relations.get(tag, 0.0) >= FRIENDLY_SUPPLY_RELATION
                       for i in part for n in world.provinces[i].neighbors if world.provinces[n].controller in world.countries):
                    continue  # Supplied across a friendly border.
                for pid in part:
                    if world.provinces[pid].controller == tag and not (sea_supplied and world.provinces[pid].coastal):
                        self.encircled.add(pid)

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
            country = world.country(tag)
            factor = country.production_factor(world) + country.aid_coverage  # Donated tanks, guns, vehicles.
            for name, stock in oob.equipment.items():
                if stock.branch is not Branch.LAND:
                    continue
                base = self._equipment_baseline.setdefault((tag, name), stock.quantity)
                refurbished = min(stock.stored, stock.stored * REACTIVATION_PER_DAY * min(factor, 1.0))
                change = (-stock.quantity * share * EQUIPMENT_LOSS_PER_CASUALTY_SHARE + refurbished
                          + base * EQUIPMENT_PRODUCTION_PER_DAY * factor + self._equipment_carry.get((tag, name), 0.0))
                whole = math.trunc(change)
                stock.stored -= min(stock.stored, max(0, round(refurbished)))
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
        assaults: dict[int, dict[str, tuple[float, float, float]]] = {}  # target -> tag -> (strength, personnel, beach km)
        for tag, dep in sorted(self.deployments.items()):
            if tag not in enemies:
                continue
            for target, (origin, power, km) in dep.attacks.items():
                if world.provinces[target].controller not in enemies[tag]:
                    continue
                if world.provinces[origin].controller not in self._staging(world, tag, allies):
                    continue
                strength, personnel, beach = assaults.setdefault(target, {}).get(tag, (0.0, 0.0, 0.0))
                assaults[target][tag] = (strength + power * dep.effectiveness * crossing_penalty(world, origin, target, km)
                                         * self._reach_factor(tag, origin),
                                         personnel + power * dep.personnel_per_power,
                                         max(beach, AMPHIBIOUS_FRONTAGE_KM if km else 0.0))
        for target, attackers in sorted(assaults.items()):
            self._fight(world, wars, target, attackers, allies, sim.clock.hours_elapsed)

    def _surprise(self, world: World, wars: list[War], attacker: str, holder: str, now_hour: int) -> float:
        war = self._war_between(wars, attacker, holder)
        if war is None or holder not in world.countries or world.country(holder).mobilised:
            return 1.0
        day = int((now_hour - war.participants[holder].joined_hour) / 24)
        return 1.0 + (SURPRISE_UNMOBILISED - 1.0) * max(0.0, 1.0 - day / SURPRISE_DAYS)

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

    def _fight(self, world: World, wars: list[War], target: int, attackers: dict[str, tuple[float, float, float]],
               allies: dict[str, set[str]], now_hour: int) -> None:
        prov = world.provinces[target]
        holder = prov.controller
        lead = max(sorted(attackers), key=lambda t: attackers[t][0])
        defence, defenders = self._defence(world, target, allies.get(holder, {holder}))
        pocket = target in self.encircled
        attack = sum(strength for strength, _, _ in attackers.values()) * self._surprise(world, wars, lead, holder, now_hour)
        ratio = attack / max(defence, 1e-6)
        attacking_personnel = sum(personnel for _, personnel, _ in attackers.values())

        advance = 0.0  # km2/day
        if ratio > 1.0:
            fort = self.fortification.get(target, 0.0)
            drones = world.country(holder).drone_saturation if holder in world.countries else 0.0
            depth = min(MAX_DEPTH_KM_PER_DAY, DEPTH_KM_AT_R2 * (ratio - 1.0) ** DEPTH_EXPONENT)
            depth /= prov.terrain_profile.movement_cost * (1.0 + FORTIFICATION_ADVANCE_DRAG * fort)
            depth *= 1.0 - DRONE_ADVANCE_DRAG * drones
            side = self._staging(world, lead, allies)
            border = sum(prov.border_with(n) for n in prov.neighbors if world.provinces[n].controller in side)
            beach = max(b for _, _, b in attackers.values())
            # A weaker defence lets each formation sweep a wider zone (pursuit frontages).
            frontage = min(border + beach, attacking_personnel / ATTACK_PERSONNEL_PER_KM * max(1.0, ratio - 1.0))
            advance = depth * frontage
        _, progress = world.contested.get(target, (lead, 0.0))
        progress += advance / 24.0 / max(prov.area_km2, 1.0)
        world.contested[target] = (lead, min(progress, 1.0))

        # Casualties this hour. Attackers bleed more against strong defences; defenders in contact are
        # limited by the attackers' frontage and protected by terrain and fortification.
        bounded = clamp(ratio, 0.2, 5.0)
        protection = prov.terrain_profile.defense_multiplier * (1.0 + self.fortification.get(target, 0.0))
        for tag, (_, personnel, _) in attackers.items():
            self._casualties(world, wars, tag, holder, CASUALTY_RATE / 24.0 * personnel / math.sqrt(bounded), offensive=True)
        stationed = {tag: s / max(self.deployments[tag].effectiveness, 1e-6) * self.deployments[tag].personnel_per_power
                     for tag, s in defenders.items()}
        in_contact = min(sum(stationed.values()), attacking_personnel / DEFENDER_FRONTAGE)
        total_stationed = sum(stationed.values()) or 1.0
        rate = CASUALTY_RATE / 24.0 * math.sqrt(bounded) / protection * (ENCIRCLED_CASUALTIES if pocket else 1.0)
        for tag, personnel in stationed.items():
            self._casualties(world, wars, tag, lead, rate * in_contact * personnel / total_stationed, offensive=False)

        if progress >= 1.0:
            world.set_controller(target, lead)
            self._taken_hour[target] = now_hour
            self.fortification[target] = 0.0
            prov.damage = min(1.0, prov.damage + (CAPTURE_DAMAGE_URBAN if ProvinceTag.URBAN_CENTER in prov.tags else CAPTURE_DAMAGE))
            for tag in defenders:
                self.deployments[tag].stationed.pop(target, None)
            for tag in attackers:  # The assault force moves in and holds what it took.
                dep = self.deployments[tag]
                if target in dep.attacks:
                    _, power, _ = dep.attacks.pop(target)
                    dep.stationed[target] = dep.stationed.get(target, 0.0) + power

    def _casualties(self, world: World, wars: list[War], tag: str, opponent: str, amount: float, offensive: bool) -> None:
        war = self._war_between(wars, tag, opponent)
        if war is None or tag not in world.countries:
            return
        key = (war.id, tag, offensive)
        total = self._carry.get(key, 0.0) + amount
        whole = int(total)
        self._carry[key] = total - whole
        if whole:
            war.record_casualties(world, tag, whole, offensive)
            self.casualties_today[tag] = self.casualties_today.get(tag, 0.0) + whole
