"""National logistics stockpile.

Each day: domestic production (scaled by surviving industry) + permitted
imports + lend-lease flow into the stockpile, then the armed forces draw
their demand out of it. Fulfillment per supply type drives combat
effectiveness, and running dry on fuel or ammunition is catastrophic rather
than linear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from wargame.core.enums import SupplyType
from wargame.core.mathutil import clamp

# How much each supply class matters to fighting power. Sums to 1.
SUPPLY_WEIGHTS: dict[SupplyType, float] = {
    SupplyType.AMMUNITION: 0.40,
    SupplyType.FUEL: 0.35,
    SupplyType.RATIONS: 0.15,
    SupplyType.SPARE_PARTS: 0.10,
}
# Munitions and spare parts: in wartime these come from abroad only as aid (lend-lease), because
# arming a belligerent is a political decision, not trade. Fuel and food keep flowing commercially.
ARMS: frozenset[SupplyType] = frozenset({SupplyType.AMMUNITION, SupplyType.SPARE_PARTS})
EFFECTIVENESS_FLOOR = 0.10  # A starving, dry army still defends with small arms.
EFFECTIVENESS_CURVE = 1.5   # >1 makes shortages bite progressively harder.

SupplyMap = dict[SupplyType, float]


def _zeros() -> SupplyMap:
    return {s: 0.0 for s in SupplyType}


def _ones() -> SupplyMap:
    return {s: 1.0 for s in SupplyType}


@dataclass
class LogisticsStockpile:
    stocks: SupplyMap = field(default_factory=_zeros)
    base_daily_consumption: SupplyMap = field(default_factory=_zeros)  # At full war tempo.
    base_daily_production: SupplyMap = field(default_factory=_zeros)   # With pre-war industry intact.
    base_daily_imports: SupplyMap = field(default_factory=_zeros)      # Peacetime trade.
    lend_lease_inbound: SupplyMap = field(default_factory=_zeros)      # Filled by War each day, consumed next tick.
    last_fulfillment: SupplyMap = field(default_factory=_ones)

    def tick_day(self, production_factor: float, import_factor: float, tempo: float,
                 arms_import_factor: float = 1.0) -> SupplyMap:
        """Run one day of supply. Returns fulfillment (0..1) per supply type."""
        fulfillment: SupplyMap = {}
        for s in SupplyType:
            imports = import_factor * (arms_import_factor if s in ARMS else 1.0)
            inflow = (
                self.base_daily_production.get(s, 0.0) * production_factor
                + self.base_daily_imports.get(s, 0.0) * imports
                + self.lend_lease_inbound.get(s, 0.0)
            )
            available = self.stocks.get(s, 0.0) + inflow
            demand = self.base_daily_consumption.get(s, 0.0) * tempo
            consumed = min(available, demand)
            self.stocks[s] = available - consumed
            fulfillment[s] = 1.0 if demand <= 0 else consumed / demand
        self.lend_lease_inbound = _zeros()
        self.last_fulfillment = fulfillment
        return fulfillment

    @property
    def supply_ratio(self) -> float:
        """Weighted mean fulfillment: 'how well supplied are we overall'."""
        return sum(SUPPLY_WEIGHTS[s] * self.last_fulfillment.get(s, 1.0) for s in SupplyType)

    def combat_effectiveness(self) -> float:
        """Multiplier on every unit's combat power.

        Weighted *geometric* mean: an army with full rations but zero
        ammunition is not 60% effective, it is near the floor.
        """
        gm = math.prod(clamp(self.last_fulfillment.get(s, 1.0)) ** w for s, w in SUPPLY_WEIGHTS.items())
        return EFFECTIVENESS_FLOOR + (1.0 - EFFECTIVENESS_FLOOR) * math.pow(gm, EFFECTIVENESS_CURVE)

    def shortfall(self, s: SupplyType, production_factor: float, tempo: float = 1.0) -> float:
        """Daily demand that domestic production cannot meet: what aid would have to supply."""
        return max(0.0, self.base_daily_consumption.get(s, 0.0) * tempo - self.base_daily_production.get(s, 0.0) * production_factor)

    def days_of_supply(self, s: SupplyType, tempo: float = 1.0, production_factor: float = 1.0) -> float:
        net_burn = self.base_daily_consumption.get(s, 0.0) * tempo - self.base_daily_production.get(s, 0.0) * production_factor
        if net_burn <= 0:
            return math.inf
        return self.stocks.get(s, 0.0) / net_burn
