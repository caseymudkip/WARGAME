"""From real-world data to engine objects.

Every number the engine needs but no source publishes is derived here, by a
formula small enough to read and argue with. Sourced inputs are used as-is;
the derivations are:

  regime      V-Dem Regimes of the World, split by liberal-democracy and
              civil-liberties indices (electoral autocracies with libdem
              < 0.15 count as authoritarian; closed autocracies with civil
              liberties < 0.10 as totalitarian).
  stability   0.40 x share of territory the state controls
            + 0.35 x absence of political violence
            + 0.25 x state fiscal capacity            (all V-Dem)
  patriotism  share willing to fight for their country (Gallup International).
  quality     equipment quality from defence spending per active soldier, on
              a log scale: about 0.26 at $3.6k/soldier (North Korea) up to
              about 0.71 at $620k (United States). A coarse proxy until
              per-system quality data exists.
  logistics   abstract supply units scaled by force size. Fuel production
              comes from oil self-sufficiency, munitions and spares from the
              size of the economy (PPP). Stocks: 60 days of fuel, 30 of
              everything else.
"""

from __future__ import annotations

import math

from wargame.core.enums import Branch, MissileClass, NuclearDoctrine, RegimeType, SupplyType
from wargame.core.mathutil import clamp
from wargame.data.snapshot import CountryRecord, Snapshot
from wargame.nation.country import Country
from wargame.nation.logistics import LogisticsStockpile
from wargame.nation.military import EquipmentStock, OrderOfBattle
from wargame.nation.national_spirit import NationalSpirit
from wargame.nation.nuclear import MissileDefenseSystem, NuclearPosture

# --- politics --------------------------------------------------------------------

DEFAULT_STABILITY = 0.5
ELECTORAL_AUTOCRACY_LIBDEM_SPLIT = 0.15
TOTALITARIAN_CIVLIB = 0.10


def regime_type(r: CountryRecord) -> RegimeType:
    row = r.values.get("regime_row")
    if row is None:
        return RegimeType.HYBRID
    if row >= 3:
        return RegimeType.LIBERAL_DEMOCRACY
    if row >= 2:
        return RegimeType.FLAWED_DEMOCRACY
    if row >= 1:
        return RegimeType.HYBRID if r.get("liberal_democracy_index") >= ELECTORAL_AUTOCRACY_LIBDEM_SPLIT else RegimeType.AUTHORITARIAN
    return RegimeType.TOTALITARIAN if r.get("civil_liberties_index", 1.0) < TOTALITARIAN_CIVLIB else RegimeType.AUTHORITARIAN


def stability(r: CountryRecord) -> float:
    terr, viol, fisc = (r.values.get(k) for k in ("territorial_control_pct", "political_violence", "fiscal_capacity"))
    if terr is None or viol is None or fisc is None:
        return DEFAULT_STABILITY
    calm = 1.0 - clamp((viol + 3.0) / 7.0)  # V-Dem political violence runs roughly -3 (none) to +4 (pervasive).
    capacity = clamp((fisc + 3.0) / 6.0)
    return clamp(0.40 * terr / 100.0 + 0.35 * calm + 0.25 * capacity)


def patriotism(r: CountryRecord) -> float:
    return clamp(r.get("willingness_to_fight_pct", 50.0) / 100.0)


def baseline_war_support(patriotism_value: float, stability_value: float) -> float:
    """Pre-war appetite for fighting; scenarios may override it."""
    return clamp(0.25 + 0.5 * patriotism_value + 0.25 * stability_value)


def national_spirit(r: CountryRecord) -> NationalSpirit:
    p, s = patriotism(r), stability(r)
    return NationalSpirit(patriotism=p, stability=s, war_support=baseline_war_support(p, s), regime=regime_type(r))


# --- military ---------------------------------------------------------------------

DEFAULT_QUALITY = 0.40
DEFAULT_READINESS = 0.70  # GFP inventories include stored and unserviceable equipment.
SORTIES_PER_COMBAT_AIRCRAFT = 1.5
MOBILISABLE_SHARE_OF_FIT = 0.05
PARAMILITARY_MOBILISATION = 0.25  # Paramilitaries range from gendarmeries to village-defence volunteers.

