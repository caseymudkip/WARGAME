"""WARGAME command line.

    wargame                      open the spectator app in your browser
    wargame serve --port 8765    the same, without opening a browser (--open to open one)
    wargame presets              list the ready-made flashpoints
    wargame run taiwan --days 365    run a flashpoint headless and print how it went
"""

from __future__ import annotations

import argparse
import sys
import time

from wargame import __version__, scenarios


def report(key: str, days: int, seed: int = 0) -> str:
    fp = scenarios.PRESETS[key]
    sim = scenarios.from_preset(fp, seed=seed)
    world = sim.world
    before = {p.id: p.controller for p in world.provinces.values()}
    t0 = time.perf_counter()
    for _ in range(max(1, days // 10)):
        sim.run_days(10)
        if sim.finished:
            break
    war = sim.wars[0]
    changed: dict[str, float] = {}
    for p in world.provinces.values():
        if p.controller != before[p.id]:
            key_ = f"{before[p.id]} -> {p.controller}"
            changed[key_] = changed.get(key_, 0.0) + p.area_km2
    ending = war.treaty.reason if war.treaty else "still fighting"
    terms = ", ".join(sorted({t.type.value for t in war.treaty.terms})) if war.treaty else ""
    lines = [f"{fp.name}: {ending} after {sim.clock.day} days ({time.perf_counter() - t0:.1f}s)"
             + (f"; terms: {terms}" if terms else "")]
    lines.append("  ground changed: " + (", ".join(f"{k} {v:,.0f} km2" for k, v in sorted(changed.items(), key=lambda kv: -kv[1])[:5])
                                         or "none"))
    lines.append("  casualties: " + ", ".join(f"{t} {world.country(t).oob.casualties_total:,}"
                                              for t in sorted(war.participants) if t in world.countries))
    for e in war.events:
        if e.kind not in ("lend_lease", "world_reaction", "pact_triggered"):
            lines.append(f"  day {e.hour // 24}: {e.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wargame", description=f"WARGAME {__version__}: an autonomous modern war simulator.")
    sub = ap.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the spectator app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="open a browser")
    sub.add_parser("presets", help="list the ready-made flashpoints")
    run = sub.add_parser("run", help="run a flashpoint without the viewer")
    run.add_argument("preset", choices=sorted(scenarios.PRESETS))
    run.add_argument("--days", type=int, default=365)
    run.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    if args.command == "presets":
        for fp in scenarios.FLASHPOINTS:
            print(f"{fp.key:14s} {fp.name}  ({fp.blurb})")
        return 0
    if args.command == "run":
        print(report(args.preset, args.days, args.seed))
        return 0
    from wargame.app.server import serve as run_server

    if args.command == "serve":
        run_server(args.host, args.port, open_browser=args.open)
    else:
        run_server(open_browser=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
