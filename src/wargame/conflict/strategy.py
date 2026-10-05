"""Strategic decisions above the daily plan: when to go over to the offensive, and when to give ground up.

The land planner (conflict/land_warfare.py) decides each day where troops stand and which provinces to
assault. This module decides the things a general staff weighs over weeks, for any belligerent:

  posture      how much of the committed force goes on the attack. Attackers push with what their
               motivation allows. Defenders counterattack with a small share. They go over to the
               offensive where a feasible concentration gets ~2:1 against a thin sector, with no need
               for overall superiority: Ukraine at Kharkiv, September 2022, struck where Russian lines
               were thin while still outnumbered in tanks and guns overall. A defender that comes to
               outnumber the invader outright attacks on a broad front.
  withdrawal   conquered ground that cannot be held is given up rather than lost with its garrison.
               Overextended: two or more provinces beyond rail-restored ground and outgunned there
               (Russia leaving Kyiv, Chernihiv and Sumy oblasts, April 2022). A bridgehead whose every
               link back crosses a major river, outgunned (Russia leaving right-bank Kherson, November
               2022). Withdrawals are reviewed weekly; a nation never abandons its own soil.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from wargame.core.enums import Side
from wargame.core.mathutil import clamp

if TYPE_CHECKING:
    from wargame.conflict.land_warfare import LandWarfare
    from wargame.conflict.war import War
    from wargame.world.world import World

# --- posture ---------------------------------------------------------------------------------------
OFFENSIVE_SHARE = {"realistic_cautious": 0.35, "epic_aggressive": 0.5}
OFFENSIVE_SHARE_HALTED = 0.1
COUNTERATTACK_SHARE = 0.15
COUNTERATTACK_MIN_RESOLVE = 0.5
OPPORTUNITY_RATIO = 2.0          # A counteroffensive goes in where it can reach ~2:1...
OPPORTUNITY_MAX_SHARE = 0.35     # ...with no more than this share of the committed force...
OPPORTUNITY_MIN_DEPTH_KM = 0.5   # ...and only where it would actually move: not into a dug-in, drone-watched
                                 # line, where 2:1 buys ~60 m a day (Ukraine's restraint in 2024-25).
# Outright superiority: from COUNTERATTACK_SHARE at 1.2x the enemy's committed power to a full offensive at 2x.
COUNTEROFFENSIVE_FROM = 1.2
COUNTEROFFENSIVE_FULL = 2.0

# --- withdrawal -------------------------------------------------------------------------------------
OVEREXTENDED_HOPS = 2            # Provinces beyond rail-restored ground.
OUTGUNNED = 1.5                  # Enemy power next door / our power holding the province.
REVIEW_DAYS = 7


class Posture(Enum):
    OFFENSIVE = "offensive"
    HALTED = "halted"
    COUNTEROFFENSIVE = "counteroffensive"
    ACTIVE_DEFENCE = "active_defence"
    DEFENCE = "defence"


@dataclass(frozen=True)
class Withdrawal:
    tag: str
    province: int
    to: str          # Who takes the ground over.
    reason: str


def offensive_share(land: LandWarfare, world: World, wars: list[War], tag: str, power: float,
                    foes: set[str], effectiveness: float) -> tuple[float, Posture]:
    """Share of the committed force that goes on the attack today, and why."""
    best, posture = 0.0, Posture.DEFENCE
    for war in wars:
        p = war.participants.get(tag)
        if p is None:
            continue
        full = OFFENSIVE_SHARE.get(p.motivation.name, 0.35)
        if p.side is Side.ATTACKER or p.claim is not None:
            share, why = (OFFENSIVE_SHARE_HALTED, Posture.HALTED) if p.offensive_halted else (full, Posture.OFFENSIVE)
        elif p.resolve < COUNTERATTACK_MIN_RESOLVE:
            share, why = 0.0, Posture.DEFENCE
        else:
            enemy = land.committed_power(foes)
            edge = clamp((power / max(enemy, 1.0) - COUNTEROFFENSIVE_FROM) / (COUNTEROFFENSIVE_FULL - COUNTEROFFENSIVE_FROM))
            share = COUNTERATTACK_SHARE + (max(full, COUNTERATTACK_SHARE) - COUNTERATTACK_SHARE) * edge
            why = Posture.COUNTEROFFENSIVE if edge > 0 else Posture.ACTIVE_DEFENCE
            opening = _weakest_sector_share(land, world, tag, power, foes, effectiveness, war.prewar_occupation)
            if opening > share:
                share, why = opening, Posture.COUNTEROFFENSIVE
        if share > best:
            best, posture = share, why
    return best, posture


def worth_attacking(land: LandWarfare, world: World, wars: list[War], tag: str, target: int) -> bool:
    """Attackers press their war aims whatever the price (Russia's attrition in 2024-25). A defender only
    attacks where the attack would actually move."""
    for war in wars:
        p = war.participants.get(tag)
        if p is not None and world.provinces[target].controller in war.enemies_of(tag):
            if p.side is Side.ATTACKER or p.claim is not None:
                return True
            return land.depth_km_per_day(world, target, OPPORTUNITY_RATIO) >= OPPORTUNITY_MIN_DEPTH_KM
    return False


def _weakest_sector_share(land: LandWarfare, world: World, tag: str, power: float, foes: set[str],
                          effectiveness: float, prewar_occupation: dict[int, str] | None = None) -> float:
    """Share of our force needed to hit the enemy's thinnest adjacent sector at OPPORTUNITY_RATIO, if affordable.

    Only ground lost in this war opens a counteroffensive; what the enemy held before it began waits."""
    prewar = prewar_occupation or {}
    if power <= 0 or effectiveness <= 0:
        return 0.0
    cheapest = None
    friends = {tag} | {t for t in world.countries if t not in foes and world.country(t).overlord == tag}
    for p in world.controlled_by(tag):
        for n in p.neighbors:
            q = world.provinces[n]
            if q.controller in foes and q.owner in friends and prewar.get(n) != q.controller:  # Liberation.
                if land.depth_km_per_day(world, n, OPPORTUNITY_RATIO) < OPPORTUNITY_MIN_DEPTH_KM:
                    continue
                defence, _ = land.defence_of(world, n, foes)
                need = OPPORTUNITY_RATIO * defence / effectiveness
                if cheapest is None or need < cheapest:
                    cheapest = need
    if cheapest is None or cheapest > OPPORTUNITY_MAX_SHARE * power:
        return 0.0
    return min(OPPORTUNITY_MAX_SHARE, 1.25 * cheapest / power)  # A margin for the dead and the delayed.


def withdrawals(land: LandWarfare, world: World, wars: list[War], enemies: dict[str, set[str]],
                allies: dict[str, set[str]]) -> list[Withdrawal]:
    """Conquered provinces each belligerent should give up this week."""
    out: list[Withdrawal] = []
    for tag in sorted(enemies):
        foes = enemies[tag]
        dep = land.deployments.get(tag)
        if dep is None:
            continue
        friends = allies.get(tag, {tag})
        for p in sorted(world.controlled_by(tag), key=lambda q: q.id):
            if p.owner in friends or p.id in land.encircled:
                continue  # Own soil is never abandoned; pockets cannot march out.
            threat = sum(land.stationed_power(foes, n) for n in p.neighbors if world.provinces[n].controller in foes)
            held = dep.stationed.get(p.id, 0.0)
            if threat <= OUTGUNNED * max(held, 1e-6):
                continue
            hops = land.hops_from_rear(tag, p.id)
            links = [n for n in p.neighbors if world.provinces[n].controller in friends]
            river_locked = bool(links) and all(land.major_river_between(world, p.id, n) for n in links)
            if hops >= OVEREXTENDED_HOPS:
                reason = "overextended beyond its supply lines"
            elif river_locked:
                reason = "a bridgehead supplied only across a major river"
            else:
                continue
            to = p.owner if p.owner in foes else max(
                sorted(t for t in foes if any(world.provinces[n].controller == t for n in p.neighbors)),
                key=lambda t: sum(land.stationed_power({t}, n) for n in p.neighbors), default=None)
            if to is not None:
                out.append(Withdrawal(tag, p.id, to, reason))
    return out
