"""The world registry: the single source of truth for who owns and holds what.

Countries never store their own province lists; they ask the World. All
owner/controller changes must go through `set_controller` /
`transfer_ownership` so the indexes stay consistent.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wargame.world.province import Province

if TYPE_CHECKING:
    from wargame.nation.country import Country


@dataclass
class World:
    provinces: dict[int, Province] = field(default_factory=dict)
    countries: dict[str, Country] = field(default_factory=dict)
    _by_owner: dict[str, set[int]] = field(default_factory=dict, repr=False)
    _by_controller: dict[str, set[int]] = field(default_factory=dict, repr=False)

    # --- registration -------------------------------------------------------

    def add_province(self, province: Province) -> None:
        self.provinces[province.id] = province
        self._by_owner.setdefault(province.owner, set()).add(province.id)
        self._by_controller.setdefault(province.controller, set()).add(province.id)

    def add_provinces(self, provinces: Iterable[Province]) -> None:
        for p in provinces:
            self.add_province(p)

    def add_country(self, country: Country) -> None:
        self.countries[country.tag] = country

    def country(self, tag: str) -> Country:
        return self.countries[tag]

    # --- territory queries --------------------------------------------------

    def owned_by(self, tag: str) -> Iterator[Province]:
        return (self.provinces[pid] for pid in self._by_owner.get(tag, ()))

    def controlled_by(self, tag: str) -> Iterator[Province]:
        return (self.provinces[pid] for pid in self._by_controller.get(tag, ()))

    def owned_value(self, tag: str) -> float:
        return sum(p.strategic_value() for p in self.owned_by(tag))

    def neighboring_countries(self, tag: str) -> set[str]:
        out: set[str] = set()
        for p in self.owned_by(tag):
            for nid in p.neighbors:
                other = self.provinces[nid].owner
                if other != tag:
                    out.add(other)
        return out

    def hops_to_hostile(self, start_pid: int, hostile_tags: frozenset[str], max_hops: int = 12) -> int | None:
        """BFS distance from a province to the nearest hostile-controlled one.

        Used as "strategic depth" when deciding whether industry can be
        evacuated and where to. None means no hostile ground within range.
        """
        seen = {start_pid}
        frontier: deque[tuple[int, int]] = deque([(start_pid, 0)])
        while frontier:
            pid, dist = frontier.popleft()
            if self.provinces[pid].controller in hostile_tags:
                return dist
            if dist >= max_hops:
                continue
            for nid in self.provinces[pid].neighbors:
                if nid not in seen:
                    seen.add(nid)
                    frontier.append((nid, dist + 1))
        return None

    # --- mutations ----------------------------------------------------------

    def set_controller(self, pid: int, tag: str) -> None:
        p = self.provinces[pid]
        if p.controller == tag:
            return
        self._by_controller[p.controller].discard(pid)
        self._by_controller.setdefault(tag, set()).add(pid)
        p.controller = tag

    def transfer_ownership(self, pid: int, tag: str) -> None:
        """Treaty-only. Also hands control to the new owner."""
        p = self.provinces[pid]
        if p.owner != tag:
            self._by_owner[p.owner].discard(pid)
            self._by_owner.setdefault(tag, set()).add(pid)
            p.owner = tag
        self.set_controller(pid, tag)
