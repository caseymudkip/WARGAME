"""Integrity of the real-world dataset and of its conversion into engine objects."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from wargame.core.enums import Branch, MissileClass, NuclearDoctrine, RegimeType
from wargame.data import apply_diplomacy, build_country, load_snapshot
from wargame.data.profile import equipment_quality, regime_type, stability

ROOT = Path(__file__).resolve().parents[1]
YEARS = (2021, 2026)
NUCLEAR_STATES = {"USA", "RUS", "CHN", "GBR", "FRA", "IND", "PAK", "ISR", "PRK"}


def _builder():
    spec = importlib.util.spec_from_file_location("build_dataset", ROOT / "tools" / "data" / "build_dataset.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", params=YEARS)
def snapshot(request):
    return load_snapshot(request.param)


@pytest.fixture(scope="module")
def snapshots():
    return {y: load_snapshot(y) for y in YEARS}


# --- reproducibility and provenance ---------------------------------------------------------


@pytest.mark.parametrize("year", YEARS)
def test_committed_snapshot_matches_its_inputs(year):
    """Rebuilding from data/raw + data/curated must reproduce the committed file exactly."""
    committed = json.loads((ROOT / "data" / "snapshots" / f"{year}.json").read_text())
    assert _builder().build(year) == committed


def test_every_value_has_exactly_one_source(snapshot):
    for iso, rec in snapshot.countries.items():
        attributed = [f for fields in rec.provenance.values() for f in fields]
        assert sorted(attributed) == sorted(rec.values), iso
        assert len(attributed) == len(set(attributed)), iso


def test_every_source_is_registered(snapshot):
    for rec in snapshot.countries.values():
        for source in rec.provenance:
            assert source.split(":")[0] in snapshot.sources, source
        if rec.nuclear:
            assert rec.nuclear["source"] in snapshot.sources


def test_coverage(snapshots):
    assert len(snapshots[2021].countries) == 140
    assert len(snapshots[2026].countries) == 145
    for snap in snapshots.values():
        assert {"USA", "RUS", "CHN", "UKR", "IND", "PAK", "ISR", "IRN", "PRK", "KOR", "JPN", "TWN", "POL", "DEU"} <= set(snap.countries)


# --- data quality guards ------------------------------------------------------------------


def test_no_value_is_negative(snapshot):
    for iso, rec in snapshot.countries.items():
        for field, v in rec.values.items():
            if v is not None and field not in ("political_violence", "fiscal_capacity"):
                assert v >= 0, (iso, field, v)


def test_corrupted_cells_are_rejected_not_kept():
    lka = load_snapshot(2021)["LKA"]
    assert lka.values["aircraft_carriers"] is None
    assert any("rejected implausible aircraft_carriers=2022" in n for n in lka.notes)


def test_corrupted_fighter_column_is_replaced_by_a_labelled_estimate():
    snap = load_snapshot(2021)
    for rec in snap.countries.values():
        assert rec.source_of("fighters") == "model"
        if rec.values["fighters"] is not None and rec.values["aircraft_total"] is not None:
            assert rec.values["fighters"] <= rec.values["aircraft_total"]


def test_2026_armour_is_not_the_inflated_all_vehicle_count():
    usa = load_snapshot(2026)["USA"]
    assert usa.values["military_vehicles"] > 300_000
    assert usa.values["armored_vehicles"] < 60_000
    assert usa.source_of("armored_vehicles") == "model"


def test_largest_forces_are_where_they_should_be(snapshots):
    for snap in snapshots.values():
        top = lambda field: max(snap.countries.values(), key=lambda r: r.get(field)).iso3  # noqa: E731
        assert top("aircraft_carriers") == "USA"
        assert top("fighters") == "USA"
        assert top("active_personnel") == "CHN"


def test_known_wartime_shifts_show_up_between_snapshots(snapshots):
    early, late = snapshots[2021], snapshots[2026]
    assert late["UKR"].get("active_personnel") > 3 * early["UKR"].get("active_personnel")
    assert late["RUS"].get("tanks") < 0.6 * early["RUS"].get("tanks")
    assert late["POL"].get("active_personnel") > early["POL"].get("active_personnel")


# --- authoritative cross-checks ----------------------------------------------------------------


def test_nuclear_states(snapshot):
    assert {iso for iso, r in snapshot.countries.items() if r.nuclear} == NUCLEAR_STATES


def test_sipri_2026_stockpiles_sum_to_the_published_total():
    snap = load_snapshot(2026)
    assert sum(r.nuclear["stockpile"] for r in snap.countries.values() if r.nuclear) == 9_745


def test_sipri_2021_inventories_sum_to_the_published_total():
    """SIPRI's 13,080 for January 2021 excludes the bracketed North Korean estimate."""
    snap = load_snapshot(2021)
    total = sum(r.nuclear["total_inventory"] for iso, r in snap.countries.items() if r.nuclear and iso != "PRK")
    assert abs(total - 13_080) <= 1


def test_gfp_budgets_broadly_agree_with_sipri():
    """GFP 2022 budgets vs SIPRI 2020 spending: most within 2x. Big gaps are known PPP-style estimates."""
    snap = load_snapshot(2021)
    pairs = [(r.iso3, r.get("defense_budget_usd") / r.get("milex_sipri_usd_2019"))
             for r in snap.countries.values()
             if r.get("defense_budget_usd") > 0 and r.get("milex_sipri_usd_2019") > 0]
    within = [iso for iso, ratio in pairs if 0.5 <= ratio <= 2.0]
    assert len(pairs) > 100
    assert len(within) / len(pairs) > 0.75


