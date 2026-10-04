"""Nuclear posture, hesitation, and ballistic missile defence.

Hesitation is a 0..1 sum of named components, so the spectator feed can
always say *why* a nation moved toward (or away from) the button. A nation
only considers launching once hesitation falls below USE_THRESHOLD.

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
        if ctx.enemy_used_nuclear:
            c["retaliation"] = RETALIATION_RELIEF
        desperation = clamp((ctx.collapse_proximity - DESPERATION_ONSET) / (1.0 - DESPERATION_ONSET))
        if desperation > 0:
            weight = EXISTENTIAL_DESPERATION if ctx.existential_threat else LIMITED_DESPERATION
            c["desperation"] = weight * desperation
        if self.leadership_risk_shift:
            c["leadership"] = self.leadership_risk_shift

        hesitation = clamp(sum(c.values()))
        launch_p = 0.0
        if hesitation < USE_THRESHOLD:
            launch_p = MAX_DAILY_LAUNCH_PROBABILITY * (USE_THRESHOLD - hesitation) / USE_THRESHOLD
        self.hesitation = hesitation
        return HesitationAssessment(hesitation, c, launch_p)
