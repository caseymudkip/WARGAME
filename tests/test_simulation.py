from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from conftest import build_world, occupy

from wargame.conflict.war_goal import WarGoal
from wargame.core.clock import Cadence, SimClock, TimeScale
from wargame.core.enums import EscalationTier, Motivation, WarGoalType
from wargame.simulation import ScenarioConfig, Simulation


def make_sim() -> Simulation:
    world = build_world()
    world.country("CAL").relations.update({"BOR": 0.8, "ARD": -0.5})
    goal = WarGoal(WarGoalType.TERRITORIAL_CONQUEST, "ARD", "BOR", frozenset({1, 2, 3, 4}))
    sim = Simulation(world, ScenarioConfig("determinism", datetime(2026, 1, 1), goal, EscalationTier.PROXY_WAR,
                                           attacker_motivation=Motivation.AGGRESSIVE, seed=42))

    def front(s: Simulation) -> None:  # Advance one province every 6 days, bleeding as we go.
        war = s.wars[0]
        if war.ended:
            return
        war.record_casualties(s.world, "ARD", 900)
        war.record_casualties(s.world, "BOR", 700)
        if s.clock.day % 6 == 0 and s.clock.day // 6 <= 4:
            occupy(s.world, [s.clock.day // 6], "ARD")

    sim.register_system(Cadence.DAILY, front)
    return sim


def snapshot(sim: Simulation):
    war = sim.wars[0]
    return (
        sim.clock.hours_elapsed,
        round(war.war_score, 9),
        [(e.hour, e.kind) for e in sim.events()],
        {t: round(p.resolve, 9) for t, p in war.participants.items()},
        {pid: (p.owner, p.controller) for pid, p in sim.world.provinces.items()},
    )


def test_clock_cadences():
    clock = SimClock(datetime(2026, 1, 1))
    fired = [clock.advance() for _ in range(168)]
    assert fired[0] == [Cadence.HOURLY]
    assert fired[23] == [Cadence.HOURLY, Cadence.DAILY]
    assert fired[167] == [Cadence.HOURLY, Cadence.DAILY, Cadence.WEEKLY]
    assert clock.now == datetime(2026, 1, 8)


def test_playback_speed_never_changes_the_outcome():
    slow, fast = make_sim(), make_sim()

    slow.set_speed(TimeScale.HOUR_BY_HOUR)
    for _ in range(24 * 40):
        slow.advance_frame(1.0)

    fast.set_speed(TimeScale.WEEK_BY_WEEK)
    for _ in range(5):
        fast.advance_frame(1.0)          # 5 weeks
    fast.set_speed(TimeScale.DAY_BY_DAY)
    for _ in range(5):
        fast.advance_frame(1.0)          # + 5 days = 40 days

    assert snapshot(slow) == snapshot(fast)


def test_paused_simulation_does_not_advance():
    sim = make_sim()
    sim.set_speed(TimeScale.PAUSED)
    assert sim.advance_frame(10.0) == 0
    assert sim.clock.hours_elapsed == 0


def test_scenario_is_locked_once_running():
    sim = make_sim()
    with pytest.raises(dataclasses.FrozenInstanceError):
        sim.scenario.escalation_tier = EscalationTier.UNRESTRICTED  # type: ignore[misc]


def test_proxy_war_runs_to_a_conclusion():
    sim = make_sim()
    for _ in range(365):
        sim.run_days(1)
        if sim.finished:
            break
    kinds = [e.kind for e in sim.events()]
    assert sim.finished
    assert kinds[0] == "declaration"
    assert "lend_lease" in kinds
    assert kinds[-1] == "peace"
