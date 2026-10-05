"""Build data/snapshots/{2021,2026}.json from the vendored raw inputs and curated files.

    python tools/data/build_dataset.py

Stdlib only and deterministic. Every value in a snapshot records which source
supplied it (`provenance`), and every judgement call made here is written into
the country's `notes`. Nothing in this script invents a number: values are
either copied from a source, mapped between identical concepts, or flagged as
missing (null). Derived quantities (stability, equipment quality, supply rates)
are computed later by the engine's loader, where the formulas are documented.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
CURATED = DATA / "curated"
OUT = DATA / "snapshots"

SNAPSHOTS = {
    2021: {"as_of": "2021-01-01", "edition": 2022, "vdem_year": 2020},
    2026: {"as_of": "2026-01-01", "edition": 2026, "vdem_year": 2025},
}

# --- country identity --------------------------------------------------------------
# V-Dem's country_text_id is ISO 3166-1 alpha-3 for every country used here; names
# from the other sources are mapped onto V-Dem names, then aliases below.
NAME_ALIASES = {
    "United States": "USA", "Myanmar": "MMR", "Turkiye": "TUR", "Turkey": "TUR",
    "Beliz": "BLZ", "Belize": "BLZ", "Gambia": "GMB", "Burma/Myanmar": "MMR",
    "United States of America": "USA", "Czech Republic": "CZE", "Cote d'Ivoire": "CIV",
    "Democratic Republic of Congo": "COD", "Congo": "COG", "Timor": "TLS", "Eswatini": "SWZ",
    "Bosnia-Herzegovina": "BIH", "Macedonia": "MKD", "Kosovo": "XKX",
}

GFP2026_FIELDS = {
    "global_firepower_rank": "gfp_rank", "power_index": "gfp_power_index",
    "total_population": "population", "total_military_manpower": "available_manpower",
    "fit_for_service": "fit_for_service", "population_reaching_military_age_annually": "reaching_military_age",
    "active_personnel": "active_personnel", "reserve_personnel": "reserve_personnel",
    "paramilitary": "paramilitary_personnel",
    "total_military_aircraft": "aircraft_total", "fighter_aircraft": "fighters", "attack_aircraft": "attack_aircraft",
    "transport_aircraft": "transport_aircraft", "trainer_aircraft": "trainer_aircraft",
    "special_mission_aircraft": "special_mission_aircraft", "tanker_aircraft": "tanker_aircraft",
    "total_military_helicopters": "helicopters", "attack_helicopters": "attack_helicopters",
    "tanks": "tanks", "armored_fighting_vehicles": "armored_vehicles",
    "self_propelled_artillery": "self_propelled_artillery", "towed_artillery": "towed_artillery",
    "rocket_projectors": "rocket_artillery",
    "total_naval_fleet": "naval_fleet_total", "total_naval_fleet_tonnage_mt": "naval_tonnage",
    "aircraft_carriers": "aircraft_carriers", "helicopter_carriers": "helicopter_carriers",
    "submarines": "submarines", "destroyers": "destroyers", "frigates": "frigates", "corvettes": "corvettes",
    "coastal_patrol_craft": "patrol_vessels", "mine_warfare_craft": "mine_warfare",
    "defense_budget_usd": "defense_budget_usd", "external_debt_usd": "external_debt_usd",
    "purchasing_power_parity_usd": "ppp_usd", "foreign_exchange_and_gold_reserves_usd": "fx_gold_reserves_usd",
    "total_serviceable_airports": "airports", "labour_force": "labor_force", "major_ports_and_terminals": "ports",
    "total_merchant_marine_fleet": "merchant_marine", "railway_coverage_km": "railway_km",
    "roadway_coverage_km": "roadway_km", "oil_production_bbl": "oil_production_bpd",
    "oil_consumption_bbl": "oil_consumption_bpd", "proven_oil_reserves_bbl": "oil_reserves_bbl",
    "natural_gas_production_cum": "gas_production_m3", "natural_gas_consumption_cum": "gas_consumption_m3",
    "proven_natural_gas_reserves_cum": "gas_reserves_m3", "total_land_area_sq_km": "land_area_km2",
    "coastline_coverage_km": "coastline_km", "border_coverage_km": "border_km", "waterway_coverage_km": "waterway_km",
}
GFP2022_FIELDS = {
    "PowerIndex": "gfp_power_index", "Total Population": "population", "Available Manpower": "available_manpower",
    "Fit-for-Service": "fit_for_service", "Reaching Mil Age Annually": "reaching_military_age",
    "Active Personnel": "active_personnel", "Reserve Personnel": "reserve_personnel",
    "Paramilitary": "paramilitary_personnel",
    "Total Aircraft Strength": "aircraft_total", "Dedicated Attack": "attack_aircraft",
    "Transports": "transport_aircraft", "Trainers": "trainer_aircraft", "Special-Mission": "special_mission_aircraft",
    "Tanker Fleet": "tanker_aircraft", "Helicopters": "helicopters", "Attack Helicopters": "attack_helicopters",
    "Tanks": "tanks", "Armored Vehicles": "armored_vehicles", "Self-Propelled Artillery": "self_propelled_artillery",
    "Towed Artillery": "towed_artillery", "Rocket Projectors": "rocket_artillery",
    "Navy Ships": "naval_fleet_total", "Aircraft Carriers": "aircraft_carriers",
    "Helicopter Carriers": "helicopter_carriers", "Submarines": "submarines", "Destroyers": "destroyers",
    "Frigates": "frigates", "Corvettes": "corvettes", "Patrol Vessels": "patrol_vessels", "Mine Warfare": "mine_warfare",
    "Defense Budget($)": "defense_budget_usd", "External Debt": "external_debt_usd",
    "Purchasing Power Parity": "ppp_usd", "Foreign Exchange/Gold": "fx_gold_reserves_usd",
    "Labor Force": "labor_force", "Ports / Trade Terminals": "ports", "Merchant Marine Fleet": "merchant_marine",
    "Railway Coverage": "railway_km", "Roadway Coverage": "roadway_km", "Waterways (usable)": "waterway_km",
    "Oil Production": "oil_production_bpd", "Oil Consumption": "oil_consumption_bpd",
    "Oil Proven Reserves": "oil_reserves_bbl", "Square Land Area": "land_area_km2",
    "Coastline Coverage": "coastline_km", "Shared Borders": "border_km",
    # "Fighters/Interceptors" is deliberately absent: corrupted in this copy (see sources.json).
}
VDEM_FIELDS = {
    "v2x_regime": "regime_row", "v2x_libdem": "liberal_democracy_index", "v2x_civlib": "civil_liberties_index",
    "v2caviol": "political_violence", "v2svstterr": "territorial_control_pct", "v2stfisccap": "fiscal_capacity",
}
# Zero in these GFP fields means "not reported" rather than a true zero.
ZERO_IS_MISSING = {"naval_tonnage"}

# Hard plausibility bounds, comfortably above the largest real value in the world. A value above
# its bound is a data error (e.g. a spreadsheet date-parse turning a cell into "2022") and is
# rejected to null with a note, never silently kept.
PLAUSIBLE_MAX = {
    "aircraft_carriers": 15, "helicopter_carriers": 15, "submarines": 120, "destroyers": 150,
    "frigates": 120, "corvettes": 200, "fighters": 5_000, "attack_aircraft": 3_000,
    "attack_helicopters": 2_000, "tanks": 30_000, "active_personnel": 3_000_000,
}
# The 2025 edition has the 2026 layout minus the power-index columns.
GFP2025_FIELDS = {c: f for c, f in GFP2026_FIELDS.items() if c not in ("global_firepower_rank", "power_index")}

# Fields compared with the nearest other edition. One year apart (2025 vs 2026) a 2x change is
# suspicious; four years apart, across a major war (2022 vs 2025), only a 10x change is.
EDITION_COMPARED = (
    "active_personnel", "reserve_personnel", "tanks", "self_propelled_artillery", "towed_artillery",
    "rocket_artillery", "aircraft_total", "fighters", "attack_aircraft", "attack_helicopters",
    "submarines", "destroyers", "frigates", "corvettes",
)
CHANGE_FACTOR = {2026: 2.0, 2021: 10.0}
CHANGE_MIN_UNITS = 20

# Budgets are checked against SIPRI's audited 2020 figure (constant 2019 USD), scaled to the snapshot
# year's nominal dollars. Outside x/3..3x a figure is treated as suspect.
SIPRI_NOMINAL_SCALE = {2021: 1.05, 2026: 1.25}
BUDGET_TOLERANCE = 3.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(raw: str | None) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def read_csv(name: str) -> list[dict[str, str]]:
    with open(RAW / name) as f:
        return list(csv.DictReader(f))


def resolve(name: str, vdem_names: dict[str, str]) -> str:
    if name in NAME_ALIASES:
        return NAME_ALIASES[name]
    if name in vdem_names:
        return vdem_names[name]
    raise SystemExit(f"Unmapped country name: {name!r}. Add it to NAME_ALIASES.")


class Inputs:
    """Every raw and curated input, keyed by ISO3."""

    def __init__(self) -> None:
        vdem_rows = read_csv("vdem_v16_subset.csv")
        self.vdem_names = {r["country_name"]: r["country_text_id"] for r in vdem_rows}
        self.vdem = {(r["country_text_id"], int(r["year"])): r for r in vdem_rows}
        self.gfp = {
            edition: {resolve(r["country"], self.vdem_names): r for r in read_csv(f"gfp_{edition}.csv")}
            for edition in (2022, 2025, 2026)
        }
        self.sipri_2020: dict[str, tuple[float, float | None]] = {}
        for r in read_csv("sipri_milex_2015_2020.csv"):
            iso = NAME_ALIASES.get(r["Entity"]) or self.vdem_names.get(r["Entity"])
            if iso and r["Year"] == "2020" and r["military_expenditure"]:
                self.sipri_2020[iso] = (float(r["military_expenditure"]), number(r["military_expenditure_share_gdp"]))
        self.willingness = load_json(CURATED / "willingness_to_fight.json")
        self.nuclear = load_json(CURATED / "nuclear.json")
        self.bmd = load_json(CURATED / "missile_defense.json")
        self.alliances = load_json(CURATED / "alliances.json")
        self.overrides = load_json(CURATED / "overrides.json")

    def gfp_value(self, edition: int, iso: str, field: str) -> float | None:
        """A cleaned value from any GFP edition (zero-as-missing and plausibility rules applied)."""
        row = self.gfp[edition].get(iso)
        field_map = {2022: GFP2022_FIELDS, 2025: GFP2025_FIELDS, 2026: GFP2026_FIELDS}[edition]
        col = next((c for c, f in field_map.items() if f == field), None)
        if row is None or col is None:
            return None
        v = number(row[col])
        if field in ZERO_IS_MISSING and v == 0:
            return None
        bound = PLAUSIBLE_MAX.get(field)
        return None if v is not None and bound is not None and v > bound else v


class Record:
    def __init__(self, iso: str) -> None:
        self.iso = iso
        self.values: dict[str, Any] = {}
        self.prov: dict[str, list[str]] = {}
        self.notes: list[str] = []
        self.confidence: dict[str, str] = {}

    def put(self, field: str, value: Any, source: str) -> None:
        for fields in self.prov.values():
            if field in fields:
                fields.remove(field)
        self.prov = {s: f for s, f in self.prov.items() if f}
        self.values[field] = value
        self.prov.setdefault(source, []).append(field)


# --- stages ------------------------------------------------------------------------------------

def stage_baseline(rec: Record, src: Inputs, snapshot: int) -> None:
    edition = SNAPSHOTS[snapshot]["edition"]
    field_map = GFP2026_FIELDS if edition == 2026 else GFP2022_FIELDS
    row = src.gfp[edition][rec.iso]
    for col, field in field_map.items():
        raw = number(row[col])
        bound = PLAUSIBLE_MAX.get(field)
        if raw is not None and bound is not None and raw > bound:
            rec.notes.append(f"rejected implausible {field}={raw} from gfp{edition} (bound {bound})")
        rec.put(field, src.gfp_value(edition, rec.iso, field), f"gfp{edition}")


def stage_armour_definition(rec: Record, src: Inputs) -> None:
    """GFP 2025/2026 count every military vehicle as 'armored' (US 409,660 vs 45,193 in 2022)."""
    rec.put("military_vehicles", rec.values["armored_vehicles"], "gfp2026")
    afv_2022 = src.gfp_value(2022, rec.iso, "armored_vehicles")
    tanks_2022, tanks_2026 = src.gfp_value(2022, rec.iso, "tanks"), rec.values.get("tanks")
    if afv_2022 is None:
        rec.put("armored_vehicles", None, "model")
        return
    trend = min(4.0, max(0.25, tanks_2026 / tanks_2022)) if tanks_2022 and tanks_2026 else 1.0
    rec.put("armored_vehicles", round(afv_2022 * trend), "model")
    rec.notes.append("armored_vehicles estimated: 2022-edition AFV count x tank trend to 2026 "
                     "(GFP 2026 'armored vehicles' covers all military vehicles; kept as military_vehicles)")


# Never gap-filled from 2025: estimated fields, and fields whose GFP definition is the inflated one.
NOT_FILLED = {"armored_vehicles", "military_vehicles"}


def stage_fill_from_2025(rec: Record, src: Inputs) -> None:
    estimated = set(rec.prov.get("model", []))
    for field in list(rec.values):
        if field in NOT_FILLED or field in estimated:
            continue
        if rec.values[field] is None and rec.iso in src.gfp[2025]:
            earlier = src.gfp_value(2025, rec.iso, field)
            if earlier is not None:
                rec.put(field, earlier, "gfp2025")
                rec.notes.append(f"{field} missing from the 2026 edition; filled from 2025 ({earlier})")


def stage_2021_specifics(rec: Record, src: Inputs) -> None:
    # The 2022 copy's fighter column is corrupted: estimate from the 2025 fighter share (closest later edition).
    total, total_25, fighters_25 = (rec.values.get("aircraft_total"), src.gfp_value(2025, rec.iso, "aircraft_total"),
                                    src.gfp_value(2025, rec.iso, "fighters"))
    if total is not None and total_25:
        rec.put("fighters", min(total, round(total * (fighters_25 or 0) / total_25)), "model")
        rec.notes.append("fighters estimated: 2025 fighter share of total aircraft applied to the 2022-edition fleet")
    else:
        rec.put("fighters", None, "model")
    rec.put("naval_tonnage", None, "gfp2022")
    if rec.iso in src.sipri_2020:
        spend, share_gdp = src.sipri_2020[rec.iso]
        rec.put("milex_sipri_usd_2019", round(spend), "sipri_milex")
        rec.put("milex_share_gdp_pct", share_gdp, "sipri_milex")
    if rec.iso == "AFG":
        rec.notes.append("GFP 2022 already reflects the August 2021 collapse of the Afghan National Army (active personnel 0).")


def stage_budget(rec: Record, src: Inputs, snapshot: int) -> None:
    """Reconcile the GFP budget with SIPRI; prefer the 2025 edition when only 2026 contradicts SIPRI."""
    budget = rec.values.get("defense_budget_usd")
    if not budget or rec.iso not in src.sipri_2020:
        return
    anchor = src.sipri_2020[rec.iso][0] * SIPRI_NOMINAL_SCALE[snapshot]
    if anchor <= 0:
        return  # SIPRI reports zero (no armed forces or no estimate): no usable anchor.

    def consistent(v: float | None) -> bool:
        return v is not None and anchor / BUDGET_TOLERANCE <= v <= anchor * BUDGET_TOLERANCE

    if consistent(budget):
        return
    if snapshot == 2026:
        alt = src.gfp_value(2025, rec.iso, "defense_budget_usd")
        if consistent(alt):
            rec.put("defense_budget_usd", alt, "gfp2025")
            rec.notes.append(f"defense_budget_usd: 2026 edition's {budget:,.0f} is {budget / anchor:.1f}x SIPRI's 2020 level; "
                             f"used the 2025 edition's {alt:,.0f}, which is consistent with SIPRI")
            return
    rec.notes.append(f"defense_budget_usd {budget:,.0f} is {budget / anchor:.1f}x SIPRI's 2020 level; "
                     "plausible only if spending changed drastically (e.g. war), verify")


def stage_edition_notes(rec: Record, src: Inputs, snapshot: int) -> None:
    other = 2025
    if rec.iso not in src.gfp[other]:
        return
    factor = CHANGE_FACTOR[snapshot]
    for field in EDITION_COMPARED:
        here, there = rec.values.get(field), src.gfp_value(other, rec.iso, field)
        if here is None or there is None or rec.prov.get("model") and field in rec.prov["model"]:
            continue
        lo, hi = sorted((here, there))
        if hi - lo >= CHANGE_MIN_UNITS and (lo == 0 or hi / lo >= factor):
            edition = SNAPSHOTS[snapshot]["edition"]
            rec.notes.append(f"{field} changes sharply between GFP editions ({edition}: {here}, {other}: {there}); "
                             "a reporting or definitional change, or an error in one edition: verify")


def stage_consistency_notes(rec: Record) -> None:
    v = rec.values
    parts = sum(v.get(k) or 0 for k in ("aircraft_carriers", "helicopter_carriers", "submarines", "destroyers",
                                         "frigates", "corvettes", "patrol_vessels", "mine_warfare"))
    if v.get("naval_fleet_total") is not None and parts > v["naval_fleet_total"]:
        rec.notes.append(f"naval sub-types sum to {parts}, above naval_fleet_total {v['naval_fleet_total']}")


def stage_politics(rec: Record, src: Inputs, snapshot: int) -> None:
    row = src.vdem.get((rec.iso, SNAPSHOTS[snapshot]["vdem_year"]))
    for col, field in VDEM_FIELDS.items():
        rec.put(field, number(row[col]) if row else None, "vdem_v16")
    if not row:
        rec.notes.append("not covered by V-Dem: regime and stability fall back to engine defaults")
    wtf = src.willingness
    own = wtf["countries"].get(rec.iso)
    if own:
        rec.put("willingness_to_fight_pct", own["pct"], "gallup_eoy2023")
        rec.confidence["willingness_to_fight_pct"] = own["confidence"]
        return
    for region, agg in wtf["regional_aggregates"].items():
        if rec.iso in agg["members"]:
            rec.put("willingness_to_fight_pct", agg["pct"], f"gallup_eoy2023:{region}_aggregate")
            rec.confidence["willingness_to_fight_pct"] = "low"
            return
    rec.put("willingness_to_fight_pct", wtf["global"]["pct"], "gallup_eoy2023:global_aggregate")
    rec.confidence["willingness_to_fight_pct"] = "very_low"


def stage_overrides(rec: Record, src: Inputs, snapshot: int) -> None:
    for field, o in src.overrides.get(str(snapshot), {}).get(rec.iso, {}).items():
        old = rec.values.get(field)
        rec.put(field, o["value"], "override")
        rec.confidence[field] = o.get("confidence", "medium")
        rec.notes.append(f"{field} overridden ({old} -> {o['value']}): {o['source']}")


def build(snapshot: int) -> dict[str, Any]:
    src = Inputs()
    edition = SNAPSHOTS[snapshot]["edition"]
    nuclear, bmd = src.nuclear[str(snapshot)], src.bmd["deployments"][str(snapshot)]
    countries: dict[str, Any] = {}
    for iso, row in src.gfp[edition].items():
        rec = Record(iso)
        stage_baseline(rec, src, snapshot)
        if snapshot == 2026:
            stage_armour_definition(rec, src)
            stage_fill_from_2025(rec, src)
        else:
            stage_2021_specifics(rec, src)
        stage_budget(rec, src, snapshot)
        stage_edition_notes(rec, src, snapshot)
        stage_consistency_notes(rec)
        stage_politics(rec, src, snapshot)
        stage_overrides(rec, src, snapshot)

        region_row = src.gfp[2025].get(iso)
        entry: dict[str, Any] = {
            "name": "Belize" if row["country"] == "Beliz" else row["country"],
            "region": region_row["region"] if region_row else row.get("Continent"),
            "values": rec.values,
            "provenance": rec.prov,
            "confidence": rec.confidence,
            "notes": rec.notes,
        }
        if iso in nuclear:
            n = nuclear[iso]
            entry["nuclear"] = {**{k: n[k] for k in ("stockpile", "deployed", "total_inventory")},
                                **src.nuclear["doctrines"][iso], "confidence": n["confidence"],
                                "source": nuclear["_source"], "note": n.get("note")}
        if iso in bmd:
            entry["missile_defense"] = [{**d, **{k: src.bmd["systems"][d["system"]][k] for k in
                                                  ("engages", "single_shot_pk", "shots_per_target", "interceptors_per_unit")}}
                                        for d in bmd[iso]]
        countries[iso] = entry

    unknown = sorted({iso for iso in list(nuclear) + list(bmd) if not iso.startswith("_") and iso not in countries})
    inputs = sorted(p for p in list(RAW.glob("*.csv")) + list(CURATED.glob("*.json")))
    return {
        "snapshot": snapshot,
        "as_of": SNAPSHOTS[snapshot]["as_of"],
        "generated_by": "tools/data/build_dataset.py",
        "inputs": {str(p.relative_to(DATA)): sha256(p) for p in inputs},
        "curated_entries_without_baseline": unknown,
        "pacts": src.alliances["pacts"][str(snapshot)],
        "relations": src.alliances["relations"][str(snapshot)],
        "countries": dict(sorted(countries.items())),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for snapshot in SNAPSHOTS:
        data = build(snapshot)
        path = OUT / f"{snapshot}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(f"{path.relative_to(ROOT)}: {len(data['countries'])} countries"
              + (f"; curated entries with no baseline row: {data['curated_entries_without_baseline']}"
                 if data["curated_entries_without_baseline"] else ""))


if __name__ == "__main__":
    main()
