"""Named, time-limited modifiers (HoI4-style "national spirits" / temporary effects).

Every modifier is keyed by a stable id so re-applying the same event refreshes it
instead of stacking, and so the spectator log can explain *why* a value moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Modifier:
    value: float
    expires_hour: int | None = None  # None = permanent until removed.
    decays: bool = False             # Linearly shrink toward 0 by expiry.
    applied_hour: int = 0

    def current_value(self, now_hour: int) -> float:
        if not self.decays or self.expires_hour is None:
            return self.value
        span = max(1, self.expires_hour - self.applied_hour)
        remaining = max(0, self.expires_hour - now_hour)
        return self.value * remaining / span


@dataclass
class ModifierStack:
    """dict[target -> dict[modifier_id -> Modifier]]."""

    entries: dict[str, dict[str, Modifier]] = field(default_factory=dict)

    def apply(
        self,
        target: str,
        modifier_id: str,
        value: float,
        now_hour: int,
        duration_hours: int | None = None,
        decays: bool = False,
    ) -> None:
        expires = None if duration_hours is None else now_hour + duration_hours
        self.entries.setdefault(target, {})[modifier_id] = Modifier(
            value=value, expires_hour=expires, decays=decays, applied_hour=now_hour
        )

    def remove(self, target: str, modifier_id: str) -> None:
        self.entries.get(target, {}).pop(modifier_id, None)

    def total(self, target: str, now_hour: int) -> float:
        return sum(m.current_value(now_hour) for m in self.entries.get(target, {}).values())

    def breakdown(self, target: str, now_hour: int) -> dict[str, float]:
        return {mid: m.current_value(now_hour) for mid, m in self.entries.get(target, {}).items()}

    def prune(self, now_hour: int) -> None:
        for mods in self.entries.values():
            expired = [mid for mid, m in mods.items() if m.expires_hour is not None and m.expires_hour <= now_hour]
            for mid in expired:
                del mods[mid]
