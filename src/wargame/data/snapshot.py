"""Reading the built snapshots (data/snapshots/<year>.json).

A snapshot is plain data. Turning it into engine objects (and every model
assumption that involves) lives in wargame.data.profile.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data"
SNAPSHOT_YEARS = (2021, 2026)


@dataclass(frozen=True)
class CountryRecord:
    iso3: str
    name: str
    region: str | None
    values: dict[str, float | None]
    provenance: dict[str, list[str]]  # source id -> fields it supplied
    confidence: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    nuclear: dict[str, Any] | None = None
    missile_defense: tuple[dict[str, Any], ...] = ()

    def get(self, key: str, default: float = 0.0) -> float:
        v = self.values.get(key)
        return default if v is None else float(v)

    def source_of(self, key: str) -> str | None:
        for source, fields in self.provenance.items():
            if key in fields:
                return source
        return None


@dataclass(frozen=True)
class Snapshot:
    year: int
    as_of: str
    countries: dict[str, CountryRecord]
    pacts: dict[str, dict[str, Any]]
    relations: list[tuple[str, str, float]]
    sources: dict[str, dict[str, Any]]

    def __getitem__(self, iso3: str) -> CountryRecord:
        return self.countries[iso3]

    def pact_partners(self, iso3: str) -> set[str]:
        partners: set[str] = set()
        for pact in self.pacts.values():
            members = pact["members"]
            if iso3 in members:
                partners.update(m for m in members if m != iso3)
        return partners


def load_snapshot(year: int, root: Path | None = None) -> Snapshot:
    root = root or DEFAULT_ROOT
    raw = json.loads((root / "snapshots" / f"{year}.json").read_text())
    sources = json.loads((root / "sources.json").read_text())
    countries = {
        iso: CountryRecord(
            iso3=iso,
            name=c["name"],
            region=c.get("region"),
            values=c["values"],
            provenance=c["provenance"],
            confidence=c.get("confidence", {}),
            notes=tuple(c.get("notes", ())),
            nuclear=c.get("nuclear"),
            missile_defense=tuple(c.get("missile_defense", ())),
        )
        for iso, c in raw["countries"].items()
    }
    relations = [(a, b, float(v)) for a, b, v in raw["relations"]]
    return Snapshot(year, raw["as_of"], countries, raw["pacts"], relations, sources)