def test_nato_enlargement():
    early, late = (load_snapshot(y).pacts["NATO"]["members"] for y in YEARS)
    assert len(early) == 30 and len(late) == 32
    assert set(late) - set(early) == {"FIN", "SWE"}


# --- conversion into engine objects --------------------------------------------------------------


def test_every_country_builds(snapshot):
    countries = {iso: build_country(r, capital_province_id=0) for iso, r in snapshot.countries.items()}
    apply_diplomacy(snapshot, countries)
    for c in countries.values():
        assert 0.0 <= c.spirit.patriotism <= 1.0
        assert 0.0 <= c.spirit.stability <= 1.0
        assert c.military_power() >= 0
        assert c.logistics.supply_ratio == pytest.approx(1.0)


def test_regime_classification():
    early, late = load_snapshot(2021), load_snapshot(2026)
    assert regime_type(early["USA"]) is RegimeType.LIBERAL_DEMOCRACY
    assert regime_type(late["PRK"]) is RegimeType.TOTALITARIAN
    assert regime_type(late["RUS"]) is RegimeType.AUTHORITARIAN
    assert regime_type(late["TWN"]) is RegimeType.LIBERAL_DEMOCRACY


def test_derived_stability_and_quality_order_sensibly():
    snap = load_snapshot(2026)
    assert stability(snap["TWN"]) > stability(snap["MMR"])
    assert equipment_quality(snap["USA"]) > equipment_quality(snap["IRN"]) > equipment_quality(snap["PRK"])


def test_military_power_ranking_is_credible(snapshots):
    for snap in snapshots.values():
        countries = {iso: build_country(r, 0) for iso, r in snap.countries.items()}
        top3 = {c.tag for c in sorted(countries.values(), key=lambda c: -c.military_power())[:3]}
        assert top3 == {"USA", "RUS", "CHN"}


def test_nuclear_postures_load():
    snap = load_snapshot(2026)
    rus = build_country(snap["RUS"], 0)
    chn = build_country(snap["CHN"], 0)
    assert rus.nuclear.warheads == 4_400 and rus.nuclear.second_strike_capable
    assert MissileClass.HYPERSONIC_GLIDE in rus.nuclear.delivery
    assert chn.nuclear.doctrine is NuclearDoctrine.NO_FIRST_USE
    assert build_country(snap["JPN"], 0).nuclear.warheads == 0


def test_missile_defense_loads():
    snap = load_snapshot(2026)
    isr = build_country(snap["ISR"], 0)
    dome = next(s for s in isr.nuclear.missile_defenses if s.name == "Iron Dome")
    assert dome.engages == frozenset({MissileClass.TACTICAL})
    assert isr.nuclear.intercept_probability(MissileClass.THEATER, 0) > 0.9  # Arrow 3 + Arrow 2 layers.
    assert isr.nuclear.intercept_probability(MissileClass.HYPERSONIC_GLIDE, 0) == 0.0
    deu = build_country(snap["DEU"], 0)
    assert any(s.name == "Arrow 3" for s in deu.nuclear.missile_defenses)
    assert not any(s.name == "Arrow 3" for s in build_country(load_snapshot(2021)["DEU"], 0).nuclear.missile_defenses)


def test_diplomacy_applies_pacts_and_rivalries():
    snap = load_snapshot(2026)
    countries = {iso: build_country(r, 0) for iso, r in snap.countries.items()}
    apply_diplomacy(snap, countries)
    assert "USA" in countries["POL"].defensive_pacts
    assert "PAK" in countries["SAU"].defensive_pacts           # September 2025 agreement.
    assert "RUS" in countries["PRK"].defensive_pacts           # 2024 treaty.
    assert countries["IND"].relations["PAK"] < -0.5
    assert countries["UKR"].relations["GBR"] > 0.5


def test_land_power_dominates_for_armies_and_naval_power_for_navies():
    snap = load_snapshot(2026)
    usa, prk = build_country(snap["USA"], 0), build_country(snap["PRK"], 0)
    assert usa.oob.branch_power(Branch.NAVAL) > 10 * prk.oob.branch_power(Branch.NAVAL)


# --- GFP 2025 edition: reconciliation of the 2026 snapshot -----------------------------------


def test_every_country_has_a_region(snapshot):
    assert all(rec.region for rec in snapshot.countries.values())


def test_budget_that_contradicts_sipri_falls_back_to_the_2025_edition():
    ago = load_snapshot(2026)["AGO"]
    assert ago.values["defense_budget_usd"] == 1_101_360_000  # 2026 edition says 31.2bn
    assert ago.source_of("defense_budget_usd") == "gfp2025"


def test_cited_override_beats_automatic_rules():
    ltu = load_snapshot(2026)["LTU"]
    assert ltu.source_of("defense_budget_usd") == "override"
    assert ltu.values["defense_budget_usd"] == 5_651_760_000
    assert any("kam.lt" in n for n in ltu.notes)


def test_estimated_and_redefined_fields_are_never_gap_filled():
    for rec in load_snapshot(2026).countries.values():
        assert rec.source_of("armored_vehicles") in ("model",)
        assert rec.source_of("military_vehicles") == "gfp2026"


def test_sharp_one_year_changes_are_flagged():
    mmr = load_snapshot(2026)["MMR"]  # MLRS 180 (2025) -> 1,520 (2026)
    assert any(n.startswith("rocket_artillery changes sharply") for n in mmr.notes)
