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

from wargame.conflict.treaty import PeaceTreaty, TreatyTerm, apply_treaty, draft_treaty, white_peace
from wargame.conflict.war_goal import WarGoal
from wargame.core.enums import EscalationTier, Motivation, ParticipantRole, Side, SupplyType, TermType, WarGoalType
from wargame.core.escalation import EscalationPolicy
from wargame.core.mathutil import clamp, noisy_or
from wargame.nation.country import CapitulationAssessment, CapitulationContext, Country
from wargame.nation.exile import exile_eligible, form_government_in_exile
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
    claim: WarGoal | None = None  # A joiner's own war aim (opportunists), honoured at the peace table.


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

AGGRESSION_FRIEND_MIN = 0.3          # States at least this friendly to the victim react to the attack...
AGGRESSION_VICTIM_SHIFT = 0.2        # ...by warming to the victim...
AGGRESSION_ATTACKER_SHIFT = -0.3     # ...and turning against the attacker.

OPPORTUNIST_MAX_RELATION = -0.5
OPPORTUNIST_MIN_PROXIMITY = 0.6
OPPORTUNIST_DAILY_CHANCE = 0.02
OPPORTUNIST_CLAIM_SIZE = 3           # Border provinces an opportunist claims for itself.

EXILE_PURSUIT_MIN_RESOLVE = 0.35     # An attacker this tired settles with a government in exile...
EXILE_PURSUIT_POWER_RATIO = 1.5      # ...and so does one without a clear edge over the Free forces.

FALLOUT_SANCTIONS = 0.5
FALLOUT_RELATIONS = -0.5
NUCLEAR_STRIKE_INTERVAL_HOURS = 72   # Hiroshima to Nagasaki: give the enemy time to yield.
PATRON_INTERVENTION_RELATION = 0.6   # Patrons this committed answer a nuclear strike with force.

