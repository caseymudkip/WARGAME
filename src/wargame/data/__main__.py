"""Inspect countries in a snapshot, with the source of every number.

    python -m wargame.data RUS UKR --year 2026
    python -m wargame.data USA --year 2021 --all
"""

from __future__ import annotations

import argparse

from wargame.data.profile import equipment_quality, regime_type, stability
from wargame.data.snapshot import SNAPSHOT_YEARS, CountryRecord, load_snapshot

KEY_FIELDS = (
    "population", "active_personnel", "reserve_personnel", "tanks", "armored_vehicles", "self_propelled_artillery",
    "rocket_artillery", "fighters", "attack_aircraft", "attack_helicopters", "aircraft_carriers", "submarines",
    "destroyers", "frigates", "defense_budget_usd", "oil_production_bpd", "oil_consumption_bpd",
    "regime_row", "willingness_to_fight_pct",
)


def show(rec: CountryRecord, everything: bool) -> None:
    print(f"\n{rec.name} ({rec.iso3}), {rec.region}")
    print(f"  derived: regime={regime_type(rec).value}, stability={stability(rec):.2f}, "
          f"equipment quality={equipment_quality(rec):.2f}")
    for field in (rec.values if everything else KEY_FIELDS):
        value = rec.values.get(field)
        shown = "n/a" if value is None else f"{value:,}" if isinstance(value, int) else f"{value:,.3f}"
        conf = rec.confidence.get(field)
        print(f"  {field:28s} {shown:>22s}  [{rec.source_of(field)}{', ' + conf if conf else ''}]")
    if rec.nuclear:
        n = rec.nuclear
        print(f"  nuclear: stockpile {n['stockpile']}, deployed {n['deployed']}, doctrine {n['doctrine']} "
              f"[{n['source']}, {n['confidence']}]")
    for d in rec.missile_defense:
        print(f"  BMD: {d['system']} x{d['units']} engages {'/'.join(d['engages'])}, Pk {d['single_shot_pk']} [{d['confidence']}]")
    for note in rec.notes:
        print(f"  note: {note}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("iso3", nargs="+")
    ap.add_argument("--year", type=int, choices=SNAPSHOT_YEARS, default=2026)
    ap.add_argument("--all", action="store_true", help="show every field, not just the key ones")
    args = ap.parse_args()
    snap = load_snapshot(args.year)
    print(f"Snapshot {snap.year} (as of {snap.as_of})")
    for iso in args.iso3:
        show(snap[iso.upper()], args.all)


if __name__ == "__main__":
    main()
