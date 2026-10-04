"""War: one armed conflict, its rules of escalation, its ledger, and how it ends.

A War owns everything that only makes sense *inside* a conflict: sides,
the war goal, war score, each belligerent's cost/benefit ledger and
motivation, external support flows, nuclear strike history, and the treaty
that ends it. Combat systems feed it (`record_casualties`, province control
changes on the World); it never moves troops itself.

Daily order (see `on_daily_tick`):
    1. deliver lend-lease          5. assess capitulation (every belligerent)
    2. roll cost/benefit ledgers   6. try to conclude (capitulation / goal / broken will / stalemate)
    3. update war score            7. nuclear decision
    4. update motivation/resolve   8. opportunistic entry (Tier 3)
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

from wargame.conflict.treaty import PeaceTreaty, apply_treaty, draft_treaty, white_peace
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, ParticipantRole, Side, SupplyType
from wargame.core.escalation import EscalationPolicy
from wargame.core.mathutil import clamp
from wargame.nation.country import CapitulationAssessment, CapitulationContext
from wargame.nation.nuclear import NuclearContext
from wargame.world.world import World

# ---------------------------------------------------------------------------
# Motivation (player setup) and the cost/benefit ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotivationProfile:
    name: str
    initial_resolve: float     # 0..1 will to keep the offensive going.
    casualty_tolerance: float  # Casualties per point of strategic value gained that still feel "worth it".
    resolve_decay: float       # Resolve lost per day per unit of cost overshoot.
    resolve_recovery: float    # Resolve regained per day when gains are cheap.
    halt_ratio: float          # Cost overshoot at which offensives pause to consolidate.
    ambition: float            # 0..1 share of spare war score spent on extra treaty demands.
    morale_bonus: float        # Flat combat morale modifier ("epic" armies fight harder).


MOTIVATION_PRESETS: dict[Motivation, MotivationProfile] = {
    Motivation.CAUTIOUS: MotivationProfile(
        name="realistic_cautious", initial_resolve=0.70, casualty_tolerance=2_000.0,
        resolve_decay=0.03, resolve_recovery=0.010, halt_ratio=1.5, ambition=0.10, morale_bonus=0.0,
    ),
    Motivation.AGGRESSIVE: MotivationProfile(
        name="epic_aggressive", initial_resolve=0.95, casualty_tolerance=8_000.0,
        resolve_decay=0.012, resolve_recovery=0.020, halt_ratio=4.0, ambition=0.60, morale_bonus=0.10,
    ),
}

LEDGER_WINDOW_DAYS = 14
MIN_CASUALTIES_TO_JUDGE = 200
MAX_OVERSHOOT_PENALTY = 3.0
RESOLVE_WAR_SUPPORT_HEADROOM = 0.25  # An army can't stay keener than its public by more than this.


@dataclass
class CampaignLedger:
    """Rolling record of blood spent vs strategic value gained."""

    casualties_total: int = 0
    casualties_today: int = 0
    value_held_yesterday: float = 0.0
    window: deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=LEDGER_WINDOW_DAYS))

    def roll(self, value_held_now: float) -> None:
        self.window.append((self.casualties_today, value_held_now - self.value_held_yesterday))
        self.casualties_today = 0
        self.value_held_yesterday = value_held_now

    def cost_overshoot(self, tolerance: float) -> float | None:
        """(casualties per value gained) / tolerance over the window. None = too little fighting to judge."""
        casualties = sum(c for c, _ in self.window)
        if casualties < MIN_CASUALTIES_TO_JUDGE:
            return None
        gained = sum(g for _, g in self.window)
        if gained <= 0:
            return math.inf
        return (casualties / gained) / tolerance


@dataclass
class WarParticipant:
    tag: str
    side: Side
    role: ParticipantRole
    joined_hour: int
    motivation: MotivationProfile
    resolve: float
    ledger: CampaignLedger
    offensive_halted: bool = False


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalSupport:
    supporter: str
    recipient: str
    daily: dict[SupplyType, float]


@dataclass(frozen=True)
class NuclearStrike:
    hour: int
    user: str
    target_country: str
    province_id: int
    missile: str
    intercepted: bool


@dataclass(frozen=True)
class WarEvent:
    hour: int
    kind: str
    message: str


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

OCCUPATION_SCORE = 100.0
TICKING_MAX = 25.0
TICKING_PER_DAY = 1.0
TICKING_GRACE_DAYS = 30
BATTLE_SCORE_MAX = 20.0
BLOCKADE_SCORE_MAX = 10.0

RESOLVE_BROKEN = 0.05
STALEMATE_RESOLVE = 0.25
STALEMATE_SCORE_BAND = 15.0
DEFENDER_MIN_SCORE_FOR_TERMS = 10.0

LEND_LEASE_MIN_RELATION = 0.5
LEND_LEASE_MAX_ENEMY_RELATION = -0.2
LEND_LEASE_SHARE = 0.15

OPPORTUNIST_MAX_RELATION = -0.5
OPPORTUNIST_MIN_PROXIMITY = 0.6
OPPORTUNIST_DAILY_CHANCE = 0.02

FALLOUT_SANCTIONS = 0.5
FALLOUT_RELATIONS = -0.5


# ---------------------------------------------------------------------------
# War
# ---------------------------------------------------------------------------


@dataclass
class War:
    id: str
    goal: WarGoal
    policy: EscalationPolicy
    nuclear_weapons_enabled: bool
    started_hour: int
    participants: dict[str, WarParticipant] = field(default_factory=dict)
    exited: set[str] = field(default_factory=set)
    external_support: list[ExternalSupport] = field(default_factory=list)
    war_score: float = 0.0  # -100..100 from the attacker's perspective.
    war_score_components: dict[str, float] = field(default_factory=dict)
    ticking_score: float = 0.0
    assessments: dict[str, CapitulationAssessment] = field(default_factory=dict)
    nuclear_strikes: list[NuclearStrike] = field(default_factory=list)
    events: list[WarEvent] = field(default_factory=list)
    treaty: PeaceTreaty | None = None

    # --- construction ------------------------------------------------------

    @classmethod
    def declare(
        cls,
        world: World,
        goal: WarGoal,
        tier: EscalationTier,
        *,
        nuclear_weapons_enabled: bool,
        attacker_motivation: Motivation,
        defender_motivation: Motivation,
        now_hour: int,
        war_id: str | None = None,
    ) -> War:
        war = cls(
            id=war_id or f"{goal.holder}-{goal.target}-{now_hour}",
            goal=goal,
            policy=EscalationPolicy.for_tier(tier),
            nuclear_weapons_enabled=nuclear_weapons_enabled,
            started_hour=now_hour,
        )
        war._join(world, goal.holder, Side.ATTACKER, ParticipantRole.PRIMARY, MOTIVATION_PRESETS[attacker_motivation], now_hour)
        war._join(world, goal.target, Side.DEFENDER, ParticipantRole.PRIMARY, MOTIVATION_PRESETS[defender_motivation], now_hour)
        attacker, defender = world.country(goal.holder), world.country(goal.target)
        defender.spirit.apply_shock("rally_round_the_flag", now_hour, war_support=0.15, duration_days=60)
        war._log(now_hour, "declaration",
                 f"{attacker.name} declares war on {defender.name}: {goal.type.value}, escalation tier {int(tier)}.")
        if war.policy.alliances_trigger:
            war._trigger_defensive_pacts(world, now_hour)
        war.review_external_support(world, now_hour)
        return war

    def _join(self, world: World, tag: str, side: Side, role: ParticipantRole,
              motivation: MotivationProfile, now_hour: int) -> None:
        world.country(tag).mark_prewar_baseline(world)
        self.participants[tag] = WarParticipant(
            tag=tag, side=side, role=role, joined_hour=now_hour, motivation=motivation,
            resolve=motivation.initial_resolve,
            ledger=CampaignLedger(value_held_yesterday=self._controlled_value(world, tag)),
        )

    def _trigger_defensive_pacts(self, world: World, now_hour: int) -> None:
        defender = world.country(self.goal.target)
        for ally in sorted(defender.defensive_pacts):
            if ally in self.participants or ally not in world.countries:
                continue
            self._join(world, ally, Side.DEFENDER, ParticipantRole.CO_BELLIGERENT,
                       MOTIVATION_PRESETS[Motivation.CAUTIOUS], now_hour)
            self._log(now_hour, "pact_triggered",
                      f"{world.country(ally).name} honours its defensive pact and enters the war.")

    # --- queries -------------------------------------------------------------

    @property
    def escalation_tier(self) -> EscalationTier:
        return self.policy.tier

    @property
    def ended(self) -> bool:
        return self.treaty is not None

    def side_of(self, tag: str) -> Side:
        return self.participants[tag].side

    def tags_on(self, side: Side) -> frozenset[str]:
        return frozenset(t for t, p in self.participants.items() if p.side is side)

    def enemies_of(self, tag: str) -> frozenset[str]:
        return self.tags_on(self.side_of(tag).opposite)

    def is_existential_for(self, tag: str) -> bool:
        return self.goal.is_existential and tag == self.goal.target

    def support_level(self, world: World, tag: str) -> float:
        """Share of the recipient's consumption covered by lend-lease (0..1)."""
        need = sum(world.country(tag).logistics.base_daily_consumption.values())
        given = sum(sum(f.daily.values()) for f in self.external_support if f.recipient == tag)
        return clamp(given / need) if need > 0 else 0.0

    @staticmethod
    def _controlled_value(world: World, tag: str) -> float:
        return sum(p.strategic_value() for p in world.controlled_by(tag))

    @staticmethod
    def _occupation_share(world: World, owners: frozenset[str], holders: frozenset[str]) -> float:
        total = held = 0.0
        for p in world.provinces.values():
            if p.owner in owners:
                v = p.strategic_value()
                total += v
                if p.controller in holders:
                    held += v
        return held / total if total else 0.0

    def capitulation_context(self, world: World, tag: str, now_hour: int) -> CapitulationContext:
        side = self.side_of(tag)
        return CapitulationContext(
            now_hour=now_hour,
            hostile_tags=self.tags_on(side.opposite),
            existential_threat=self.is_existential_for(tag),
            external_support_level=self.support_level(world, tag),
            allied_belligerents=len(self.tags_on(side)) - 1,
        )

    def nuclear_context(self, world: World, tag: str) -> NuclearContext:
        country = world.country(tag)
        enemies = sorted(self.enemies_of(tag))
        enemy_countries = [world.country(e) for e in enemies]
        umbrella = any(
            world.countries[a].nuclear.is_nuclear_power
            for e in enemy_countries
            for a in e.defensive_pacts
            if a in world.countries and a != tag and a not in self.participants
        )
        # Retaliation would come by whichever delivery class our BMD handles worst.
        enemy_classes = {m for e in enemy_countries if e.nuclear.is_nuclear_power for m in e.nuclear.delivery}
        own_bmd = min(
            (country.nuclear.intercept_probability(m, country.capital_province_id) for m in enemy_classes),
            default=0.0,
        )
        assessment = self.assessments.get(tag)
        return NuclearContext(
            weapons_enabled=self.nuclear_weapons_enabled,
            policy=self.policy,
            collapse_proximity=assessment.proximity if assessment else 0.0,
            existential_threat=self.is_existential_for(tag),
            enemy_used_nuclear=any(s.user in enemies for s in self.nuclear_strikes),
            enemy_warheads=sum(e.nuclear.warheads for e in enemy_countries),
            enemy_second_strike=any(e.nuclear.second_strike_capable for e in enemy_countries),
            enemy_nuclear_umbrella=umbrella,
            own_bmd_vs_retaliation=own_bmd,
            trade_dependence=country.trade_dependence,
        )

    # --- inputs from other systems --------------------------------------------

    def record_casualties(self, world: World, tag: str, count: int) -> None:
        """Called by the combat system."""
        ledger = self.participants[tag].ledger
        ledger.casualties_today += count
        ledger.casualties_total += count
        world.country(tag).oob.record_casualties(count)

    # --- daily loop -----------------------------------------------------------

    def on_daily_tick(self, world: World, now_hour: int, rng: random.Random) -> None:
        if self.ended:
            return
        self._deliver_external_support(world)
        for p in self.participants.values():
            p.ledger.roll(self._controlled_value(world, p.tag))
        self._update_war_score(world, now_hour)
        self._update_resolve(world, now_hour)
        self._assess_capitulations(world, now_hour)
        if self._try_conclude(world, now_hour):
            return
        self._consider_nuclear_use(world, now_hour, rng)
        self._consider_opportunistic_entry(world, now_hour, rng)

    def _update_war_score(self, world: World, now_hour: int) -> None:
        attackers, defenders = self.tags_on(Side.ATTACKER), self.tags_on(Side.DEFENDER)

        progress = self.goal.progress(world, attackers)
        days = (now_hour - self.started_hour) / 24
        if progress > 0:
            self.ticking_score += TICKING_PER_DAY * progress
        elif days > TICKING_GRACE_DAYS:
            self.ticking_score -= TICKING_PER_DAY * 0.5  # Defender holds the goal: time is on its side.
        self.ticking_score = clamp(self.ticking_score, -TICKING_MAX, TICKING_MAX)

        inflicted = sum(self.participants[t].ledger.casualties_total for t in defenders)
        taken = sum(self.participants[t].ledger.casualties_total for t in attackers)
        battle = BATTLE_SCORE_MAX * (inflicted - taken) / (inflicted + taken) if inflicted + taken else 0.0
        blockade = BLOCKADE_SCORE_MAX * (
            world.country(self.goal.target).blockade_interdiction - world.country(self.goal.holder).blockade_interdiction
        )

        self.war_score_components = {
            "occupation": OCCUPATION_SCORE * (
                self._occupation_share(world, defenders, attackers) - self._occupation_share(world, attackers, defenders)
            ),
            "war_goal": self.ticking_score,
            "battles": battle,
            "blockade": blockade,
        }
        self.war_score = clamp(sum(self.war_score_components.values()), -100.0, 100.0)

    def _update_resolve(self, world: World, now_hour: int) -> None:
        """Cost/reward: offensives that bleed without gaining ground lose the army's will."""
        for p in self.participants.values():
            country = world.country(p.tag)
            if p.side is Side.DEFENDER:
                p.resolve = country.spirit.resolve(now_hour)  # Defenders' will *is* the nation's.
                continue
            was_halted = p.offensive_halted
            overshoot = p.ledger.cost_overshoot(p.motivation.casualty_tolerance)
            if overshoot is not None:
                if overshoot > 1.0:
                    p.resolve -= p.motivation.resolve_decay * min(overshoot - 1.0, MAX_OVERSHOOT_PENALTY)
                else:
                    p.resolve += p.motivation.resolve_recovery * (1.0 - overshoot)
                p.offensive_halted = overshoot > p.motivation.halt_ratio
            ceiling = clamp(country.spirit.effective_war_support(now_hour) + RESOLVE_WAR_SUPPORT_HEADROOM)
            p.resolve = clamp(min(p.resolve, ceiling))
            if p.offensive_halted and not was_halted:
                self._log(now_hour, "offensive_halted",
                          f"{country.name} halts its offensive: losses are outpacing strategic gains.")
            elif was_halted and not p.offensive_halted:
                self._log(now_hour, "offensive_resumed", f"{country.name} resumes offensive operations.")

    def _assess_capitulations(self, world: World, now_hour: int) -> None:
        for tag, p in list(self.participants.items()):
            country = world.country(tag)
            previous = self.assessments.get(tag)
            assessment = country.evaluate_capitulation(world, self.capitulation_context(world, tag, now_hour))
            self.assessments[tag] = assessment
            if assessment.capitulates and p.role is ParticipantRole.CO_BELLIGERENT:
                self._separate_peace(world, tag, now_hour)
            elif assessment.collapse_progress > 0 and (previous is None or previous.collapse_progress == 0):
                self._log(now_hour, "collapse_begins", assessment.narrative())

    def _separate_peace(self, world: World, tag: str, now_hour: int) -> None:
        """A capitulated co-belligerent leaves; its own occupied land stays occupied until the main peace."""
        for p in list(world.controlled_by(tag)):
            if p.owner != tag:
                world.set_controller(p.id, p.owner)
        del self.participants[tag]
        self.exited.add(tag)
        self._log(now_hour, "separate_peace", f"{world.country(tag).name} capitulates and leaves the war.")

    # --- ending the war ----------------------------------------------------------

    def _try_conclude(self, world: World, now_hour: int) -> bool:
        holder, target = self.goal.holder, self.goal.target
        attacker, defender = world.country(holder), world.country(target)
        attackers = self.tags_on(Side.ATTACKER)

        if defender.capitulated:
            return self._conclude(world, now_hour, holder, target, 100.0,
                                  self.goal.is_achieved(world, attackers), f"{defender.name} capitulated")
        if attacker.capitulated:
            return self._conclude(world, now_hour, target, holder, 100.0, False, f"{attacker.name} capitulated")
        if self.goal.is_achieved(world, attackers):
            return self._conclude(world, now_hour, holder, target, self.war_score, True, "war goal secured")

        attacker_resolve = self.participants[holder].resolve
        if attacker_resolve <= RESOLVE_BROKEN:
            reason = f"{attacker.name}'s will to continue the war has broken"
            if -self.war_score >= DEFENDER_MIN_SCORE_FOR_TERMS:
                return self._conclude(world, now_hour, target, holder, -self.war_score, False, reason)
            if self.war_score > 0:  # Exhausted but ahead: a ceasefire on current lines.
                return self._conclude(world, now_hour, holder, target, self.war_score, False, reason)
            return self._end(world, white_peace(now_hour, reason), now_hour)

        if (
            attacker_resolve < STALEMATE_RESOLVE
            and self.participants[target].resolve < STALEMATE_RESOLVE
            and abs(self.war_score) < STALEMATE_SCORE_BAND
        ):
            return self._end(world, white_peace(now_hour, "mutual exhaustion"), now_hour)
        return False

    def _conclude(self, world: World, now_hour: int, winner: str, loser: str,
                  score: float, goal_achieved: bool, reason: str) -> bool:
        treaty = draft_treaty(
            world=world, goal=self.goal, winner=winner, loser=loser,
            winner_side=self.tags_on(self.side_of(winner)), war_score=score,
            ambition=self.participants[winner].motivation.ambition,
            goal_achieved=goal_achieved, signed_hour=now_hour, reason=reason,
        )
        return self._end(world, treaty, now_hour)

    def _end(self, world: World, treaty: PeaceTreaty, now_hour: int) -> bool:
        everyone = set(self.participants) | self.exited
        apply_treaty(world, treaty, everyone)
        for tag in everyone:
            world.country(tag).on_peace()
        self.treaty = treaty
        terms = ", ".join(t.type.value for t in treaty.terms)
        self._log(now_hour, "peace", f"Peace signed ({treaty.reason}): {terms}.")
        return True

    # --- external support (Tier 2+) ---------------------------------------------

    def review_external_support(self, world: World, now_hour: int) -> None:
        """Re-evaluated weekly: who is willing to arm whom, based on geopolitical leanings."""
        if not self.policy.lend_lease_allowed:
            self.external_support = []
            return
        before = {(f.supporter, f.recipient) for f in self.external_support}
        flows: list[ExternalSupport] = []
        for tag, country in world.countries.items():
            if tag in self.participants or tag in self.exited:
                continue
            for recipient in self.participants:
                if country.relations.get(recipient, 0.0) < LEND_LEASE_MIN_RELATION:
                    continue
                if any(country.relations.get(e, 0.0) > LEND_LEASE_MAX_ENEMY_RELATION for e in self.enemies_of(recipient)):
                    continue
                daily = {s: amt * LEND_LEASE_SHARE for s, amt in country.logistics.base_daily_production.items() if amt > 0}
                if daily:
                    flows.append(ExternalSupport(tag, recipient, daily))
        self.external_support = flows
        for f in flows:
            if (f.supporter, f.recipient) not in before:
                self._log(now_hour, "lend_lease",
                          f"{world.country(f.supporter).name} begins supplying {world.country(f.recipient).name}.")

    def _deliver_external_support(self, world: World) -> None:
        for flow in self.external_support:
            recipient = world.country(flow.recipient)
            delivered = 1.0 - recipient.blockade_interdiction * recipient.seaborne_import_share
            for s, amt in flow.daily.items():
                recipient.logistics.lend_lease_inbound[s] += amt * delivered

    # --- opportunistic entry (Tier 3) ----------------------------------------------

    def _consider_opportunistic_entry(self, world: World, now_hour: int, rng: random.Random) -> None:
        if not self.policy.opportunistic_entry_allowed:
            return
        for tag, country in world.countries.items():
            if tag in self.participants or tag in self.exited:
                continue
            for victim in list(self.participants):
                assessment = self.assessments.get(victim)
                if assessment is None or assessment.proximity < OPPORTUNIST_MIN_PROXIMITY:
                    continue
                if country.relations.get(victim, 0.0) > OPPORTUNIST_MAX_RELATION:
                    continue
                if victim not in world.neighboring_countries(tag):
                    continue
                if rng.random() < OPPORTUNIST_DAILY_CHANCE:
                    self._join(world, tag, self.side_of(victim).opposite, ParticipantRole.CO_BELLIGERENT,
                               MOTIVATION_PRESETS[Motivation.CAUTIOUS], now_hour)
                    self._log(now_hour, "opportunist",
                              f"Sensing weakness, {country.name} declares war on {world.country(victim).name}.")
                    break

    # --- nuclear -----------------------------------------------------------------------

    def _consider_nuclear_use(self, world: World, now_hour: int, rng: random.Random) -> None:
        if not self.nuclear_weapons_enabled:
            return
        for tag in list(self.participants):
            country = world.country(tag)
            if not country.nuclear.is_nuclear_power:
                continue
            assessment = country.nuclear.evaluate_hesitation(self.nuclear_context(world, tag))
            if assessment.considering_use and rng.random() < assessment.launch_probability:
                self.resolve_nuclear_strike(world, tag, now_hour, rng)

    def resolve_nuclear_strike(self, world: World, user_tag: str, now_hour: int, rng: random.Random) -> NuclearStrike | None:
        user = world.country(user_tag)
        enemies = self.enemies_of(user_tag)
        targets = [p for p in world.provinces.values() if p.controller in enemies and p.owner != user_tag]
        if not targets or not user.nuclear.is_nuclear_power:
            return None
        target = max(targets, key=lambda p: p.strategic_value())
        victim = world.country(target.controller)
        # Launch with whichever delivery class the victim's BMD handles worst.
        missile = min(sorted(user.nuclear.delivery, key=lambda m: m.value),
                      key=lambda m: victim.nuclear.intercept_probability(m, target.id))
        p_intercept = victim.nuclear.intercept_probability(missile, target.id)
        for layer in victim.nuclear.missile_defenses:
            layer.expend(missile, target.id)
        user.nuclear.warheads -= 1
        intercepted = rng.random() < p_intercept

        strike = NuclearStrike(now_hour, user_tag, victim.tag, target.id, missile.value, intercepted)
        self.nuclear_strikes.append(strike)
        if intercepted:
            self._log(now_hour, "nuclear_intercepted",
                      f"{user.name} launches a nuclear strike on {target.name}; {victim.name}'s missile defence intercepts it.")
        else:
            target.damage = max(target.damage, 0.9)
            target.population = int(target.population * 0.6)
            target.infrastructure *= 0.2
            victim.spirit.apply_shock("nuclear_strike", now_hour, war_support=-0.10, stability=-0.15, duration_days=180)
            self._log(now_hour, "nuclear_detonation", f"{user.name} detonates a nuclear weapon over {target.name}.")

        # Fallout falls on the launcher whether or not the warhead got through.
        mult = self.policy.nuclear_fallout_multiplier
        if mult > 0:
            user.sanction_severity = clamp(user.sanction_severity + FALLOUT_SANCTIONS * mult)
            friends = self.tags_on(self.side_of(user_tag))
            for tag, other in world.countries.items():
                if tag not in friends:
                    other.relations[user_tag] = clamp(other.relations.get(user_tag, 0.0) + FALLOUT_RELATIONS * mult, -1.0, 1.0)
        return strike

    # --- narration ------------------------------------------------------------------------

    def _log(self, hour: int, kind: str, message: str) -> None:
        self.events.append(WarEvent(hour, kind, message))
