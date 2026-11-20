"""What the state machine does with FishBridge readings: bags, combat, lure, channel end."""

import random

from engine.bridge import BridgeState
from engine.clock import SimClock
from engine.config import Lure, LureConfig, Profile, StopConditions, TimingConfig
from engine.fsm import BiteSignal, FishingFSM, Signals, State, Status
from engine.lures import OBSERVE_GRACE_S
from engine.session import Session
from tests.test_fsm import FakeActions

TICK = 0.01
NO_JITTER = TimingConfig(jitter_ms=0)


def bridge(**fields) -> BridgeState:
    base = dict(
        heartbeat=1,
        free_slots=10,
        total_slots=80,
        in_combat=False,
        pole_equipped=True,
        lure_active=False,
        lure_remaining_s=0.0,
        channelling=False,
        channel_s=0,
    )
    base.update(fields)
    return BridgeState(**base)


class Rig:
    def __init__(self, profile: Profile | None = None) -> None:
        self.actions = FakeActions()
        self.fsm = FishingFSM(profile or Profile(timing=NO_JITTER), self.actions, random.Random(0))
        self.t = 100.0

    def start(self) -> None:
        """Start and cast in the same instant, so deadlines line up with whole seconds."""
        self.fsm.start(self.t)
        self.fsm.tick(self.t)

    def run(self, seconds: float, **signals) -> None:
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.t = round(self.t + TICK, 6)
            self.fsm.tick(self.t, Signals(**signals))


def test_combat_pauses_at_once():
    rig = Rig()
    rig.start()
    rig.run(3.0, bridge=bridge())
    rig.run(0.05, bridge=bridge(in_combat=True))
    assert rig.fsm.status is Status.PAUSED
    assert rig.fsm.reason == "Paused: in combat"
    assert rig.actions.released >= 1


def test_combat_rule_can_be_switched_off():
    rig = Rig(Profile(timing=NO_JITTER, stop=StopConditions(pause_on_combat=False)))
    rig.start()
    rig.run(1.0, bridge=bridge(in_combat=True))
    assert rig.fsm.status is Status.RUNNING


def test_full_bags_stop_before_the_next_cast_not_mid_loot():
    rig = Rig()
    rig.start()
    rig.run(2.0, bridge=bridge(free_slots=1))
    rig.fsm.tick(rig.t, Signals(bite=BiteSignal(t=rig.t), bridge=bridge(free_slots=1)))
    rig.run(0.5, bridge=bridge(free_slots=0))  # hooked; the catch fills the last slot
    assert rig.fsm.status is Status.RUNNING  # still looting
    rig.run(2.0, bridge=bridge(free_slots=0))
    assert rig.fsm.status is Status.STOPPED
    assert rig.fsm.reason == "Stopped: bags are full"
    assert rig.actions.presses.count("]") == 2  # one cast, one hook, no third cast


def test_unknown_bag_count_never_stops_the_bot():
    rig = Rig()
    rig.start()
    rig.run(35.0, bridge=bridge(free_slots=None, total_slots=None))
    assert rig.fsm.status is Status.RUNNING


def test_without_the_addon_nothing_changes():
    rig = Rig()
    rig.start()
    rig.run(35.0)  # no bridge at all: the 30 s timeout applies as before
    assert rig.fsm.stats.misses == 1


def test_an_empty_line_is_recast_as_soon_as_the_channel_ends():
    rig = Rig()
    rig.start()
    rig.run(2.5, bridge=bridge(channelling=True, channel_s=21))
    rig.run(18.0, bridge=bridge(channelling=True, channel_s=21))
    assert rig.fsm.stats.misses == 0
    rig.run(0.05, bridge=bridge(channelling=False))  # the channel ended at ~20.5 s
    assert rig.fsm.stats.misses == 1
    assert rig.fsm.state is State.RECOVER


def test_a_channel_that_has_not_shown_yet_is_not_an_end():
    rig = Rig()
    rig.start()
    rig.run(5.0, bridge=bridge(channelling=False))  # the addon has not caught up
    assert rig.fsm.stats.misses == 0
    assert rig.fsm.state is State.WATCH


def lure_profile() -> Profile:
    return Profile(
        timing=NO_JITTER,
        lures=LureConfig(auto_apply=True, lures=[Lure(name="Bauble", key="7", duration_min=10)]),
    )


def test_a_lure_already_on_in_game_is_not_applied_again():
    rig = Rig(lure_profile())
    rig.start()
    rig.fsm.tick(rig.t, Signals(bridge=bridge(lure_active=True, lure_remaining_s=420.0)))
    # start() asked for a lure before it saw the bridge; from here on the game wins
    assert rig.fsm.lures.remaining(rig.t) is None or rig.fsm.lures.remaining(rig.t) > 400


def test_a_lure_that_dropped_off_is_noticed():
    rig = Rig(lure_profile())
    rig.fsm.lures.applied(rig.fsm.lures.lure, rig.t - OBSERVE_GRACE_S - 1)
    rig.fsm.lures.observe(False, 0.0, rig.t)
    assert rig.fsm.lures.due(rig.t) is not None


def test_a_stale_off_right_after_applying_is_ignored():
    rig = Rig(lure_profile())
    rig.fsm.lures.applied(rig.fsm.lures.lure, rig.t)
    rig.fsm.lures.observe(False, 0.0, rig.t + 0.5)  # the addon has not seen it yet
    assert rig.fsm.lures.due(rig.t + 0.5) is None


def test_simulated_session_stops_on_full_bags():
    sim = SimClock()
    session = Session(
        Profile(stop=StopConditions(consecutive_misses=20)),
        simulate=True,
        clock_fn=sim.now,
        sleep_fn=sim.sleep,
        rng=random.Random(7),
        tick_s=0.02,
    )
    seen = []
    session.on_bridge = seen.append
    session.run(duration_s=3600)
    assert session.fsm.reason == "Stopped: bags are full"
    assert session.stats.catches == 40
    assert seen and all(state is not None for state in seen)
