"""The fishing state machine, driven tick by tick with a fake clock and fake actions.

Jitter is zeroed so every deadline is exact; one test turns it back on to check it only
ever adds time.
"""

import random

import pytest

from engine.config import (
    Lure,
    LureConfig,
    Mode,
    MouseOptions,
    Profile,
    StopConditions,
    TimingConfig,
)
from engine.fsm import BiteSignal, FishingFSM, Signals, State, Status

TICK = 0.01


class FakeActions:
    def __init__(self) -> None:
        self.log: list[tuple] = []
        self.released = 0

    def press(self, key: str) -> None:
        self.log.append(("press", key))

    def right_click(self, x: int, y: int, *, shift: bool) -> None:
        self.log.append(("right_click", x, y, shift))

    def release_all(self) -> None:
        self.released += 1

    @property
    def presses(self) -> list[str]:
        return [entry[1] for entry in self.log if entry[0] == "press"]


def profile(**overrides) -> Profile:
    base = Profile(
        timing=TimingConfig(
            land_delay_ms=2000,
            max_wait_ms=30000,
            hook_delay_ms=300,
            post_hook_delay_ms=1000,
            between_casts_ms=600,
            jitter_ms=0,
        )
    )
    return base.model_copy(update=overrides)


class Rig:
    """An FSM plus a clock you can advance."""

    def __init__(self, prof: Profile | None = None) -> None:
        self.actions = FakeActions()
        self.fsm = FishingFSM(prof or profile(), self.actions, random.Random(0))
        self.t = 100.0

    def start(self) -> None:
        self.fsm.start(self.t)
        self.fsm.tick(self.t)

    def run(self, seconds: float, signals: Signals | None = None) -> None:
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.t = round(self.t + TICK, 6)
            self.fsm.tick(self.t, signals or Signals())

    def bite(self, after: float = 0.0) -> None:
        """Deliver one bite now (after advancing ``after`` seconds)."""
        self.run(after)
        self.fsm.tick(self.t, Signals(bite=BiteSignal(t=self.t, strength_db=14.0)))


# ======================================================================================
# mode A
# ======================================================================================


def test_start_casts_immediately_with_the_fish_key():
    rig = Rig()
    rig.start()
    assert rig.actions.presses == ["]"]
    assert rig.fsm.state is State.CAST
    assert rig.fsm.stats.casts == 1


def test_listens_only_after_the_land_delay():
    rig = Rig()
    rig.start()
    rig.run(1.99)
    assert not rig.fsm.listening(rig.t)
    rig.run(0.02)
    assert rig.fsm.state is State.WATCH
    assert rig.fsm.listening(rig.t)


def test_bite_hooks_with_the_same_key_after_the_reaction_delay():
    rig = Rig()
    rig.start()
    rig.run(2.0)
    rig.bite(after=8.0)
    assert rig.fsm.state is State.HOOK
    rig.run(0.29)
    assert rig.actions.presses == ["]"]  # not yet
    rig.run(0.02)
    assert rig.actions.presses == ["]", "]"]
    assert rig.fsm.stats.catches == 1
    record = rig.fsm.stats.log[0]
    assert record.wait_s == pytest.approx(10.0, abs=0.02)
    assert record.reaction_s == pytest.approx(0.3, abs=0.02)


def test_recasts_after_loot_and_between_casts_delay():
    rig = Rig()
    rig.start()
    rig.run(2.0)
    rig.bite(after=5.0)
    rig.run(0.31)  # hook
    rig.run(1.0)  # post-hook: loot
    assert rig.fsm.state is State.RECOVER
    rig.run(0.61)
    assert rig.actions.presses == ["]", "]", "]"]
    assert rig.fsm.stats.casts == 2


def test_a_bite_from_before_arming_is_ignored():
    rig = Rig()
    rig.start()
    rig.run(2.5)
    stale = BiteSignal(t=rig.t - 1.0)  # heard while the bobber was still landing
    rig.fsm.tick(rig.t, Signals(bite=stale))
    assert rig.fsm.state is State.WATCH


