"""Nuclear posture, hesitation, ballistic missile defence, and the victim's response.

Hesitation is a 0..1 sum of named components, so the spectator feed can
always say *why* a nation moved toward (or away from) the button. A nation
only considers launching once hesitation falls below USE_THRESHOLD.

Two roads lead below the threshold:
  desperation  a nation facing collapse reaches for its last resort;
  compellence  a much stronger nation "seals the deal" against a smaller,
               stubborn, non-nuclear enemy that refuses to yield (the 1945
               pattern). Outside a vacuum the diplomatic fallout keeps this
               rare or absent; inside a vacuum it is a real option, which is
               consistent with survey evidence that publics weigh their own
               soldiers' lives over enemy civilians (Sagan & Valentino 2017:
               ~60% of Americans approved a nuclear strike killing 2 million
               Iranian civilians to avoid 20,000 US combat deaths).
Use stays sparing: every prior strike adds hesitation, and coercive strikes
are spaced out to give the victim time to yield (Hiroshima to Nagasaki: 3 days).

BMD performance numbers are scenario data, never hardcoded here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from wargame.core.enums import MissileClass, NuclearDoctrine
from wargame.core.escalation import EscalationPolicy
from wargame.core.mathutil import clamp

DOCTRINE_BASE_HESITATION: dict[NuclearDoctrine, float] = {
    NuclearDoctrine.NO_FIRST_USE: 0.85,
    NuclearDoctrine.ASSURED_RETALIATION: 0.85,
    NuclearDoctrine.FLEXIBLE_RESPONSE: 0.72,
    NuclearDoctrine.ESCALATE_TO_DEESCALATE: 0.55,
}
NO_FIRST_USE_TABOO = 0.30        # Extra hesitation for NFU states until the enemy goes nuclear.
RETALIATION_RELIEF = -0.60       # The enemy already crossed the line.
FALLOUT_WEIGHT = 0.15            # x policy multiplier x (0.5 + trade dependence)
MAD_SECOND_STRIKE = 0.30
MAD_FIRST_STRIKE_ONLY = 0.15
EXTENDED_DETERRENCE = 0.20       # A nuclear-armed pact member stands behind the enemy (Tier 3).
EXISTENTIAL_DESPERATION = -0.45
LIMITED_DESPERATION = -0.20
DESPERATION_ONSET = 0.60         # Fraction of the way to capitulation where desperation begins.
PATRON_INTERVENTION = 0.30       # x enemy patrons' power / own power (Tier 2: launching may bring them in).
COMPELLENCE_WEIGHT = 0.35        # Max hesitation removed by a stalled war against a weak, defiant enemy.
LIMITED_GOAL_COMPELLENCE = 0.3   # A border war is rarely worth breaking the taboo.
COMPELLENCE_MIN_POWER_RATIO = 1.5
COMPELLENCE_FULL_POWER_RATIO = 6.0
SPARING_PER_STRIKE = 0.10        # Each prior strike makes the next harder to authorise...
SPARING_PER_STRIKE_VACUUM = 0.05 # ...less so when nobody outside is watching.

USE_THRESHOLD = 0.30
MAX_DAILY_LAUNCH_PROBABILITY = 0.25


@dataclass
class MissileDefenseSystem:
    name: str
    engages: frozenset[MissileClass]
    single_shot_pk: float                          # Scenario data. Probability one interceptor kills one warhead.
    interceptors: int
    shots_per_target: int = 2                      # Shoot-shoot doctrine.
    covered_provinces: frozenset[int] = frozenset()  # Empty = national coverage.
    batteries: int = 0                             # Fire units (battalions/batteries); air defence weight.

    def covers(self, province_id: int) -> bool:
        return not self.covered_provinces or province_id in self.covered_provinces

    def intercept_probability(self, missile: MissileClass, province_id: int) -> float:
        if missile not in self.engages or not self.covers(province_id) or self.interceptors <= 0:
            return 0.0
        shots = min(self.shots_per_target, self.interceptors)
        return 1.0 - (1.0 - self.single_shot_pk) ** shots

    def expend(self, missile: MissileClass, province_id: int) -> None:
        if self.intercept_probability(missile, province_id) > 0:
            self.interceptors -= min(self.shots_per_target, self.interceptors)


def layered_intercept_probability(
    systems: Iterable[MissileDefenseSystem], missile: MissileClass, province_id: int
) -> float:
    """Independent layers (e.g. exo-atmospheric then terminal): 1 - prod(leak_i)."""
    leak = 1.0
    for s in systems:
        leak *= 1.0 - s.intercept_probability(missile, province_id)
    return 1.0 - leak


@dataclass(frozen=True)
class NuclearContext:
    """Everything outside the country that bears on the decision; built by War."""

    weapons_enabled: bool
    policy: EscalationPolicy
    collapse_proximity: float     # Own capitulation pressure / threshold. >=1 means collapsing.
    existential_threat: bool
    enemy_used_nuclear: bool
    enemy_warheads: int
    enemy_second_strike: bool
    enemy_nuclear_umbrella: bool  # Nuclear-armed pact partner behind the enemy.
    own_bmd_vs_retaliation: float # Chance our BMD stops an enemy retaliatory warhead.
    trade_dependence: float
    enemy_patron_power_ratio: float = 0.0  # Military power of the enemy's outside backers / ours.
    pursuing_goal: bool = False            # We hold the war goal (we are the ones trying to win something).
    goal_existential: bool = False         # Our goal is regime change / total capitulation.
    power_ratio: float = 1.0               # Our side's military power / enemy side's.
    stubbornness: float = 0.0              # 0..1: the war has dragged on and the enemy is nowhere near yielding.
    own_prior_strikes: int = 0


@dataclass(frozen=True)
class HesitationAssessment:
    hesitation: float
    components: dict[str, float]
    launch_probability: float     # Per-day probability of authorising a strike.

    @property
    def considering_use(self) -> bool:
        return self.launch_probability > 0.0


@dataclass
class NuclearPosture:
    warheads: int = 0
    delivery: frozenset[MissileClass] = frozenset()
    doctrine: NuclearDoctrine = NuclearDoctrine.ASSURED_RETALIATION
    second_strike_capable: bool = False
    missile_defenses: list[MissileDefenseSystem] = field(default_factory=list)
    leadership_risk_shift: float = 0.0  # Scenario data: +cautious / -reckless leadership.
    hesitation: float = 1.0             # Last evaluated value (1.0 = will not launch).

    @property
    def is_nuclear_power(self) -> bool:
        return self.warheads > 0 and bool(self.delivery)

    def intercept_probability(self, missile: MissileClass, province_id: int) -> float:
        return layered_intercept_probability(self.missile_defenses, missile, province_id)

    def evaluate_hesitation(self, ctx: NuclearContext) -> HesitationAssessment:
        if not ctx.weapons_enabled or not self.is_nuclear_power:
            self.hesitation = 1.0
            return HesitationAssessment(1.0, {"unavailable": 1.0}, 0.0)

        c: dict[str, float] = {"doctrine": DOCTRINE_BASE_HESITATION[self.doctrine]}
        if self.doctrine is NuclearDoctrine.NO_FIRST_USE and not ctx.enemy_used_nuclear:
            c["no_first_use_pledge"] = NO_FIRST_USE_TABOO
        c["escalation_tier"] = ctx.policy.nuclear_hesitation_shift
        if ctx.policy.nuclear_fallout_multiplier > 0:
            c["diplomatic_fallout"] = FALLOUT_WEIGHT * ctx.policy.nuclear_fallout_multiplier * (0.5 + ctx.trade_dependence)
        if ctx.enemy_warheads > 0:
            mad = MAD_SECOND_STRIKE if ctx.enemy_second_strike else MAD_FIRST_STRIKE_ONLY
            c["mutually_assured_destruction"] = mad * (1.0 - ctx.own_bmd_vs_retaliation)
        if ctx.enemy_nuclear_umbrella and ctx.policy.alliances_trigger:
            c["extended_deterrence"] = EXTENDED_DETERRENCE
        if ctx.enemy_patron_power_ratio > 0 and ctx.policy.nuclear_use_escalates_to is not None:
            c["patron_intervention"] = PATRON_INTERVENTION * clamp(ctx.enemy_patron_power_ratio)
        if ctx.enemy_used_nuclear:
            c["retaliation"] = RETALIATION_RELIEF
        desperation = clamp((ctx.collapse_proximity - DESPERATION_ONSET) / (1.0 - DESPERATION_ONSET))
        if desperation > 0:
            weight = EXISTENTIAL_DESPERATION if ctx.existential_threat else LIMITED_DESPERATION
            c["desperation"] = weight * desperation
        if ctx.pursuing_goal and ctx.enemy_warheads == 0 and ctx.stubbornness > 0:
            asymmetry = clamp(
                (ctx.power_ratio - COMPELLENCE_MIN_POWER_RATIO) / (COMPELLENCE_FULL_POWER_RATIO - COMPELLENCE_MIN_POWER_RATIO)
            )
            weight = COMPELLENCE_WEIGHT * (1.0 if ctx.goal_existential else LIMITED_GOAL_COMPELLENCE)
            if asymmetry > 0:
                c["compellence"] = -weight * asymmetry * ctx.stubbornness
        if ctx.own_prior_strikes:
            per_strike = SPARING_PER_STRIKE if ctx.policy.nuclear_fallout_multiplier > 0 else SPARING_PER_STRIKE_VACUUM
            c["sparing_use"] = per_strike * ctx.own_prior_strikes
        if self.leadership_risk_shift:
            c["leadership"] = self.leadership_risk_shift

        hesitation = clamp(sum(c.values()))
        launch_p = 0.0
        if hesitation < USE_THRESHOLD:
            launch_p = MAX_DAILY_LAUNCH_PROBABILITY * (USE_THRESHOLD - hesitation) / USE_THRESHOLD
        self.hesitation = hesitation
        return HesitationAssessment(hesitation, c, launch_p)


# ---------------------------------------------------------------------------
# The victim's response: collapse, or a rally for revenge
# ---------------------------------------------------------------------------
#
# Small or unstable nations break (Japan surrendered within a week of
# Hiroshima, though historians such as Hasegawa weight the simultaneous Soviet
# entry as heavily as the bombs). A peer that can strike back is devastated
# but rallies for revenge, as publics did after Pearl Harbor and 9/11.

RETALIATION_RESILIENCE = 0.65  # Being able to answer in kind is the main source of resilience...
SIZE_RESILIENCE = 0.35         # ...size relative to the attacker is the rest.
RALLY_THRESHOLD = 0.50
REVENGE_RALLY = 0.25           # x resilience, war support.
DEVASTATION_STABILITY = 0.05
COLLAPSE_WAR_SUPPORT = 0.35    # x vulnerability.
COLLAPSE_STABILITY = 0.45      # x vulnerability. Two strikes can break even a last-stand nation.
COLLAPSE_SHOCK = 0.60          # x vulnerability, added to capitulation pressure (decays daily).


@dataclass(frozen=True)
class NuclearShockResponse:
    rally: bool
    resilience: float
    war_support: float
    stability: float
    capitulation_shock: float


def nuclear_shock_response(*, can_retaliate: bool, population_ratio: float, stability: float) -> NuclearShockResponse:
    """How a nation's will reacts to a nuclear detonation on its soil.

    population_ratio is victim population / attacker population.
    """
    resilience = clamp(RETALIATION_RESILIENCE * float(can_retaliate) + SIZE_RESILIENCE * clamp(population_ratio))
    if resilience >= RALLY_THRESHOLD:
        return NuclearShockResponse(True, resilience, REVENGE_RALLY * resilience, -DEVASTATION_STABILITY, 0.0)
    vulnerability = clamp((1.0 - resilience) * (0.6 + 0.4 * (1.0 - stability)))
    return NuclearShockResponse(
        rally=False,
        resilience=resilience,
        war_support=-COLLAPSE_WAR_SUPPORT * vulnerability,
        stability=-COLLAPSE_STABILITY * vulnerability,
        capitulation_shock=COLLAPSE_SHOCK * vulnerability,
    )
