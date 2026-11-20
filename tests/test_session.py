"""Sessions end to end on a simulated world and a fast clock.

The M4 acceptance criterion is "30-minute headless session in every mode without a crash";
these run exactly that, in about a second each. Plus the plumbing around the loop: control
requests from other threads, refusing what is not built yet, and the hotkey parser.
"""

import random
import threading

import pytest

from engine.clock import SimClock
from engine.config import FusionMode, Mode, Profile, StopConditions
from engine.fsm import Status
from engine.hotkeys import parse_hotkey
from engine.session import Session, SessionError

THIRTY_MIN = 30 * 60


def simulated(profile: Profile, seed: int = 0) -> tuple[Session, SimClock]:
    sim = SimClock()
    session = Session(
        profile,
        simulate=True,
        clock_fn=sim.now,
        sleep_fn=sim.sleep,
        rng=random.Random(seed),
        tick_s=0.02,
    )
    return session, sim


@pytest.mark.parametrize("mode", list(Mode))
def test_thirty_minute_session_in_every_mode(mode):
    # Bags full would end it early: the simulated FishBridge fills a slot per catch.
    profile = Profile(
        mode=mode, stop=StopConditions(consecutive_misses=20, stop_on_bags_full=False)
    )
    session, sim = simulated(profile, seed=7)
    stats = session.run(duration_s=THIRTY_MIN)

    assert session.fsm.status is Status.STOPPED
    assert stats.active_seconds(sim.now()) == pytest.approx(THIRTY_MIN, abs=1.0)
    # Bites come 6.8-25.9 s after a cast 90% of the time, so roughly a cast every ~20 s.
    assert stats.casts > 60
    assert stats.catches > 0.75 * stats.casts
    assert stats.catches + stats.misses >= stats.casts - 1
    assert session.controller.held == ((), ())  # nothing left pressed


def test_every_action_in_a_dry_session_is_recorded_not_sent():
    session, _sim = simulated(Profile(), seed=1)
    session.run(duration_s=120)
    kinds = {action.kind for action in session.controller.actions}
    assert kinds == {"key_down", "key_up"}
    assert all(action.target == "]" for action in session.controller.actions)


def test_requests_from_another_thread_are_applied_on_the_next_tick():
    session, _sim = simulated(Profile(), seed=2)
    session.request("pause", "coffee")
    seen = []
    session.on_event = seen.append
    session.request("stop")
    session.run(duration_s=60)
    assert any(event.reason == "coffee" for event in seen)
    assert session.fsm.status is Status.STOPPED
    assert session.fsm.reason == "stopped by user"


def test_emergency_stop_request_ends_the_run():
    session, _sim = simulated(Profile(), seed=3)
    session.request("emergency_stop")
    session.run(duration_s=60)
    assert session.fsm.reason == "emergency stop"


def test_request_is_thread_safe():
    session, _sim = simulated(Profile(), seed=4)
    threads = [threading.Thread(target=session.request, args=("apply_lure",)) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(session._requests) == 50


def test_mode_b_is_refused_for_a_real_game_until_vision_exists():
    session = Session(Profile(mode=Mode.RIGHT_CLICK), dry_run=True)
    with pytest.raises(SessionError, match="vision"):
        session.run(duration_s=1)


def test_pixel_fusion_is_refused_for_a_real_game():
    profile = Profile()
    profile.detection.fusion = FusionMode.AUDIO_AND_PIXEL
    with pytest.raises(SessionError, match="pixel"):
        Session(profile, dry_run=True).run(duration_s=1)


@pytest.mark.parametrize(("spec", "vk"), [("<f12>", 0x7B), ("f10", 0x79), ("<F9>", 0x78)])
def test_parse_hotkey(spec, vk):
    assert parse_hotkey(spec) == vk


def test_hotkey_chords_are_rejected_for_now():
    with pytest.raises(ValueError, match="chord"):
        parse_hotkey("<ctrl>+<f12>")