# field -> (branch, combat weight relative to one main battle tank). Weight 0 = enabler, not a combatant.
EQUIPMENT: dict[str, tuple[Branch, float]] = {
    "tanks": (Branch.LAND, 1.0),
    "armored_vehicles": (Branch.LAND, 0.25),
    "self_propelled_artillery": (Branch.LAND, 0.8),
    "towed_artillery": (Branch.LAND, 0.4),
    "rocket_artillery": (Branch.LAND, 1.0),
    "fighters": (Branch.AIR, 6.0),
    "attack_aircraft": (Branch.AIR, 5.0),
    "attack_helicopters": (Branch.AIR, 2.5),
    "transport_aircraft": (Branch.AIR, 0.0),
    "tanker_aircraft": (Branch.AIR, 0.0),
    "special_mission_aircraft": (Branch.AIR, 0.0),
    "trainer_aircraft": (Branch.AIR, 0.0),
    "aircraft_carriers": (Branch.NAVAL, 300.0),
    "helicopter_carriers": (Branch.NAVAL, 80.0),
    "submarines": (Branch.NAVAL, 40.0),
    "destroyers": (Branch.NAVAL, 50.0),
    "frigates": (Branch.NAVAL, 25.0),
    "corvettes": (Branch.NAVAL, 8.0),
    "patrol_vessels": (Branch.NAVAL, 1.0),
    "mine_warfare": (Branch.NAVAL, 2.0),
}


def equipment_quality(r: CountryRecord) -> float:
    budget, active = r.get("defense_budget_usd"), r.get("active_personnel")
    if budget <= 0 or active <= 0:
        return DEFAULT_QUALITY
    per_soldier = max(budget / active, 1_000.0)
    return clamp(0.15 + 0.2 * math.log10(per_soldier / 1_000.0), 0.15, 0.95)


def order_of_battle(r: CountryRecord, airfields: int = 1) -> OrderOfBattle:
    q = equipment_quality(r)
    equipment = {
        name: EquipmentStock(name, branch, int(r.get(name)), q, DEFAULT_READINESS, combat_weight=weight)
        for name, (branch, weight) in EQUIPMENT.items()
    }
    active, reserve, para = (int(r.get(k)) for k in ("active_personnel", "reserve_personnel", "paramilitary_personnel"))
    combat_aircraft = r.get("fighters") + r.get("attack_aircraft")
    return OrderOfBattle(
        equipment=equipment,
        active_personnel=active,
        reserve_personnel=reserve,
        mobilizable_manpower=active + reserve + int(PARAMILITARY_MOBILISATION * para)
        + int(MOBILISABLE_SHARE_OF_FIT * r.get("fit_for_service")),
        sorties_per_airfield=SORTIES_PER_COMBAT_AIRCRAFT * combat_aircraft / max(1, airfields),
    )


# --- logistics ----------------------------------------------------------------------

STOCK_DAYS = {SupplyType.FUEL: 60.0, SupplyType.AMMUNITION: 30.0, SupplyType.RATIONS: 30.0, SupplyType.SPARE_PARTS: 30.0}
DEMAND_PER_UNIT = {SupplyType.FUEL: 1.0, SupplyType.AMMUNITION: 1.0, SupplyType.RATIONS: 0.5, SupplyType.SPARE_PARTS: 0.2}
ESSENTIAL_OIL_SHARE = 0.3  # In total war, military plus essential civilian demand ~30% of peacetime use.
PEACETIME_TRADE_MARGIN = 0.1


def force_units(r: CountryRecord) -> float:
    """Size of the force that must be supplied, in abstract units (1 = 1,000 troops)."""
    ground = sum(r.get(k) for k in ("tanks", "armored_vehicles", "self_propelled_artillery", "towed_artillery", "rocket_artillery"))
    air = sum(r.get(k) for k in ("fighters", "attack_aircraft", "attack_helicopters"))
    sea = 10 * r.get("aircraft_carriers") + sum(r.get(k) for k in ("destroyers", "frigates", "submarines")) + 0.3 * r.get("corvettes")
    return r.get("active_personnel") / 1_000.0 + 0.05 * ground + 0.2 * air + sea


