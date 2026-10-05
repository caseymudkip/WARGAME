"""War goals: what a war is *for*, and therefore when it can end."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from wargame.core.enums import WarGoalType
from wargame.core.mathutil import clamp
from wargame.world.world import World

EXISTENTIAL_GOALS = frozenset({WarGoalType.REGIME_CHANGE, WarGoalType.TOTAL_CAPITULATION})
PROVINCE_GOALS = frozenset({WarGoalType.BORDER_SKIRMISH, WarGoalType.TERRITORIAL_CONQUEST})
COERCION_SUCCESS_LEVERAGE = 0.75
STRIKE_LEVERAGE_FULL = 0.5   # Half-wrecked key provinces force concessions (Belgrade conceded after 78 days, 1999).


@dataclass(frozen=True)
class WarGoal:
    type: WarGoalType
    holder: str                               # Who pursues the goal (the primary attacker).
    target: str                               # Who it is pursued against.
    province_ids: frozenset[int] = frozenset()  # The objective; for existential goals, demands made on top.

    def __post_init__(self) -> None:
        if self.type in PROVINCE_GOALS and not self.province_ids:
            raise ValueError(f"{self.type.value} war goal needs target provinces")

    @property
    def is_existential(self) -> bool:
        """Does the target fight for its survival? Feeds capitulation and nuclear logic."""
        return self.type in EXISTENTIAL_GOALS

    @property
    def requires_occupation(self) -> bool:
        """False lets the strategic AI pursue the goal by blockade and strikes alone."""
        return self.type is not WarGoalType.COERCION

    def progress(self, world: World, holder_side: Collection[str]) -> float:
        """0..1 how close the holder is to the goal. Drives ticking war score."""
        target = world.country(self.target)
        if target.capitulated:
            return 1.0
        if self.type in PROVINCE_GOALS:
            total = held = 0.0
            for pid in self.province_ids:
                p = world.provinces[pid]
                v = p.strategic_value()
                total += v
                if p.controller in holder_side:
                    held += v
            return held / total if total else 0.0
        if self.type is WarGoalType.REGIME_CHANGE:
            return 1.0 if world.provinces[target.capital_province_id].controller in holder_side else 0.0
        if self.type is WarGoalType.COERCION:
            # A blockade coerces through the shortages and exhaustion it causes, over months, not at once.
            return clamp(max(target.spirit.war_exhaustion / COERCION_SUCCESS_LEVERAGE,
                             target.strategic_damage / STRIKE_LEVERAGE_FULL))
        return target.collapse_progress  # TOTAL_CAPITULATION

    def is_achieved(self, world: World, holder_side: Collection[str]) -> bool:
        if self.type is WarGoalType.TOTAL_CAPITULATION:
            return world.country(self.target).capitulated
        return self.progress(world, holder_side) >= 1.0
