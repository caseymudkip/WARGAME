"""Simulation time.

The engine always advances in fixed 1-hour ticks. The spectator's speed
setting only changes how many ticks are run per wall-clock second; it never
changes the simulation's resolution. That guarantees the same scenario plays
out identically whether it is watched hour-by-hour or week-by-week.

Systems subscribe at a cadence:
    HOURLY  combat, movement, air sorties, strike resolution
    DAILY   logistics, economy, morale, war score, capitulation, peace checks
    WEEKLY  diplomacy re-evaluation, lend-lease reviews
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, IntEnum

HOURS_PER_DAY = 24
HOURS_PER_WEEK = 24 * 7


class Cadence(IntEnum):
    HOURLY = 1
    DAILY = HOURS_PER_DAY
    WEEKLY = HOURS_PER_WEEK


class TimeScale(Enum):
    """Spectator playback speed, in simulated hours per wall-clock second."""

    PAUSED = 0.0
    HOUR_BY_HOUR = 1.0
    SIX_HOURS = 6.0
    DAY_BY_DAY = float(HOURS_PER_DAY)
    WEEK_BY_WEEK = float(HOURS_PER_WEEK)
    MONTH_BY_MONTH = float(30 * HOURS_PER_DAY)


@dataclass
class SimClock:
    start: datetime
    hours_elapsed: int = 0

    @property
    def now(self) -> datetime:
        return self.start + timedelta(hours=self.hours_elapsed)

    @property
    def day(self) -> int:
        return self.hours_elapsed // HOURS_PER_DAY

    def advance(self) -> list[Cadence]:
        """Advance one hour and return every cadence that fires on the new hour."""
        self.hours_elapsed += 1
        return [c for c in Cadence if self.hours_elapsed % c == 0]


@dataclass
class SpeedController:
    """Converts wall-clock frame time into a whole number of sim ticks."""

    scale: TimeScale = TimeScale.DAY_BY_DAY
    _carry: float = 0.0

    def ticks_for_frame(self, real_seconds: float) -> int:
        self._carry += real_seconds * self.scale.value
        ticks = int(self._carry)
        self._carry -= ticks
        return ticks