def production_ratios(r: CountryRecord) -> dict[SupplyType, float]:
    oil_prod, oil_cons = r.get("oil_production_bpd"), r.get("oil_consumption_bpd")
    oil_ratio = oil_prod / oil_cons if oil_cons > 0 else (1.0 if oil_prod > 0 else 0.0)
    economy = clamp(math.log10(max(r.get("ppp_usd"), 1e11) / 1e11) / 2.0)  # $100bn -> 0, $10tn -> 1
    munitions = 0.3 + 0.7 * economy
    return {
        SupplyType.FUEL: clamp(oil_ratio / ESSENTIAL_OIL_SHARE, 0.1, 1.5),
        SupplyType.AMMUNITION: munitions,
        SupplyType.RATIONS: 1.0,
        SupplyType.SPARE_PARTS: munitions,
    }


def logistics(r: CountryRecord) -> LogisticsStockpile:
    units = force_units(r)
    demand = {s: DEMAND_PER_UNIT[s] * units for s in SupplyType}
    ratios = production_ratios(r)
    return LogisticsStockpile(
        stocks={s: STOCK_DAYS[s] * demand[s] for s in SupplyType},
        base_daily_consumption=demand,
        base_daily_production={s: ratios[s] * demand[s] for s in SupplyType},
        base_daily_imports={s: (max(0.0, 1.0 - ratios[s]) + PEACETIME_TRADE_MARGIN) * demand[s] for s in SupplyType},
    )


def seaborne_import_share(r: CountryRecord) -> float:
    if r.get("coastline_km") <= 0:
        return 0.0   # Landlocked.
    if r.get("border_km") <= 0:
        return 0.95  # Island: almost everything arrives by sea.
    return 0.6


# --- nuclear and missile defence -------------------------------------------------------

def missile_defenses(r: CountryRecord) -> list[MissileDefenseSystem]:
    return [
        MissileDefenseSystem(
            name=d["system"],
            engages=frozenset(MissileClass(m) for m in d["engages"]),
            single_shot_pk=d["single_shot_pk"],
            interceptors=int(d["units"] * d["interceptors_per_unit"]),
            shots_per_target=d["shots_per_target"],
        )
        for d in r.missile_defense
    ]


def nuclear_posture(r: CountryRecord) -> NuclearPosture:
    defenses = missile_defenses(r)
    n = r.nuclear
    if not n:
        return NuclearPosture(missile_defenses=defenses)
    return NuclearPosture(
        warheads=int(n["stockpile"]),
        delivery=frozenset(MissileClass(m) for m in n["delivery"]),
        doctrine=NuclearDoctrine(n["doctrine"]),
        second_strike_capable=bool(n["second_strike"]),
        missile_defenses=defenses,
    )


# --- assembly ---------------------------------------------------------------------------

def build_country(r: CountryRecord, capital_province_id: int, airfields: int = 1) -> Country:
    """A Country with no diplomacy yet; call apply_diplomacy once all countries exist."""
    return Country(
        tag=r.iso3,
        name=r.name,
        capital_province_id=capital_province_id,
        spirit=national_spirit(r),
        oob=order_of_battle(r, airfields),
        logistics=logistics(r),
        nuclear=nuclear_posture(r),
        seaborne_import_share=seaborne_import_share(r),
    )


PACT_RELATION = 0.8


def apply_diplomacy(snapshot: Snapshot, countries: dict[str, Country]) -> None:
    """Defensive pacts and geopolitical leanings from the snapshot, applied symmetrically."""
    for iso, country in countries.items():
        partners = snapshot.pact_partners(iso) & countries.keys()
        country.defensive_pacts |= partners
        for p in partners:
            country.relations.setdefault(p, PACT_RELATION)
    for a, b, value in snapshot.relations:
        if a in countries and b in countries:
            countries[a].relations[b] = value
            countries[b].relations[a] = value