def test_the_splash_tail_does_not_hook_twice():
    rig = Rig()
    rig.start()
    rig.run(2.0)
    rig.bite(after=5.0)
    rig.run(0.5)
    rig.bite()  # the second onset of the same splash, ~0.8 s later
    rig.run(0.2)
    assert rig.actions.presses.count("]") == 2  # cast + one hook
    assert rig.fsm.stats.catches == 1


# ======================================================================================
# misses and stop conditions
# ======================================================================================


def test_timeout_is_a_miss_and_recasts():
    rig = Rig()
    rig.start()
    rig.run(30.0)
    assert rig.fsm.stats.misses == 1
    assert rig.fsm.state is State.RECOVER
    rig.run(0.61)
    assert rig.fsm.stats.casts == 2


def test_consecutive_misses_pause_with_the_reason():
    rig = Rig(profile(stop=StopConditions(consecutive_misses=3)))
    rig.start()
    rig.run(3 * 30.7)
    assert rig.fsm.status is Status.PAUSED
    assert rig.fsm.reason == "Stopped: 3 misses in a row"
    assert rig.actions.released >= 1


def test_a_catch_resets_the_miss_streak():
    rig = Rig(profile(stop=StopConditions(consecutive_misses=2)))
    rig.start()
    rig.run(30.0)  # miss 1
    rig.run(0.61 + 2.0)
    rig.bite(after=4.0)
    rig.run(2.0)
    assert rig.fsm.stats.consecutive_misses == 0
    assert rig.fsm.status is Status.RUNNING


def test_max_catches_stops_the_session():
    rig = Rig(profile(stop=StopConditions(max_catches=1)))
    rig.start()
    rig.run(2.0)
    rig.bite(after=3.0)
    rig.run(2.0)
    assert rig.fsm.status is Status.STOPPED
    assert rig.fsm.reason == "Stopped: 1 catches"


def test_session_time_limit_stops_it():
    rig = Rig(profile(stop=StopConditions(max_duration_min=1)))
    rig.start()
    rig.run(61.0)
    assert rig.fsm.status is Status.STOPPED
    assert "1 min" in rig.fsm.reason


def test_losing_focus_pauses_and_resume_starts_with_a_fresh_cast():
    rig = Rig()
    rig.start()
    rig.run(5.0)
    rig.fsm.tick(rig.t, Signals(focused=False))
    assert rig.fsm.status is Status.PAUSED
    assert rig.fsm.reason == "WoW lost focus"
    assert not rig.fsm.listening(rig.t)

    rig.run(10.0)  # nothing happens while paused
    assert rig.actions.presses == ["]"]

    rig.fsm.resume(rig.t)
    rig.fsm.tick(rig.t)
    assert rig.actions.presses == ["]", "]"]


def test_paused_time_does_not_count_towards_the_session():
    rig = Rig()
    rig.start()
    rig.run(10.0)
    rig.fsm.pause(rig.t)
    rig.run(100.0)
    rig.fsm.resume(rig.t)
    assert rig.fsm.stats.active_seconds(rig.t) == pytest.approx(10.0, abs=0.05)


def test_emergency_stop_releases_and_stops():
    rig = Rig()
    rig.start()
    rig.run(3.0)
    rig.fsm.emergency_stop(rig.t)
    assert rig.fsm.status is Status.STOPPED
    assert rig.fsm.state is State.IDLE
    assert rig.fsm.reason == "emergency stop"
    assert rig.actions.released >= 1


# ======================================================================================
# modes B and C
# ======================================================================================


def test_mode_c_casts_with_the_cast_key_and_hooks_with_interact():
    rig = Rig(profile(mode=Mode.INTERACT_KEY))
    rig.start()
    rig.run(2.0)
    rig.bite(after=6.0)
    rig.run(0.31)
    assert rig.actions.presses == ["9", "f"]


