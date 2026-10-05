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
    2021: {"as_of": "2021-01-01", "gfp": "gfp_2022.csv", "gfp_source": "gfp2022", "vdem_year": 2020},
    2026: {"as_of": "2026-01-01", "gfp": "gfp_2026.csv", "gfp_source": "gfp2026", "vdem_year": 2025},
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
# Regions for countries absent from GFP 2022 (the only source that tags continents).
EXTRA_REGIONS = {"BLZ": "North America", "BEN": "Africa", "ISL": "Europe", "LUX": "Europe", "SEN": "Africa"}

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
# Fields compared across editions; a >10x change usually means a definitional shift, so it is noted.
EDITION_COMPARED = (
    "active_personnel", "reserve_personnel", "tanks", "self_propelled_artillery", "towed_artillery",
    "rocket_artillery", "aircraft_total", "fighters", "attack_aircraft", "attack_helicopters",
    "submarines", "destroyers", "frigates", "corvettes",
)
EDITION_CHANGE_FACTOR = 10.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(raw: str) -> float | None:
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


def vdem_rows() -> tuple[dict[str, str], dict[tuple[str, int], dict[str, str]]]:
    names: dict[str, str] = {}
    by_key: dict[tuple[str, int], dict[str, str]] = {}
    with open(RAW / "vdem_v16_subset.csv") as f:
        for row in csv.DictReader(f):
            names[row["country_name"]] = row["country_text_id"]
            by_key[(row["country_text_id"], int(row["year"]))] = row
    return names, by_key


def resolve(name: str, vdem_names: dict[str, str]) -> str:
    if name in NAME_ALIASES:
        return NAME_ALIASES[name]
    if name in vdem_names:
        return vdem_names[name]
    raise SystemExit(f"Unmapped country name: {name!r}. Add it to NAME_ALIASES.")


def gfp_rows(snapshot: int) -> list[dict[str, str]]:
    with open(RAW / SNAPSHOTS[snapshot]["gfp"]) as f:
        return list(csv.DictReader(f))


def regions(vdem_names: dict[str, str]) -> dict[str, str]:
    out = dict(EXTRA_REGIONS)
    for row in gfp_rows(2021):
        out[resolve(row["country"], vdem_names)] = row["Continent"]
    return out


def sipri_2020(vdem_names: dict[str, str]) -> dict[str, tuple[float, float | None]]:
    out: dict[str, tuple[float, float | None]] = {}
    with open(RAW / "sipri_milex_2015_2020.csv") as f:
        for row in csv.DictReader(f):
            if row["Year"] != "2020" or not row["military_expenditure"]:
                continue
            name = row["Entity"]
            iso = NAME_ALIASES.get(name) or vdem_names.get(name)
            if iso:
                out[iso] = (float(row["military_expenditure"]), number(row["military_expenditure_share_gdp"]))
    return out


def willingness(iso: str, curated: dict[str, Any]) -> tuple[float, str, str]:
    """(pct, provenance id, confidence) with fallback: country -> regional aggregate -> global."""
    own = curated["countries"].get(iso)
    if own:
        return own["pct"], "gallup_eoy2023", own["confidence"]
    for region, agg in curated["regional_aggregates"].items():
        if iso in agg["members"]:
            return agg["pct"], f"gallup_eoy2023:{region}_aggregate", "low"
    return curated["global"]["pct"], "gallup_eoy2023:global_aggregate", "very_low"


