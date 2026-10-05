"""A war run in the browser: the engine under Pyodide (Python compiled to WebAssembly), driven from JavaScript.

The page asks for days; the engine fights them and answers with day frames in the replay format
(wargame/replay.py), so the browser shows a live war exactly as it shows a recorded one. Everything crosses
the boundary as JSON strings.
"""

from __future__ import annotations

import json
from typing import Any

from wargame import scenarios
from wargame.app.session import Setup
from wargame.replay import Recorder

MAX_DAYS_PER_CALL = 60


class LiveWar:
    def __init__(self, setup_json: str) -> None:
        setup = Setup.from_json(json.loads(setup_json))
        self.year = scenarios.PRESETS[setup.preset].year if setup.preset else setup.year
        self.sim = setup.build()
        self.recorder = Recorder(self.sim)
        self.recorder.frame()
        self._sent_frames = 1

    def header(self) -> str:
        nuclear = self.sim.scenario.nuclear_weapons_enabled
        return json.dumps(self.recorder.header("custom", self.year, nuclear=nuclear), separators=(",", ":"))

    def advance(self, days: int) -> str:
        """Fight up to `days` more days (fewer if the war ends) and return what happened."""
        for _ in range(max(1, min(days, MAX_DAYS_PER_CALL))):
            if self.sim.finished:
                break
            self.sim.run_days(1)
            self.recorder.frame()
        update: dict[str, Any] = {"frames": self.recorder.frames[self._sent_frames:], **self.recorder.outcome()}
        self._sent_frames = len(self.recorder.frames)
        return json.dumps(update, separators=(",", ":"))
