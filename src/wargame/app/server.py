"""Local web server for the spectator app (standard library only).

    GET  /                      the viewer
    GET  /api/meta              presets, goals, tiers, speeds, motivations
    GET  /api/world?year=2026   countries and provinces at a start date (for the setup screen)
    GET  /api/geometry          province outlines (data/map/geometry.json)
    POST /api/start             {preset} or {year, attacker, defender, goal, tier, nuclear, ...}
    POST /api/speed             {speed: "PAUSED" | "DAY_BY_DAY" | ...}
    GET  /api/state?since=N&map=V   the war since event N (province control only if it changed since V)
    GET  /api/province/<id>     one province in detail
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from wargame import __version__, scenarios
from wargame.app.session import SPEEDS, Session, Setup, countries
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.data.snapshot import DEFAULT_ROOT
from wargame.data.world_map import build_real_world

STATIC = Path(__file__).resolve().parent / "static"
GEOMETRY = DEFAULT_ROOT / "map" / "geometry.json"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}

GOAL_LABELS = {
    WarGoalType.BORDER_SKIRMISH: "Border skirmish (a few border provinces)",
    WarGoalType.TERRITORIAL_CONQUEST: "Territorial conquest (claimed provinces)",
    WarGoalType.REGIME_CHANGE: "Regime change (take the capital, install a puppet)",
    WarGoalType.TOTAL_CAPITULATION: "Total capitulation (annexation)",
    WarGoalType.COERCION: "Coercion (strikes and blockade until concessions)",
}
TIER_LABELS = {
    EscalationTier.VACUUM: "Vacuum: nobody else gets involved",
    EscalationTier.PROXY_WAR: "Proxy war: outsiders send arms, not troops",
    EscalationTier.UNRESTRICTED: "Unrestricted: alliances trigger",
}
SPEED_LABELS = {"PAUSED": "Pause", "HOUR_BY_HOUR": "1 hour/s", "SIX_HOURS": "6 hours/s", "DAY_BY_DAY": "1 day/s",
                "WEEK_BY_WEEK": "1 week/s", "MONTH_BY_MONTH": "1 month/s"}


class NotFound(Exception):
    pass


class App:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.session: Session | None = None
        self._world_cache: dict[int, dict[str, Any]] = {}
        self._geometry: bytes | None = None

    def meta(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "presets": [{"key": fp.key, "name": fp.name, "year": fp.year, "attacker": fp.attacker, "defender": fp.defender,
                         "blurb": fp.blurb} for fp in scenarios.FLASHPOINTS],
            "years": sorted(scenarios.START_DATES),
            "goals": [{"value": g.value, "label": label} for g, label in GOAL_LABELS.items()],
            "tiers": [{"value": t.value, "label": label} for t, label in TIER_LABELS.items()],
            "motivations": [{"value": Motivation.CAUTIOUS.name, "label": "Realistic (cautious)"},
                            {"value": Motivation.AGGRESSIVE.name, "label": "Epic (aggressive)"}],
            "speeds": [{"value": name, "label": SPEED_LABELS.get(name, name)} for name in SPEEDS],
        }

    def world(self, year: int) -> dict[str, Any]:
        with self.lock:
            if year not in self._world_cache:
                if year not in scenarios.START_DATES:
                    raise ValueError("the start year must be 2021 or 2026")
                world = build_real_world(year).world
                self._world_cache[year] = {
                    "year": year,
                    "countries": countries(year),
                    "provinces": [[p.name, p.owner, p.controller, p.lat, p.lon, round(p.area_km2)]
                                  for p in sorted(world.provinces.values(), key=lambda p: p.id)],
                }
            return self._world_cache[year]

    def geometry(self) -> bytes:
        if self._geometry is None:
            self._geometry = GEOMETRY.read_bytes()
        return self._geometry

    def start(self, setup: Setup) -> Session:
        sim = setup.build()  # Outside the lock: building the world takes a moment.
        with self.lock:
            if self.session is not None:
                self.session.close()
            self.session = Session(sim, setup)
            return self.session

    def current(self) -> Session:
        if self.session is None:
            raise NotFound("no war is running")
        return self.session

    def province(self, pid: int) -> dict[str, Any]:
        try:
            return self.current().province(pid)
        except KeyError:
            raise NotFound(f"no province {pid}") from None


def handler_for(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"WARGAME/{__version__}"

        def log_message(self, format: str, *args: Any) -> None:  # Keep the console quiet.
            pass

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    self._file(STATIC / "index.html")
                elif url.path.startswith("/static/"):
                    self._file(STATIC / Path(url.path).name)
                elif url.path == "/api/meta":
                    self._json(app.meta())
                elif url.path == "/api/world":
                    self._json(app.world(int(query.get("year", 2026))))
                elif url.path == "/api/geometry":
                    self._bytes(app.geometry(), "application/json")
                elif url.path == "/api/state":
                    self._json(app.current().state(int(query.get("since", 0)), int(query.get("map", -1))))
                elif url.path.startswith("/api/province/"):
                    self._json(app.province(int(url.path.rsplit("/", 1)[1])))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
            except NotFound as e:
                self._error(HTTPStatus.NOT_FOUND, str(e))
            except ValueError as e:
                self._error(HTTPStatus.BAD_REQUEST, str(e))
            except Exception as e:  # A bug: say so, rather than hiding it as a missing page.
                self._internal(e)

        def do_POST(self) -> None:
            url = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if url.path == "/api/start":
                    session = app.start(Setup.from_json(body))
                    self._json(session.state())
                elif url.path == "/api/speed":
                    app.current().set_speed(str(body.get("speed", "PAUSED")))
                    self._json({"speed": body.get("speed")})
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
            except NotFound as e:
                self._error(HTTPStatus.NOT_FOUND, str(e))
            except ValueError as e:  # Includes malformed JSON and unknown countries, goals or speeds.
                self._error(HTTPStatus.BAD_REQUEST, str(e))
            except Exception as e:
                self._internal(e)

        def _internal(self, e: Exception) -> None:
            traceback.print_exc(file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"internal error: {type(e).__name__}: {e}")

        def _file(self, path: Path) -> None:
            if not path.is_file() or path.parent != STATIC:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            self._bytes(path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

        def _json(self, data: Any) -> None:
            self._bytes(json.dumps(data, separators=(",", ":")).encode(), "application/json")

        def _error(self, status: HTTPStatus, message: str) -> None:
            body = json.dumps({"error": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def make_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), handler_for(App()))
    server.daemon_threads = True
    return server


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"WARGAME {__version__} is running at {url}  (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
