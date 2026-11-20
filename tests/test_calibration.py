"""Calibration: the analyses behind each suggestion, and the controller's rules around it.

The steps that listen to WoW or press keys need the game; what they compute from what
they hear does not, and that is what is pinned here.
"""

import random
import threading
import time

import numpy as np
import pytest
from pydantic import ValidationError

from engine import config as cfg
from engine import protocol as proto
from engine.calibration import (
    MAX_OVER_DB,
    MIN_OVER_DB,
    CalibrationError,
    analyse_ambient,
    hsv_range_from_patch,
    suggest_land_delay_ms,
    template_path,
)
from engine.controller import Controller
from engine.session import Session


def sessions(profile, **kwargs):
    return Session(profile, simulate=True, rng=random.Random(0), **kwargs)


# --------------------------------------------------------------------------------------
# sound
# --------------------------------------------------------------------------------------


def test_quiet_spot_gets_the_most_sensitive_setting():
    rng = np.random.default_rng(0)
    levels = list(-63 + rng.normal(0, 1.0, 1000))
    report = analyse_ambient(levels, floor_min_dbfs=-90.0)  # clamp out of the way
    assert report.floor_db == pytest.approx(-63, abs=0.3)
    assert report.suggested_over_db == MIN_OVER_DB


def test_waves_push_the_threshold_up():
    """Ambience that swells 9 dB above its median needs a threshold above that."""
    rng = np.random.default_rng(1)
    levels = list(-63 + rng.normal(0, 1.0, 1000))
    levels[::50] = [-54.0] * len(levels[::50])  # a wave every second, +9 dB
    report = analyse_ambient(levels, floor_min_dbfs=-90.0)
    assert report.peak_over_db == pytest.approx(9.0, abs=0.5)
    assert report.suggested_over_db == pytest.approx(13.0, abs=0.5)
    assert report.suggested_ratio == pytest.approx(10 ** (report.suggested_over_db / 20), abs=0.01)


def test_a_very_noisy_spot_is_capped_where_splashes_still_break_through():
    levels = [-63.0] * 500 + [-40.0] * 20
    assert analyse_ambient(levels).suggested_over_db == MAX_OVER_DB


def test_no_sound_at_all_is_an_error_with_a_hint():
    with pytest.raises(CalibrationError, match="sound"):
        analyse_ambient([])


# --------------------------------------------------------------------------------------
# timing and bobber
# --------------------------------------------------------------------------------------


def test_land_delay_clears_the_slowest_landing():
    assert suggest_land_delay_ms([0.9, 1.1, 1.3]) == 1900
    assert suggest_land_delay_ms([0.61]) == 1300
    assert suggest_land_delay_ms([]) is None


def test_feather_colour_from_a_red_patch():
    patch = np.zeros((3, 3, 3), np.uint8)
    patch[:, :] = (30, 30, 200)  # BGR: a strong red
    colour = hsv_range_from_patch(patch)
    assert colour.h_min == 0
    assert colour.h_max <= 12
    assert colour.s_min >= 40
    assert colour.v_min >= 40


def test_template_lives_next_to_the_profiles(isolated_config_dir):
    path = template_path("STV coast: night")
    assert path.parent == isolated_config_dir / "templates"
    assert path.name == "STV coast_ night-bobber.png"


# --------------------------------------------------------------------------------------
# the controller's rules
# --------------------------------------------------------------------------------------


def test_fishing_cannot_start_while_a_calibration_step_runs():
    controller = Controller(session_factory=sessions)
    release = threading.Event()
    controller.calibrator._start("sound", lambda: (release.wait(5), {})[1])
    try:
        controller.start()
        assert not controller.running
        errors = [e for e in controller.drain_events() if e.type == "error"]
        assert errors and "calibration" in errors[0].message
    finally:
        release.set()
        controller.calibrator.wait(5)


def test_calibration_cannot_start_while_fishing():
    controller = Controller(session_factory=sessions)
    controller.start()
    try:
        with pytest.raises(CalibrationError, match="stop fishing"):
            controller.calibrate_sound(10)
    finally:
        controller.stop()
        controller.wait(5)


def test_a_failing_step_reports_an_error_event():
    controller = Controller(session_factory=sessions)

    def broken():
        msg = "no game window"
        raise CalibrationError(msg)

    controller.calibrator._start("timing", broken)
    controller.calibrator.wait(5)
    events = [e for e in controller.drain_events() if e.type == "calibration"]
    assert events[-1].status == "error"
    assert events[-1].message == "no game window"


def test_finish_stamps_and_saves_the_profile(isolated_config_dir):
    controller = Controller(session_factory=sessions)
    assert controller.profile.calibrated_at is None
    controller.finish_calibration()
    assert controller.profile.calibrated_at is not None
    assert cfg.load_profile(controller.profile.name).calibrated_at is not None


def test_test_run_of_five_casts():
    from engine.clock import SimClock

    sim = SimClock()

    def fast(profile, **kwargs):
        return Session(
            profile,
            simulate=True,
            clock_fn=sim.now,
            sleep_fn=lambda s: (sim.sleep(s), time.sleep(0))[0],  # yield the GIL each tick
            rng=random.Random(3),
            **kwargs,
        )

    controller = Controller(session_factory=fast)
    controller.test_cast(5)
    assert controller.wait(10)
    reason = controller._last_state.reason
    assert reason.startswith("test done:")
    assert reason.endswith("of 5 casts hooked")


def test_list_windows_reports_real_windows():
    controller = Controller(session_factory=sessions)
    controller.list_windows()
    event = next(e for e in controller.drain_events() if e.type == "windows")
    assert all(item.width >= 320 for item in event.windows)


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "list_windows"},
        {"type": "select_window", "hwnd": 1234},
        {"type": "capture_bobber", "x": 0.5, "y": 0.4},
        {"type": "calibrate_sound"},
        {"type": "calibrate_sound", "seconds": 30},
        {"type": "measure_timing", "casts": 3},
        {"type": "cancel_calibration"},
        {"type": "finish_calibration"},
        {"type": "test_cast", "count": 5},
    ],
)
def test_calibration_commands_parse(frame):
    assert proto.parse_command(frame).type == frame["type"]


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "capture_bobber", "x": 1.5, "y": 0.4},
        {"type": "calibrate_sound", "seconds": 1},
        {"type": "measure_timing", "casts": 50},
        {"type": "test_cast", "count": 0},
    ],
)
def test_out_of_range_calibration_commands_are_refused(frame):
    with pytest.raises(ValidationError):
        proto.parse_command(frame)


def test_a_silent_game_is_a_setup_problem_not_a_quiet_spot():
    """Seen live: WoW in the background with its sound off reads as -107 dB."""
    with pytest.raises(CalibrationError, match="silent"):
        analyse_ambient([-107.0] * 1000)


def test_the_suggestion_uses_the_floor_the_detector_will_use():
    """A median under the clamp: the detector measures against the clamp, so must this."""
    levels = [-75.0] * 990 + [-55.0] * 10  # ambience at -75, a few swells to -55
    report = analyse_ambient(levels, floor_min_dbfs=-60.0)
    assert report.floor_db == -60.0
    assert report.peak_over_db == pytest.approx(5.0, abs=0.5)
