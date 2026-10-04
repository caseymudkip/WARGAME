"""Provinces: the atomic unit of territory.

`owner` is de jure (who it belongs to); `controller` is de facto (whose troops
hold it). Occupation is simply owner != controller. Only treaties change
owner; only combat changes controller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from wargame.core.enums import ProvinceTag, TerrainType


@dataclass(frozen=True)
class TerrainProfile:
    defense_multiplier: float  # Applied to defender combat power.
    movement_cost: float       # Hours-per-km multiplier for movement.
    supply_penalty: float      # Extra fraction of supply lost in transit.


TERRAIN: dict[TerrainType, TerrainProfile] = {
    TerrainType.PLAINS: TerrainProfile(1.00, 1.0, 0.00),
    TerrainType.FOREST: TerrainProfile(1.25, 1.4, 0.05),
    TerrainType.HILLS: TerrainProfile(1.35, 1.5, 0.08),
    TerrainType.MOUNTAINS: TerrainProfile(1.75, 2.5, 0.20),
    TerrainType.MARSH: TerrainProfile(1.40, 2.2, 0.15),
    TerrainType.DESERT: TerrainProfile(1.05, 1.3, 0.12),
    TerrainType.JUNGLE: TerrainProfile(1.50, 2.3, 0.18),
    TerrainType.URBAN: TerrainProfile(1.90, 1.8, 0.00),
    TerrainType.ARCTIC: TerrainProfile(1.30, 2.0, 0.25),
}

# Additive strategic value per tag. The capital dwarfs everything; an empty
# field is barely worth defending. Tune here, not in AI code.
TAG_VALUE: dict[ProvinceTag, float] = {
    ProvinceTag.CAPITAL: 10.0,
    ProvinceTag.URBAN_CENTER: 3.0,
    ProvinceTag.INDUSTRIAL: 2.0,
    ProvinceTag.NAVAL_BASE: 2.0,
    ProvinceTag.ENERGY: 2.0,
    ProvinceTag.PORT: 1.5,
    ProvinceTag.AIRFIELD: 1.5,
    ProvinceTag.RAIL_HUB: 1.0,
    ProvinceTag.FARMLAND: 0.25,
}

BASE_LAND_VALUE = 0.5
INDUSTRY_VALUE_WEIGHT = 0.5
INFRASTRUCTURE_VALUE_WEIGHT = 1.0


@dataclass
class Province:
    id: int
    name: str
    owner: str
    controller: str
    terrain: TerrainType = TerrainType.PLAINS
    infrastructure: float = 0.5        # 0..1 roads/rail/power; scales supply throughput.
    population: int = 0
    industrial_output: float = 0.0     # Abstract production units per day.
    tags: frozenset[ProvinceTag] = frozenset()
    neighbors: tuple[int, ...] = ()
    coastal: bool = False
    damage: float = 0.0                # 0..1 from bombing/fighting; degrades output.
    extra: dict[str, float] = field(default_factory=dict)  # Scenario-specific data hooks.

    @property
    def is_occupied(self) -> bool:
        return self.owner != self.controller

    @property
    def terrain_profile(self) -> TerrainProfile:
        return TERRAIN[self.terrain]

    @property
    def effective_industry(self) -> float:
        return self.industrial_output * (1.0 - self.damage)

    def has(self, tag: ProvinceTag) -> bool:
        return tag in self.tags

    def strategic_value(self) -> float:
        """How much this province matters, to everyone.

        One shared valuation drives AI defence priority, war score, treaty
        costs and capitulation pressure, so the AI fights hardest for exactly
        the ground whose loss would break its nation.
        """
        value = BASE_LAND_VALUE
        value += math.log10(1.0 + self.population / 10_000)
        value += INDUSTRY_VALUE_WEIGHT * self.effective_industry
        value += INFRASTRUCTURE_VALUE_WEIGHT * self.infrastructure
        value += sum(TAG_VALUE.get(t, 0.0) for t in self.tags)
        return value
