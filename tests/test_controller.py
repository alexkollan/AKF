"""The controller with simulated sessions: lifecycle, live config, profiles, emergency stop.

Two session factories: one on a SimClock (a session runs as fast as the CPU allows, for
"does it finish" questions) and one on the real clock (for "how fast does it react").
"""

import random
import time

import pytest
from pydantic import ValidationError

from engine import config as cfg
from engine import protocol as proto
from engine.clock import SimClock
from engine.controller import Controller, deep_merge
from engine.session import Session


def fast_sessions(profile, **kwargs):
    sim = SimClock()
    return Session(
        profile,
        simulate=True,
        clock_fn=sim.now,
        sleep_fn=lambda s: (sim.sleep(s), time.sleep(0))[0],  # yield the GIL each tick
        rng=random.Random(0),
        **kwargs,
    )


def realtime_sessions(profile, **kwargs):
    return Session(profile, simulate=True, rng=random.Random(0), **kwargs)


def wait_for(predicate, timeout=2.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return False


def events_of(controller, kind):
    return [event for event in controller.drain_events() if event.type == kind]


def test_deep_merge_keeps_untouched_branches():
    base = {"a": 1, "keys": {"fish_key": "]", "cast_key": "9"}, "lures": {"lures": [1, 2]}}
    merged = deep_merge(base, {"keys": {"cast_key": "8"}, "lures": {"lures": [3]}})
    assert merged == {"a": 1, "keys": {"fish_key": "]", "cast_key": "8"}, "lures": {"lures": [3]}}
    assert base["keys"]["cast_key"] == "9"  # the original is untouched


def test_start_and_stop():
    controller = Controller(session_factory=realtime_sessions)
    controller.start()
    assert wait_for(lambda: controller.status == "running")
    controller.stop()
    assert wait_for(lambda: controller.status == "stopped")
    assert controller.wait(2.0)
    assert not controller.running


def test_snapshot_describes_everything_a_new_client_needs():
    controller = Controller(session_factory=realtime_sessions)
    kinds = [event.type for event in controller.snapshot()]
    assert kinds == ["config", "profiles", "state", "stats"]


def test_emergency_stop_reacts_within_100_ms():
    """M5 acceptance: emergency stop releases everything and stops in under 100 ms."""
    sessions: list[Session] = []

    def factory(profile, **kwargs):
        session = realtime_sessions(profile, **kwargs)
        sessions.append(session)
        return session

    controller = Controller(session_factory=factory)
    controller.start()
    assert wait_for(lambda: controller.status == "running")
    time.sleep(0.3)  # let it get into a cast

    started = time.perf_counter()
    controller.emergency_stop()
    assert wait_for(lambda: controller.status == "stopped", timeout=1.0)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1, f"emergency stop took {elapsed * 1000:.0f} ms"
    assert controller._last_state.reason == "emergency stop"
    assert sessions[0].controller.held == ((), ())
    controller.wait(2.0)


def test_toggle_start_stop_is_the_hotkey_behaviour():
    controller = Controller(session_factory=realtime_sessions)
    controller.toggle_start_stop()
    assert wait_for(lambda: controller.status == "running")
    controller.toggle_start_stop()
    assert wait_for(lambda: controller.status == "stopped")
    controller.wait(2.0)


def test_pause_and_resume():
    controller = Controller(session_factory=realtime_sessions)
    controller.start()
    assert wait_for(lambda: controller.status == "running")
    controller.toggle_pause()
    assert wait_for(lambda: controller.status == "paused")
    controller.start()  # start while paused resumes
    assert wait_for(lambda: controller.status == "running")
    controller.stop()
    controller.wait(2.0)


def test_config_change_while_running_reaches_the_session():
    sessions: list[Session] = []

    def factory(profile, **kwargs):
        session = realtime_sessions(profile, **kwargs)
        sessions.append(session)
        return session

    controller = Controller(session_factory=factory)
    controller.start()
    assert wait_for(lambda: controller.status == "running")
    controller.set_config({"mode": "C"})
    assert controller.profile.mode is cfg.Mode.INTERACT_KEY
    # Handed to the running machine, which holds it until its next cast.
    assert wait_for(
        lambda: (
            sessions[0].fsm._pending_profile is not None
            or sessions[0].fsm.profile.mode is cfg.Mode.INTERACT_KEY
        )
    )
    controller.stop()
    controller.wait(2.0)


def test_invalid_config_is_rejected_and_nothing_changes():
    controller = Controller(session_factory=realtime_sessions)
    before = controller.profile
    with pytest.raises(ValidationError):
        controller.set_config({"timing": {"land_delay_ms": -5}})
    with pytest.raises(ValidationError):
        controller.set_config({"no_such_field": 1})
    assert controller.profile == before


def test_test_cast_stops_after_one_outcome():
    controller = Controller(session_factory=fast_sessions)
    controller.test_cast()
    assert controller.wait(5.0)
    assert controller._last_state.reason.startswith("test cast done")
    stats = controller.stats_event().stats
    assert stats.casts == 1


def test_profiles_save_list_load(isolated_config_dir):
    controller = Controller(session_factory=realtime_sessions)
    controller.set_config({"mode": "C", "notes": "windy"})
    controller.save_profile("lake")
    assert cfg.list_profiles() == ["lake"]
    assert controller.settings.active_profile == "lake"

    controller.set_config({"mode": "A"})
    controller.load_profile("lake")
    assert controller.profile.mode is cfg.Mode.INTERACT_KEY
    assert controller.profile.notes == "windy"

    profiles = events_of(controller, "profiles")
    assert profiles[-1].names == ["lake"]
    assert profiles[-1].active == "lake"


def test_active_profile_is_restored_on_the_next_launch(isolated_config_dir):
    first = Controller(session_factory=realtime_sessions)
    first.set_config({"mode": "C"})
    first.save_profile("stv")
    second = Controller(session_factory=realtime_sessions)
    assert second.profile.name == "stv"
    assert second.profile.mode is cfg.Mode.INTERACT_KEY


def test_settings_change_is_saved_and_announced(isolated_config_dir):
    controller = Controller(session_factory=realtime_sessions)
    seen = []
    controller.on_settings_changed = seen.append
    controller.set_settings({"hotkeys": {"pause": "<f11>"}})
    assert cfg.load_settings().hotkeys.pause == "<f11>"
    assert seen[0].hotkeys.pause == "<f11>"


def test_a_session_that_cannot_start_reports_an_error_state():
    controller = Controller(session_factory=Session)  # the real thing, with mode B
    controller.set_config({"mode": "B"})
    controller.start()
    assert controller.wait(5.0)
    assert controller.status == "error"
    assert "vision" in controller._last_state.reason
    errors = [e for e in controller.drain_events() if isinstance(e, proto.LogEvent)]
    assert errors and errors[-1].line.level == "error"