def test_mode_b_locates_then_right_clicks_the_bobber():
    rig = Rig(profile(mode=Mode.RIGHT_CLICK, mouse=MouseOptions(shift_right_click=True)))
    rig.start()
    rig.run(2.01)
    assert rig.fsm.state is State.LOCATE
    assert rig.fsm.wants_bobber()
    rig.fsm.tick(rig.t, Signals(bobber=(640, 360)))
    assert rig.fsm.state is State.WATCH
    rig.bite(after=5.0)
    rig.run(0.31)
    assert rig.actions.log[-1] == ("right_click", 640, 360, True)


def test_mode_b_without_a_bobber_is_a_miss():
    rig = Rig(profile(mode=Mode.RIGHT_CLICK))
    rig.start()
    rig.run(2.0 + 3.05)
    assert rig.fsm.stats.misses == 1


# ======================================================================================
# lures, live config, jitter
# ======================================================================================


def lure_profile(**lure_config) -> Profile:
    lures = LureConfig(
        auto_apply=True,
        refresh_margin_s=15,
        lures=[Lure(name="Bauble", key="7", duration_min=1.0, cast_time_s=2.0)],
        **lure_config,
    )
    return profile(lures=lures)


def test_lure_goes_on_before_the_first_cast():
    rig = Rig(lure_profile())
    rig.start()
    assert rig.actions.presses == ["7"]
    assert rig.fsm.state is State.LURE
    rig.run(2.51)
    assert rig.actions.presses == ["7", "]"]
    assert rig.fsm.stats.lures_applied == 1


def test_lure_is_reapplied_when_its_timer_runs_low():
    rig = Rig(lure_profile())
    rig.start()
    rig.run(2.51)
    applied = rig.t
    # Fish for a while: the 60 s lure must be renewed at ~45 s (15 s margin), between casts.
    # One catch cycle is ~7 s (2 land + 3 wait + 0.3 hook + 1 loot + 0.6 between).
    for _ in range(8):
        rig.run(2.0)
        rig.bite(after=3.0)
        rig.run(2.0)
    lure_presses = [i for i, key in enumerate(rig.actions.presses) if key == "7"]
    assert len(lure_presses) == 2
    assert rig.fsm.lures.remaining(rig.t) > 15
    assert rig.t - applied > 45


def test_apply_now_forces_a_lure_at_the_next_cast():
    rig = Rig(profile(lures=LureConfig(lures=[Lure(name="Bauble", key="7")])))  # auto off
    rig.start()
    assert "7" not in rig.actions.presses
    rig.fsm.apply_lure_now()
    rig.run(2.0)
    rig.bite(after=3.0)
    rig.run(2.0)
    assert "7" in rig.actions.presses


def test_profile_changes_take_effect_at_the_next_cast():
    rig = Rig()
    rig.start()
    rig.run(2.5)
    rig.fsm.update_profile(profile(mode=Mode.INTERACT_KEY))
    rig.bite(after=1.0)
    rig.run(0.31)
    assert rig.actions.presses[-1] == "]"  # this cast still finishes in mode A
    rig.run(2.0)
    assert rig.actions.presses[-1] == "9"  # the next one uses mode C's cast key


def test_jitter_only_ever_adds_time():
    jittery = profile(timing=TimingConfig(land_delay_ms=2000, between_casts_ms=600, jitter_ms=400))
    rig = Rig(jittery)
    rig.start()
    rig.run(2.0)
    rig.bite(after=3.0)
    rig.run(0.29)  # hook delay is 300 ms + up to 400 ms of jitter
    assert rig.fsm.stats.catches == 0
    rig.run(0.5)
    assert rig.fsm.stats.catches == 1


def test_stats_snapshot_serialises_to_camel_case():
    rig = Rig()
    rig.start()
    rig.run(2.0)
    rig.bite(after=3.0)
    rig.run(0.5)
    wire = rig.fsm.stats.snapshot(rig.t).model_dump(by_alias=True)
    assert wire["catches"] == 1
    assert wire["successRate"] == 1.0
    assert "catchesPerHour" in wire
    assert "consecutiveMisses" in wire
