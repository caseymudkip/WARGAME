"""Re-extract the vendored raw inputs in data/raw/ from their upstream sources.

Each upstream is a public GitHub repository pinned to a commit (see
data/sources.json). Clone them, then run:

    python tools/data/extract_upstream.py \
        --gfp2026 <Unified-Military-Analytics clone> \
        --gfp2022 <Global-Firepower clone> \
        --vdem <vdemdata clone> \
        --owid <owid-datasets clone>

Needs pandas, pyreadr and openpyxl (dev-only; the engine itself is stdlib-only).
The outputs are byte-for-byte deterministic for the pinned commits.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"

VDEM_COLUMNS = [
    "country_name", "country_text_id", "year",
    "v2x_regime",     # Regimes of the World: 0 closed autocracy .. 3 liberal democracy
    "v2x_libdem",     # Liberal democracy index 0..1
    "v2x_civlib",     # Civil liberties index 0..1
    "v2caviol",       # Political violence by non-state actors (higher = more), ~ -3..4
    "v2svstterr",     # % of territory the state effectively controls
    "v2stfisccap",    # State fiscal capacity, ~ -3..3
]
VDEM_YEARS = (2020, 2021, 2024, 2025)
SIPRI_YEARS = range(2015, 2021)


def extract_gfp2026(repo: Path) -> None:
    shutil.copyfile(repo / "data" / "military_cleaned.csv", RAW / "gfp_2026.csv")


def extract_gfp2022(repo: Path) -> None:
    import openpyxl

    wb = openpyxl.load_workbook(repo / "GLOBAL FIREPOWER 2022 X D'CHALLENGER.xlsx", read_only=True, data_only=True)
    rows = [r for r in wb.worksheets[0].iter_rows(values_only=True) if r and len(r) > 3 and r[0]]
    with open(RAW / "gfp_2022.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        for r in rows:
            w.writerow(["" if v is None else (int(v) if isinstance(v, float) and v.is_integer() else v) for v in r])


def extract_vdem(repo: Path) -> None:
    import pyreadr

    df = next(iter(pyreadr.read_r(str(repo / "data" / "vdem.RData")).values()))
    sub = df[df["year"].isin(VDEM_YEARS)][VDEM_COLUMNS].copy()
    sub["year"] = sub["year"].astype(int)
    sub = sub.sort_values(["country_text_id", "year"])
    sub.to_csv(RAW / "vdem_v16_subset.csv", index=False, float_format="%.4f", lineterminator="\n")


def extract_sipri(repo: Path) -> None:
    src = repo / "datasets" / "SIPRI Military Expenditure Database" / "SIPRI Military Expenditure Database.csv"
    with open(src) as f, open(RAW / "sipri_milex_2015_2020.csv", "w", newline="") as out:
        r = csv.DictReader(f)
        w = csv.DictWriter(out, fieldnames=r.fieldnames or [], lineterminator="\n")
        w.writeheader()
        for row in r:
            if row["Year"].isdigit() and int(row["Year"]) in SIPRI_YEARS:
                w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gfp2026", type=Path, required=True)
    ap.add_argument("--gfp2022", type=Path, required=True)
    ap.add_argument("--vdem", type=Path, required=True)
    ap.add_argument("--owid", type=Path, required=True)
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    extract_gfp2026(args.gfp2026)
    extract_gfp2022(args.gfp2022)
    extract_vdem(args.vdem)
    extract_sipri(args.owid)


if __name__ == "__main__":
    main()