STUBBORN_AFTER_DAYS = 60             # Before this, nobody is "frustrated" yet.
STUBBORN_RAMP_DAYS = 180


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
    settlements: list[PeaceTreaty] = field(default_factory=list)  # Surrenders signed while the war goes on.
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
        if war.policy.lend_lease_allowed:
            war._world_reacts_to_aggression(world, now_hour)
        war.review_external_support(world, now_hour)
        return war

    def _world_reacts_to_aggression(self, world: World, now_hour: int) -> None:
        """Third parties friendly to the victim harden: toward it, and against the attacker (cf. February 2022)."""
        attacker, victim = self.goal.holder, self.goal.target
        moved = []
        for tag, country in world.countries.items():
            if tag in self.participants:
                continue
            toward_victim = country.relations.get(victim, 0.0)
            if toward_victim >= AGGRESSION_FRIEND_MIN and country.relations.get(attacker, 0.0) < toward_victim:
                country.relations[victim] = clamp(toward_victim + AGGRESSION_VICTIM_SHIFT, -1.0, 1.0)
                country.relations[attacker] = clamp(country.relations.get(attacker, 0.0) + AGGRESSION_ATTACKER_SHIFT, -1.0, 1.0)
                moved.append(country.name)
        if moved:
            self._log(now_hour, "world_reaction",
                      f"{len(moved)} states condemn the attack on {world.country(victim).name} and move to support it.")

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

    def side_power(self, world: World, side: Side) -> float:
        return sum(world.country(t).military_power() for t in sorted(self.tags_on(side)))

    def stubbornness(self, world: World, now_hour: int) -> float:
        """0..1: the war has dragged on and the goal's target is nowhere near yielding.

        Judged on *conventional* pressure and on whether the government is
        actually collapsing: a target still standing after a nuclear strike is
        still stubborn, which is what makes a follow-up strike (Nagasaki) likely.
        """
        days = (now_hour - self.started_hour) / 24
        target = self.assessments.get(self.goal.target)
        if target is None:
            return 0.0
        conventional = noisy_or(v for k, v in target.components.items() if k != "nuclear_shock")
        proximity = conventional / target.threshold if target.threshold > 0 else 1.0
        ramp = clamp((days - STUBBORN_AFTER_DAYS) / STUBBORN_RAMP_DAYS)
        return ramp * clamp(1.0 - proximity) * (1.0 - target.collapse_progress)

    def nuclear_context(self, world: World, tag: str, now_hour: int) -> NuclearContext:
        country = world.country(tag)
        side = self.side_of(tag)
        enemies = sorted(self.enemies_of(tag))
        enemy_countries = [world.country(e) for e in enemies]
        own_power = self.side_power(world, side)
        patrons = sorted({f.supporter for f in self.external_support if f.recipient in enemies})
        patron_power = sum(world.country(p).military_power() for p in patrons)
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
            enemy_patron_power_ratio=patron_power / own_power if own_power > 0 else 0.0,
            pursuing_goal=tag == self.goal.holder,
            goal_existential=self.goal.is_existential,
            power_ratio=own_power / max(self.side_power(world, side.opposite), 1e-9),
            stubbornness=self.stubbornness(world, now_hour) if tag == self.goal.holder else 0.0,
            own_prior_strikes=sum(1 for s in self.nuclear_strikes if s.user == tag),
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
            if p.claim is not None and not p.offensive_halted and p.claim.is_achieved(world, (p.tag,)):
                p.offensive_halted = True  # It has what it came for and digs in.
                self._log(now_hour, "claim_secured",
                          f"{country.name} has seized the provinces it claimed and digs in to hold them.")
            if p.side is Side.DEFENDER:
                p.resolve = country.spirit.resolve(now_hour)  # Defenders' will *is* the nation's.
                continue
            if p.claim is not None and p.offensive_halted:
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
            elif assessment.capitulates:
                self._log(now_hour, "capitulation", assessment.narrative())
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

        # The government has fallen (capitulated, or lost its capital to a regime-change war),
        # but morale in the free territory may carry the fight on without it.
        government_fell = defender.capitulated or (self.goal.is_existential and self.goal.is_achieved(world, attackers))
        if government_fell and self.goal.is_existential and exile_eligible(world, defender, now_hour):
            return self._government_in_exile(world, defender, now_hour)
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
        if not treaty.is_white_peace:
            treaty.terms[:0] = self._claim_terms(world, winner_side=self.side_of(winner), already=treaty.transferred_provinces())
        return self._end(world, treaty, now_hour)

    def _claim_terms(self, world: World, winner_side: Side, already: set[int]) -> list[TreatyTerm]:
        """Winning-side joiners take the claimed provinces they hold (all of them if the claimant's target collapsed)."""
        terms: list[TreatyTerm] = []
        for p in self.participants.values():
            claim = p.claim
            if claim is None or p.side is not winner_side or claim.target not in world.countries:
                continue
            if claim.target in self.participants and self.side_of(claim.target) is winner_side:
                continue
            target = world.country(claim.target)
            pids = frozenset(
                pid for pid in claim.province_ids
                if pid not in already
                and world.provinces[pid].owner == claim.target
                and (target.capitulated or world.provinces[pid].controller == p.tag)
            )
            if pids:
                terms.append(TreatyTerm(TermType.PROVINCE_TRANSFER, p.tag, claim.target, 0.0, pids))
                already |= pids
        return terms

    def _government_in_exile(self, world: World, government: Country, now_hour: int) -> bool:
        """The government capitulates; a Free state fights on. Returns True if the war ended."""
        holder = self.goal.holder
        exile = form_government_in_exile(world, government, now_hour)

        # The old government signs for what it still has; the war goes on, so nothing is handed back.
        surrender = draft_treaty(
            world=world, goal=self.goal, winner=holder, loser=government.tag,
            winner_side=self.tags_on(Side.ATTACKER), war_score=100.0,
            ambition=self.participants[holder].motivation.ambition,
            goal_achieved=True, signed_hour=now_hour, reason=f"{government.name} capitulated",
        )
        apply_treaty(world, surrender, (), restore=False)
        for p in list(world.controlled_by(government.tag)):
            if p.owner != government.tag:
                world.set_controller(p.id, p.owner)  # Its troops stand down.
        self.settlements.append(surrender)
        motivation = self.participants.pop(government.tag).motivation
        self.exited.add(government.tag)
        government.on_peace()
        self._join(world, exile.tag, Side.DEFENDER, ParticipantRole.PRIMARY, motivation, now_hour)
        capital = world.provinces[exile.capital_province_id].name
        self._log(now_hour, "government_in_exile",
                  f"{government.name}'s government capitulates, but {exile.name} fights on from {capital}.")

        attacker = world.country(holder)
        if self._attacker_pursues(world, exile):
            claimed = self.goal.province_ids & {p.id for p in world.owned_by(exile.tag)}
            self.goal = (
                WarGoal(WarGoalType.TOTAL_CAPITULATION, holder, exile.tag) if self.goal.is_existential
                else WarGoal(WarGoalType.TERRITORIAL_CONQUEST, holder, exile.tag, frozenset(claimed))
            )
            self.ticking_score = 0.0
            self._log(now_hour, "pursuit", f"{attacker.name} vows to destroy {exile.name}.")
            return False
        self._log(now_hour, "ceasefire", f"{attacker.name} will not pursue {exile.name} and accepts a ceasefire.")
        return self._end(world, white_peace(now_hour, f"ceasefire with {exile.name}"), now_hour)

    def _attacker_pursues(self, world: World, exile: Country) -> bool:
        """The attacking nation's call: crush the Free forces, or take its gains and stop."""
        if self.participants[self.goal.holder].resolve < EXILE_PURSUIT_MIN_RESOLVE:
            return False
        exile_provinces = {p.id for p in world.owned_by(exile.tag)}
        if not (self.goal.is_existential or self.goal.province_ids & exile_provinces):
            return False
        return self.side_power(world, Side.ATTACKER) >= EXILE_PURSUIT_POWER_RATIO * self.side_power(world, Side.DEFENDER)

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
                    claim = self._border_claim(world, tag, victim)
                    self.participants[tag].claim = claim
                    claimed = ", ".join(world.provinces[pid].name for pid in sorted(claim.province_ids))
                    self._log(now_hour, "opportunist",
                              f"Sensing weakness, {country.name} declares war on {world.country(victim).name}, "
                              f"claiming {claimed}.")
                    break

    @staticmethod
    def _border_claim(world: World, claimant: str, victim: str) -> WarGoal:
        border = {
            nid for p in world.owned_by(claimant) for nid in p.neighbors if world.provinces[nid].owner == victim
        }
        best = sorted(border, key=lambda pid: (-world.provinces[pid].strategic_value(), pid))[:OPPORTUNIST_CLAIM_SIZE]
        return WarGoal(WarGoalType.TERRITORIAL_CONQUEST, claimant, victim, frozenset(best))

    # --- nuclear -----------------------------------------------------------------------

    def _consider_nuclear_use(self, world: World, now_hour: int, rng: random.Random) -> None:
        if not self.nuclear_weapons_enabled:
            return
        for tag in list(self.participants):
            country = world.country(tag)
            if not country.nuclear.is_nuclear_power or self._awaiting_strike_effect(tag, now_hour):
                continue
            assessment = country.nuclear.evaluate_hesitation(self.nuclear_context(world, tag, now_hour))
            if assessment.considering_use and rng.random() < assessment.launch_probability:
                self.resolve_nuclear_strike(world, tag, now_hour, rng)

    def _awaiting_strike_effect(self, tag: str, now_hour: int) -> bool:
        """After a strike, give the enemy time to yield, unless they hit back in the meantime."""
        own = [s.hour for s in self.nuclear_strikes if s.user == tag]
        if not own or now_hour - max(own) >= NUCLEAR_STRIKE_INTERVAL_HOURS:
            return False
        enemies = self.enemies_of(tag)
        return not any(s.user in enemies and s.hour >= max(own) for s in self.nuclear_strikes)

    def resolve_nuclear_strike(self, world: World, user_tag: str, now_hour: int, rng: random.Random) -> NuclearStrike | None:
        user = world.country(user_tag)
        enemies = self.enemies_of(user_tag)
        targets = [p for p in world.provinces.values() if p.controller in enemies and p.owner != user_tag]
        if not targets or not user.nuclear.is_nuclear_power:
            return None
        # The most valuable target still standing: nobody re-strikes a ruin.
        target = max(targets, key=lambda p: p.strategic_value() * (1.0 - p.damage))
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
            response = victim.absorb_nuclear_strike(world, user, now_hour)
            self._log(now_hour, "nuclear_detonation", f"{user.name} detonates a nuclear weapon over {target.name}.")
            if response.rally:
                self._log(now_hour, "nuclear_rally",
                          f"Devastated but defiant, {victim.name} rallies and demands retaliation.")
            else:
                self._log(now_hour, "nuclear_shock", f"{victim.name}'s will to resist is shaken to its foundations.")

        # Fallout falls on the launcher whether or not the warhead got through.
        mult = self.policy.nuclear_fallout_multiplier
        if mult > 0:
            user.sanction_severity = clamp(user.sanction_severity + FALLOUT_SANCTIONS * mult)
            friends = self.tags_on(self.side_of(user_tag))
            for tag, other in world.countries.items():
                if tag not in friends:
                    other.relations[user_tag] = clamp(other.relations.get(user_tag, 0.0) + FALLOUT_RELATIONS * mult, -1.0, 1.0)
        self._escalate_after_nuclear_use(world, user_tag, now_hour)
        return strike

    def _escalate_after_nuclear_use(self, world: World, user_tag: str, now_hour: int) -> None:
        """Tier 2 ratchet: the taboo is broken, the proxy war becomes a general war.

        Patrons strongly committed to the victim intervene conventionally. They
        enter as ordinary belligerents, so whether *they* then go nuclear is
        decided by the same hesitation model (and MAD normally says no).
        """
        new_tier = self.policy.nuclear_use_escalates_to
        if new_tier is None:
            return
        victims = self.enemies_of(user_tag)
        victim_side = self.side_of(user_tag).opposite
        patrons = sorted({f.supporter for f in self.external_support if f.recipient in victims})
        self.policy = EscalationPolicy.for_tier(new_tier)
        self._log(now_hour, "escalation",
                  f"The nuclear taboo is broken: the war escalates to tier {int(new_tier)}.")
        for patron in patrons:
            if patron in self.participants:
                continue
            country = world.country(patron)
            if max(country.relations.get(v, 0.0) for v in victims) < PATRON_INTERVENTION_RELATION:
                continue
            self._join(world, patron, victim_side, ParticipantRole.CO_BELLIGERENT,
                       MOTIVATION_PRESETS[Motivation.CAUTIOUS], now_hour)
            self._log(now_hour, "patron_intervention",
                      f"{country.name} answers the nuclear strike with direct military intervention.")
        self._trigger_defensive_pacts(world, now_hour)
        self.review_external_support(world, now_hour)

    # --- narration ------------------------------------------------------------------------

    def _log(self, hour: int, kind: str, message: str) -> None:
        self.events.append(WarEvent(hour, kind, message))
