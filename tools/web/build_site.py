"""Build the browser edition of WARGAME: the flashpoints, recorded day by day, and a viewer that replays them.

    python tools/web/build_site.py OUT_DIR [--days 730] [--only taiwan,korea] [--pyodide PYODIDE_DIR]

The flashpoints are fought here, in Python, exactly as in the spectator app; the browser plays the record back.
A spectator can't change a war once it starts, so a replay of the same scenario and seed is the same war.

With --pyodide the site also fights custom wars: the engine itself runs in the browser under Pyodide (Python
compiled to WebAssembly; wargame/app/live.py) and streams day frames in the same format. Artifact hosts may
serve no archives, so Python's standard library and the engine ship as JSON bundles of text files.

OUT_DIR receives index.html (the viewer, from tools/web/viewer.html) and data/: meta.json, world-2021.json,
world-2026.json, geometry.json and replays/<flashpoint>.json; with --pyodide also pyodide/ (the runtime, with
python_stdlib.json for its zip), engine.json (the engine and its data), engine-boot.js and engine-worker.js.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from wargame import __version__, replay, scenarios
from wargame.data.snapshot import DEFAULT_ROOT
from wargame.data.world_map import build_real_world

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "pyodide-lock.json"]
ENGINE_FILES = ["src/wargame/**/*.py", "data/sources.json", "data/snapshots/*.json", "data/map/world_map.json",
                "data/curated/*.json"]


def engine_bundle() -> dict[str, str]:
    """The engine and the data it reads at run time, laid out as in the repository (DEFAULT_ROOT resolves)."""
    return {f.relative_to(ROOT).as_posix(): f.read_text(encoding="utf-8")
            for pattern in ENGINE_FILES for f in sorted(ROOT.glob(pattern))}


def stdlib_bundle(stdlib_zip: Path) -> dict[str, str]:
    """Pyodide's python_stdlib.zip as text files (it holds Python source, a few READMEs and markers)."""
    with zipfile.ZipFile(stdlib_zip) as z:
        return {i.filename: z.read(i).decode("utf-8") for i in z.infolist() if not i.is_dir()}


def world_file(year: int) -> dict[str, Any]:
    world = build_real_world(year).world
    return {
        "year": year,
        "countries": {tag: c.name for tag, c in sorted(world.countries.items())},
        # name, owner, controller, lat, lon, area km2, terrain, population
        "provinces": [[p.name, p.owner, p.controller, p.lat, p.lon, round(p.area_km2), p.terrain.value, p.population]
                      for p in sorted(world.provinces.values(), key=lambda p: p.id)],
    }


def record_preset(fp: scenarios.Flashpoint, max_days: int) -> dict[str, Any]:
    return replay.record(scenarios.from_preset(fp), fp.key, fp.year, max_days, fp.blurb, fp.nuclear)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--only", default="")
    ap.add_argument("--pyodide", type=Path, help="an extracted pyodide-core release (github.com/pyodide/pyodide, 0.27.7): "
                    "enables custom wars, run in the browser by the engine under WebAssembly")
    args = ap.parse_args()
    out: Path = args.out
    (out / "data" / "replays").mkdir(parents=True, exist_ok=True)
    keys = [k for k in args.only.split(",") if k] or [fp.key for fp in scenarios.FLASHPOINTS]

    compact = {"separators": (",", ":"), "ensure_ascii": False}
    for year in sorted(scenarios.START_DATES):
        (out / "data" / f"world-{year}.json").write_text(json.dumps(world_file(year), **compact))
    shutil.copyfile(DEFAULT_ROOT / "map" / "geometry.json", out / "data" / "geometry.json")
    presets = []
    for fp in scenarios.FLASHPOINTS:
        if fp.key not in keys:
            continue
        t0 = time.perf_counter()
        replay_ = record_preset(fp, args.days)
        path = out / "data" / "replays" / f"{fp.key}.json"
        path.write_text(json.dumps(replay_, **compact))
        last = replay_["frames"][-1]["d"]
        ending = replay_["treaty"]["reason"] if replay_["treaty"] else f"still fighting on day {last}"
        print(f"{fp.key:14s} {len(replay_['frames']):4d} days, {path.stat().st_size / 1e6:4.1f} MB, "
              f"{time.perf_counter() - t0:5.1f}s: {ending}", flush=True)
        presets.append({"key": fp.key, "name": fp.name, "year": fp.year, "attacker": fp.attacker, "defender": fp.defender,
                        "blurb": fp.blurb, "days": last, "ending": ending})
    engine = args.pyodide is not None
    if engine:
        (out / "pyodide").mkdir(exist_ok=True)
        for name in PYODIDE_FILES:
            shutil.copyfile(args.pyodide / name, out / "pyodide" / name)
        (out / "pyodide" / "python_stdlib.json").write_text(json.dumps(stdlib_bundle(args.pyodide / "python_stdlib.zip"), **compact),
                                                               encoding="utf-8")
        (out / "pyodide" / "python_stdlib.txt").write_text(
            "Python's standard library for this page is python_stdlib.json: see engine-boot.js.\n")
        (out / "engine.json").write_text(json.dumps(engine_bundle(), **compact), encoding="utf-8")
        for name in ("engine-boot.js", "engine-worker.js"):
            shutil.copyfile(HERE / name, out / name)
    (out / "data" / "meta.json").write_text(json.dumps({"version": __version__, "presets": presets, "engine": engine}, **compact))
    shutil.copyfile(HERE / "viewer.html", out / "index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
