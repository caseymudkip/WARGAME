"""Order of Battle (OOB): what a nation can field.

Deliberately thin for now; the ingestion task will flesh out formations and
unit-level equipment. The key modelling choice is already here: quality is
super-linear, so 100 modern tanks can outweigh 300 obsolete ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from wargame.core.enums import Branch

QUALITY_EXPONENT = 1.5


@dataclass
class EquipmentStock:
    name: str
    branch: Branch
    quantity: int
    quality: float           # 0..1 tech level, adjusted for maintenance and training.
    readiness: float = 0.75  # 0..1 fraction actually operational.
    unit_cost: float = 1.0   # For equipment-loss accounting in the cost/reward ledger.
    tonnage: float = 0.0     # Per unit; naval only.
    combat_weight: float = 1.0  # Combat value of one unit relative to one main battle tank.
    stored: int = 0          # In long-term storage: no combat value until refurbished (Country at war).

    @property
    def effective_strength(self) -> float:
        return self.quantity * self.readiness * math.pow(self.quality, QUALITY_EXPONENT) * self.combat_weight


@dataclass
class OrderOfBattle:
    equipment: dict[str, EquipmentStock] = field(default_factory=dict)
    active_personnel: int = 0
    reserve_personnel: int = 0
    mobilizable_manpower: int = 0      # Everyone who could ever be called up.
    sorties_per_airfield: float = 0.0  # Daily sortie generation per intact airfield.
    casualties_total: int = 0
    casualties_today: int = 0

    def branch_power(self, branch: Branch) -> float:
        """Used by the strategic AI to compare branch superiority (e.g. blockade vs invade)."""
        return sum(e.effective_strength for e in self.equipment.values() if e.branch is branch)

    @property
    def naval_tonnage(self) -> float:
        return sum(e.quantity * e.tonnage for e in self.equipment.values() if e.branch is Branch.NAVAL)

    @property
    def casualty_ratio(self) -> float:
        if self.mobilizable_manpower <= 0:
            return 0.0
        return self.casualties_total / self.mobilizable_manpower

    def record_casualties(self, count: int) -> None:
        self.casualties_total += count
        self.casualties_today += count
        self.active_personnel = max(0, self.active_personnel - count)

    def roll_day(self) -> int:
        """Return and reset today's casualty count."""
        today, self.casualties_today = self.casualties_today, 0
        return today