def build(snapshot: int) -> dict[str, Any]:
    cfg = SNAPSHOTS[snapshot]
    vdem_names, vdem = vdem_rows()
    region_of = regions(vdem_names)
    wtf = load_json(CURATED / "willingness_to_fight.json")
    nuclear = load_json(CURATED / "nuclear.json")
    bmd = load_json(CURATED / "missile_defense.json")
    alliances = load_json(CURATED / "alliances.json")
    milex = sipri_2020(vdem_names) if snapshot == 2021 else {}
    other_gfp = {resolve(r["country"], vdem_names): r for r in gfp_rows(2026)} if snapshot == 2021 else {}
    gfp2022_by_iso = {resolve(r["country"], vdem_names): r for r in gfp_rows(2021)}

    field_map = GFP2026_FIELDS if snapshot == 2026 else GFP2022_FIELDS
    countries: dict[str, Any] = {}
    for row in gfp_rows(snapshot):
        iso = resolve(row["country"], vdem_names)
        values: dict[str, Any] = {}
        prov: dict[str, list[str]] = {}
        notes: list[str] = []
        confidence: dict[str, str] = {}

        def put(field: str, value: Any, source: str) -> None:
            values[field] = value
            prov.setdefault(source, []).append(field)

        for col, field in field_map.items():
            v = number(row[col])
            if field in ZERO_IS_MISSING and v == 0:
                v = None
            bound = PLAUSIBLE_MAX.get(field)
            if v is not None and bound is not None and v > bound:
                notes.append(f"rejected implausible {field}={v} from {cfg['gfp_source']} (bound {bound})")
                v = None
            put(field, v, cfg["gfp_source"])

        if snapshot == 2026:
            # GFP 2026 counts every military vehicle (Humvees, MRAPs, trucks) as "armored": US 409,660
            # versus 45,193 in the 2022 edition. Keep it under an honest name and estimate armoured
            # fighting vehicles from the 2022 count, scaled by the country's tank trend.
            put("military_vehicles", values.pop("armored_vehicles"), "gfp2026")
            prov["gfp2026"].remove("armored_vehicles")
            earlier = gfp2022_by_iso.get(iso)
            afv_2021 = number(earlier["Armored Vehicles"]) if earlier else None
            tanks_2021 = number(earlier["Tanks"]) if earlier else None
            tanks_2026 = values.get("tanks")
            if afv_2021 is None:
                put("armored_vehicles", None, "model")
            else:
                trend = 1.0
                if tanks_2021 and tanks_2026:
                    trend = min(4.0, max(0.25, tanks_2026 / tanks_2021))
                put("armored_vehicles", round(afv_2021 * trend), "model")
                notes.append("armored_vehicles estimated: 2022-edition AFV count x tank trend to 2026 "
                             "(GFP 2026 'armored vehicles' covers all military vehicles; kept as military_vehicles)")

        if snapshot == 2021:
            # Fighters: estimated from the 2026 fighter share of the air fleet (column corrupted upstream).
            later = other_gfp.get(iso)
            total21 = values.get("aircraft_total")
            if later and number(later["total_military_aircraft"]) and total21 is not None:
                share = (number(later["fighter_aircraft"]) or 0) / number(later["total_military_aircraft"])  # type: ignore[operator]
                put("fighters", min(total21, round(total21 * share)), "model")
                notes.append("fighters estimated: 2026 fighter share of total aircraft applied to the 2022-edition fleet")
            else:
                put("fighters", None, "model")
            put("naval_tonnage", None, "gfp2022")
            if iso in milex:
                spend, share_gdp = milex[iso]
                put("milex_sipri_usd_2019", round(spend), "sipri_milex")
                put("milex_share_gdp_pct", share_gdp, "sipri_milex")
            if iso == "AFG":
                notes.append("GFP 2022 already reflects the August 2021 collapse of the Afghan National Army (active personnel 0).")

        other_row, other_map = (other_gfp.get(iso), GFP2026_FIELDS) if snapshot == 2021 else (gfp2022_by_iso.get(iso), GFP2022_FIELDS)
        if other_row is not None:
            col_of = {f: c for c, f in other_map.items()}
            for f in EDITION_COMPARED:
                here, there = values.get(f), number(other_row[col_of[f]]) if f in col_of else None
                if here is None or there is None:
                    continue
                lo, hi = sorted((here, there))
                if (lo == 0 and hi >= 50) or (lo > 0 and hi / lo > EDITION_CHANGE_FACTOR):
                    this_ed, other_ed = ("2022", "2026") if snapshot == 2021 else ("2026", "2022")
                    notes.append(f"{f} changes sharply between GFP editions ({this_ed}: {here}, {other_ed}: {there}); "
                                 "often a reporting or definitional change, verify before relying on it")

        naval_parts = sum(values.get(k) or 0 for k in ("aircraft_carriers", "helicopter_carriers", "submarines", "destroyers",
                                                         "frigates", "corvettes", "patrol_vessels", "mine_warfare"))
        if values.get("naval_fleet_total") is not None and naval_parts > values["naval_fleet_total"]:
            notes.append(f"naval sub-types sum to {naval_parts}, above naval_fleet_total {values['naval_fleet_total']}")

        vrow = vdem.get((iso, cfg["vdem_year"]))
        for col, field in VDEM_FIELDS.items():
            put(field, number(vrow[col]) if vrow else None, "vdem_v16")
        if not vrow:
            notes.append("not covered by V-Dem: regime and stability fall back to engine defaults")

        pct, src, conf = willingness(iso, wtf)
        put("willingness_to_fight_pct", pct, src)
        confidence["willingness_to_fight_pct"] = conf

        entry: dict[str, Any] = {
            "name": row["country"] if row["country"] != "Beliz" else "Belize",
            "region": region_of.get(iso),
            "values": values,
            "provenance": prov,
            "confidence": confidence,
            "notes": notes,
        }
        if iso in nuclear[str(snapshot)]:
            n = nuclear[str(snapshot)][iso]
            entry["nuclear"] = {**{k: n[k] for k in ("stockpile", "deployed", "total_inventory")},
                                **nuclear["doctrines"][iso], "confidence": n["confidence"],
                                "source": nuclear[str(snapshot)]["_source"], "note": n.get("note")}
        deployments = bmd["deployments"][str(snapshot)].get(iso, [])
        if deployments:
            entry["missile_defense"] = [{**d, **{k: bmd["systems"][d["system"]][k] for k in
                                                  ("engages", "single_shot_pk", "shots_per_target", "interceptors_per_unit")}}
                                        for d in deployments]
        countries[iso] = entry

    unknown = sorted({iso for iso in list(nuclear[str(snapshot)]) + list(bmd["deployments"][str(snapshot)])
                      if not iso.startswith("_") and iso not in countries})
    inputs = sorted(p for p in list(RAW.glob("*.csv")) + list(CURATED.glob("*.json")))
    return {
        "snapshot": snapshot,
        "as_of": cfg["as_of"],
        "generated_by": "tools/data/build_dataset.py",
        "inputs": {str(p.relative_to(DATA)): sha256(p) for p in inputs},
        "curated_entries_without_baseline": unknown,
        "pacts": alliances["pacts"][str(snapshot)],
        "relations": alliances["relations"][str(snapshot)],
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
