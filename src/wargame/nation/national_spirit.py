"""National spirit: the will of a nation to keep fighting.

Static-ish inputs come from scenario data (patriotism, regime). Dynamic values
(stability, war support, war exhaustion) evolve daily from casualties, lost
ground and shortages. Everything here is 0..1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wargame.core.enums import RegimeType
from wargame.core.mathutil import clamp
from wargame.core.modifiers import ModifierStack


@dataclass(frozen=True)
class RegimeProfile:
    casualty_sensitivity: float  # How much each casualty hurts public will.
    stability_weight: float      # Share of national resolve carried by regime stability...
    war_support_weight: float    # ...versus by public war support.
    repression: float            # Ability to hold stability together despite exhaustion.


# Democracies are held together by public consent; autocracies by regime
# control. patriotism carries the remaining PATRIOTISM_WEIGHT for everyone.
REGIME_PROFILES: dict[RegimeType, RegimeProfile] = {
    RegimeType.LIBERAL_DEMOCRACY: RegimeProfile(0.85, 0.20, 0.40, 0.00),
    RegimeType.FLAWED_DEMOCRACY: RegimeProfile(0.70, 0.25, 0.35, 0.10),
    RegimeType.HYBRID: RegimeProfile(0.55, 0.30, 0.30, 0.30),
    RegimeType.AUTHORITARIAN: RegimeProfile(0.40, 0.40, 0.20, 0.60),
    RegimeType.TOTALITARIAN: RegimeProfile(0.25, 0.50, 0.10, 0.85),
}
PATRIOTISM_WEIGHT = 0.40

LAST_STAND_PATRIOTISM = 0.90  # At or above this (with a functioning state) a nation never surrenders.
LAST_STAND_MIN_STABILITY = 0.50

# Daily dynamics tuning.
EXHAUSTION_PER_CASUALTY_RATIO = 3.0  # x (casualties today / mobilizable manpower).
EXHAUSTION_PER_OCCUPIED_FRACTION = 0.01
EXHAUSTION_PER_SHORTAGE = 0.005
EXHAUSTION_PEACE_RECOVERY = 0.01
WAR_SUPPORT_DRIFT = 0.03             # Fraction of the gap to target closed per day.
WAR_SUPPORT_EXHAUSTION_DRAG = 0.8
STABILITY_EXHAUSTION_RATE = 0.004
STABILITY_SHORTAGE_RATE = 0.004

WAR_SUPPORT = "war_support"
STABILITY = "stability"


@dataclass
class NationalSpirit:
    patriotism: float
    stability: float
    war_support: float
    regime: RegimeType
    war_exhaustion: float = 0.0
    casualty_sensitivity_override: float | None = None
    baseline_war_support: float | None = None  # Defaults to the scenario's starting war_support.
    modifiers: ModifierStack = field(default_factory=ModifierStack)

    def __post_init__(self) -> None:
        if self.baseline_war_support is None:
            self.baseline_war_support = self.war_support

    @property
    def profile(self) -> RegimeProfile:
        return REGIME_PROFILES[self.regime]

    @property
    def casualty_sensitivity(self) -> float:
        if self.casualty_sensitivity_override is not None:
            return self.casualty_sensitivity_override
        return self.profile.casualty_sensitivity

    @property
    def occupation_resistance(self) -> float:
        """Partisan intensity in occupied provinces (consumed by the occupation system)."""
        return clamp(0.15 + 0.85 * self.patriotism)

    def effective_war_support(self, now_hour: int) -> float:
        return clamp(self.war_support + self.modifiers.total(WAR_SUPPORT, now_hour))

    def effective_stability(self, now_hour: int) -> float:
        return clamp(self.stability + self.modifiers.total(STABILITY, now_hour))

    def resolve(self, now_hour: int) -> float:
        """0..1 composite will to fight, weighted by what holds this regime together."""
        p = self.profile
        return clamp(
            PATRIOTISM_WEIGHT * self.patriotism
            + p.stability_weight * self.effective_stability(now_hour)
            + p.war_support_weight * self.effective_war_support(now_hour)
        )

    def fights_to_last_man(self, now_hour: int) -> bool:
        return self.patriotism >= LAST_STAND_PATRIOTISM and self.effective_stability(now_hour) >= LAST_STAND_MIN_STABILITY

    def apply_shock(
        self,
        modifier_id: str,
        now_hour: int,
        war_support: float = 0.0,
        stability: float = 0.0,
        duration_days: int = 30,
        decays: bool = True,
    ) -> None:
        """Temporary event effect (rally-round-the-flag, capital fallen, nuclear strike...)."""
        hours = duration_days * 24
        if war_support:
            self.modifiers.apply(WAR_SUPPORT, modifier_id, war_support, now_hour, hours, decays)
        if stability:
            self.modifiers.apply(STABILITY, modifier_id, stability, now_hour, hours, decays)

    def daily_update(
        self,
        now_hour: int,
        at_war: bool,
        casualty_ratio_today: float,
        occupied_fraction: float,
        supply_ratio: float,
    ) -> None:
        shortage = 1.0 - supply_ratio
        if at_war:
            self.war_exhaustion = clamp(
                self.war_exhaustion
                + EXHAUSTION_PER_CASUALTY_RATIO * casualty_ratio_today * self.casualty_sensitivity
                + EXHAUSTION_PER_OCCUPIED_FRACTION * occupied_fraction
                + EXHAUSTION_PER_SHORTAGE * shortage
            )
        else:
            self.war_exhaustion = clamp(self.war_exhaustion - EXHAUSTION_PEACE_RECOVERY)

        # War support drifts toward a target eroded by exhaustion; patriotism halves the erosion.
        assert self.baseline_war_support is not None
        drag = WAR_SUPPORT_EXHAUSTION_DRAG * self.war_exhaustion * (1.0 - 0.5 * self.patriotism)
        target = clamp(self.baseline_war_support - drag)
        self.war_support = clamp(self.war_support + (target - self.war_support) * WAR_SUPPORT_DRIFT)

        # Stability erodes under exhaustion and shortages; repressive regimes resist.
        erosion = STABILITY_EXHAUSTION_RATE * self.war_exhaustion + STABILITY_SHORTAGE_RATE * shortage
        self.stability = clamp(self.stability - erosion * (1.0 - self.profile.repression))

        self.modifiers.prune(now_hour)
