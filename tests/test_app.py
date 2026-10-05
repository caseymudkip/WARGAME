"""The spectator app: scenarios, the session thread, the HTTP API and the command line (real map)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from wargame import __main__ as cli
from wargame import replay, scenarios
from wargame.app.live import LiveWar
from wargame.app.server import make_server
from wargame.app.session import Session, Setup
from wargame.core.enums import EscalationTier, WarGoalType


def test_every_preset_builds_a_war_on_the_real_map():
    for fp in scenarios.FLASHPOINTS:
        sim = scenarios.from_preset(fp)
        war = sim.wars[0]
        assert war.goal.holder == fp.attacker and war.goal.target == fp.defender
        if fp.goal in (WarGoalType.BORDER_SKIRMISH, WarGoalType.TERRITORIAL_CONQUEST):
            assert war.goal.province_ids, fp.key  # Named provinces exist on the map.


def test_a_custom_land_grab_claims_the_most_valuable_border_provinces():
    sim = scenarios.custom(2026, "AZE", "ARM", WarGoalType.BORDER_SKIRMISH, EscalationTier.VACUUM)
    world, claimed = sim.world, sim.wars[0].goal.province_ids
    assert len(claimed) == scenarios.AUTO_CLAIM[WarGoalType.BORDER_SKIRMISH]
    for pid in claimed:
        p = world.provinces[pid]
        assert p.owner == "ARM" and any(world.provinces[n].owner == "AZE" for n in p.neighbors)


def test_bad_setups_are_refused():
    with pytest.raises(ValueError):
        Setup(preset="atlantis").build()
    with pytest.raises(ValueError):
        Setup(year=2026, attacker="RUS", defender="RUS").build()
    with pytest.raises(ValueError):
        Setup(year=1999, attacker="RUS", defender="UKR").build()


def test_a_live_war_streams_the_same_frames_as_a_recording():
    """The browser fights custom wars through LiveWar; a war fought in chunks is the war recorded in one go."""
    setup = {"year": 2026, "attacker": "AZE", "defender": "ARM", "goal": "border_skirmish", "tier": 1, "seed": 3}
    live = LiveWar(json.dumps(setup))
    head = json.loads(live.header())
    assert head["key"] == "custom" and head["year"] == 2026 and len(head["frames"]) == 1
    frames = list(head["frames"])
    for days in (5, 1, 9):
        update = json.loads(live.advance(days))
        assert len(update["frames"]) == days and update["ended"] is False
        frames += update["frames"]
    recorded = replay.record(Setup.from_json(setup).build(), "custom", 2026, max_days=15)
    assert [f["d"] for f in frames] == list(range(16))
    assert frames == json.loads(json.dumps(recorded["frames"]))
    assert update["tags"] == recorded["tags"] and update["events"] == recorded["events"]


def test_a_live_war_over_unreachable_claims_freezes_into_an_armistice():
    """Azerbaijan claims two Armenian provinces it doesn't border: the front goes quiet and the war freezes,
    although the air forces never stop raiding."""
    live = LiveWar(json.dumps({"year": 2026, "attacker": "AZE", "defender": "ARM", "goal": "border_skirmish",
                               "tier": 1, "provinces": [105, 107]}))
    update: dict = {"ended": False}
    for _ in range(12):
        update = json.loads(live.advance(60))
        if update["ended"]:
            break
    treaty = update["treaty"]
    assert update["ended"] and treaty["frozen"] and treaty["winner"] is None
    assert "armistice" in treaty["reason"]


def test_a_session_runs_only_when_unpaused():
    session = Session(Setup(preset="kashmir").build(), Setup(preset="kashmir"))
    try:
        time.sleep(0.3)
        assert session.state()["day"] == 0  # Sessions start paused.
        session.set_speed("MONTH_BY_MONTH")
        deadline = time.time() + 20
        while session.state()["day"] < 5 and time.time() < deadline:
            time.sleep(0.1)
        state = session.state()
        assert state["day"] >= 5 and state["speed"] == "MONTH_BY_MONTH"
        assert {m["tag"] for m in state["wars"][0]["sides"]["attacker"]} == {"IND"}
        assert state["names"]["PAK"] == "Pakistan"
        with pytest.raises(ValueError):
            session.set_speed("LUDICROUS")
    finally:
        session.close()


def test_province_control_is_only_resent_when_it_changes():
    session = Session(Setup(preset="kashmir").build(), Setup(preset="kashmir"))
    try:
        first = session.state()
        assert len(first["provinces"]) == len(session.sim.world.provinces)
        again = session.state(first["event_count"], first["map_version"])
        assert "provinces" not in again and again["events"] == []
    finally:
        session.close()


@pytest.fixture(scope="module")
def server():
    srv = make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def test_the_http_api_serves_a_war_end_to_end(server):
    with urllib.request.urlopen(server + "/", timeout=10) as r:
        assert b"WARGAME" in r.read()
    meta = get(server + "/api/meta")
    assert {p["key"] for p in meta["presets"]} == set(scenarios.PRESETS)
    assert "PAUSED" in {s["value"] for s in meta["speeds"]}
    world = get(server + "/api/world?year=2026")
    assert len(world["provinces"]) == 3604 and any(c["tag"] == "UKR" for c in world["countries"])
    with urllib.request.urlopen(server + "/api/geometry", timeout=30) as r:
        assert len(json.loads(r.read())["provinces"]) == 3604

    state = post(server + "/api/start", {"preset": "eritrea"})
    assert state["day"] == 0 and state["wars"][0]["holder"] == "ETH"
    post(server + "/api/speed", {"speed": "MONTH_BY_MONTH"})
    time.sleep(1.0)
    later = get(server + f"/api/state?since={state['event_count']}&map={state['map_version']}")
    assert later["day"] > 0
    pid = state["wars"][0]["goal_provinces"][0]
    province = get(server + f"/api/province/{pid}")
    assert province["owner"] == "ERI"


def test_the_http_api_rejects_nonsense(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server + "/api/start", {"preset": "atlantis"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server + "/api/start", {"year": 2026, "attacker": "RUS", "defender": "UKR", "attacker_motivation": "ANGRY"})
    assert e.value.code == 400
    post(server + "/api/start", {"preset": "kashmir"})
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server + "/api/province/999999")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server + "/api/nowhere")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server + "/static/../../server.py")
    assert e.value.code == 404


def test_the_command_line_runs_a_flashpoint(capsys):
    assert cli.main(["presets"]) == 0
    assert "taiwan" in capsys.readouterr().out
    assert cli.main(["run", "kashmir", "--days", "20"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("India-Pakistan Kashmir clash") and "casualties" in out
