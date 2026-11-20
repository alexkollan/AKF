"""A fishing session: the state machine wired to the real game, or to a simulation.

:class:`~engine.fsm.FishingFSM` decides; this module supplies what it decides about. Each
loop iteration it:

1. applies any control request (pause, resume, stop, emergency stop) from other threads;
2. feeds the audio queued since the last tick to the splash detector, armed only while the
   machine is listening, and turns a detection into a :class:`~engine.fsm.BiteSignal` on the
   shared clock;
3. checks that WoW still has focus;
4. ticks the machine and hands its events to the ``on_event`` callback.

It blocks the calling thread — run it on a worker thread, never on the asyncio loop.

With ``simulate=True`` the game is replaced by :class:`SimulatedWorld`, which plants bites
at the delays measured in-game; together with :class:`~engine.clock.SimClock` that runs a
half-hour session in about a second, which is how the M4 soak tests work.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from engine import audio as audio_mod
from engine import capture, clock
from engine.bridge import BridgeReader, BridgeState
from engine.config import FusionMode, Mode, MouseOptions, Profile
from engine.fsm import BiteSignal, FishingFSM, FsmEvent, Signals, State, Status
from engine.input import InputController, MouseButton, focus_window, has_focus
from engine.stats import SessionStats

logger = logging.getLogger(__name__)

TICK_S = 0.01
SIMULATED_CHANNEL_S = 30
SIMULATED_TOTAL_SLOTS = 80
#: The UI chart is fed at 10 Hz (docs/PLAN.md, M5).
LEVELS_INTERVAL_S = 0.1
#: FishBridge state for the UI: once a second is plenty for bags and combat.
BRIDGE_INTERVAL_S = 1.0


class SessionError(RuntimeError):
    """The session cannot start with this profile on this machine."""


# --------------------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------------------


class InputActions:
    """:class:`~engine.fsm.GameActions` on top of the real (or dry-run) input controller."""

    def __init__(
        self,
        controller: InputController,
        window: capture.WindowInfo | None,
        mouse: MouseOptions,
    ) -> None:
        self.controller = controller
        self.window = window
        self.mouse = mouse

    def press(self, key: str) -> None:
        self.controller.tap(key)

    def right_click(self, x: int, y: int, *, shift: bool) -> None:
        start = self.controller.cursor_position()
        if self.window is not None:
            x, y = self.window.client_to_screen(x, y)
        self.controller.glide_to(x, y, steps=self.mouse.move_steps)
        self.controller.click(
            MouseButton.RIGHT,
            modifiers=("shift",) if shift else (),
            hold_ms=self.mouse.click_hold_ms,
        )
        if self.mouse.restore_cursor:
            self.controller.move_to(*start)

    def release_all(self) -> None:
        self.controller.release_all()


# --------------------------------------------------------------------------------------
# signal sources
# --------------------------------------------------------------------------------------


class AudioBites:
    """Loopback audio through the splash detector, gated by the machine."""

    def __init__(self, source: audio_mod.AudioSource, detector: audio_mod.SplashDetector) -> None:
        self.source = source
        self.detector = detector

    def poll(self, listening: bool) -> BiteSignal | None:
        if listening and not self.detector.armed:
            self.detector.arm()
        elif not listening and self.detector.armed:
            self.detector.disarm()

        bite: BiteSignal | None = None
        for chunk in self.source.drain():
            event = self.detector.feed(chunk)
            if event is not None:
                bite = BiteSignal(
                    t=self.source.start_time + event.t,
                    strength_db=audio_mod.to_dbfs(event.ratio),
                )
        return bite


@dataclass
class SimulatedWorld:
    """A stand-in for WoW: bites arrive at the delays measured in-game, or not at all."""

    rng: random.Random
    bite_probability: float = 0.9
    bite_window_s: tuple[float, float] = (6.8, 25.9)  # cast key -> bite, measured live
    locate_probability: float = 0.97
    bobber: tuple[int, int] = (960, 540)
    bag_slots: int = 40  # free at the start

    def __post_init__(self) -> None:
        self._seen_cast: float | None = None
        self._bite_at: float | None = None
        self._splash_at = float("-inf")
        self._located = False
        self._heartbeat = 0

    def poll(self, fsm: FishingFSM, now: float) -> Signals:
        cast_at = fsm.cast_at
        if cast_at is not None and cast_at != self._seen_cast:
            self._seen_cast = cast_at
            self._located = False
            self._bite_at = (
                cast_at + self.rng.uniform(*self.bite_window_s)
                if self.rng.random() < self.bite_probability
                else None
            )

        bite = None
        if self._bite_at is not None and now >= self._bite_at and fsm.listening(now):
            bite = BiteSignal(t=self._bite_at, strength_db=15.0, source="simulated")
            self._splash_at = self._bite_at
            self._bite_at = None

        bobber = None
        if fsm.wants_bobber() and not self._located:
            self._located = True
            if self.rng.random() < self.locate_probability:
                bobber = self.bobber
        return Signals(bite=bite, bobber=bobber, bridge=self._bridge(fsm, now))

    def _bridge(self, fsm: FishingFSM, now: float) -> BridgeState:
        """A FishBridge that fills a bag slot per catch, so "bags full" can be seen too."""
        self._heartbeat = (self._heartbeat + 1) % 256
        cast_at = fsm.cast_at
        channelling = (
            cast_at is not None
            and fsm.state in (State.CAST, State.LOCATE, State.WATCH)
            and now - cast_at < SIMULATED_CHANNEL_S
        )
        remaining = fsm.lures.remaining(now)
        return BridgeState(
            heartbeat=self._heartbeat,
            free_slots=max(0, self.bag_slots - fsm.stats.catches),
            total_slots=SIMULATED_TOTAL_SLOTS,
            in_combat=False,
            pole_equipped=True,
            lure_active=bool(remaining),
            lure_remaining_s=remaining or 0.0,
            channelling=channelling,
            channel_s=SIMULATED_CHANNEL_S if channelling else 0,
        )

    def level(self, now: float, armed: bool) -> audio_mod.DetectorLevel:
        """What the detector chart would show: ambience near -63 dBFS, splashes near -32.

        Shaped after the live session, so the UI can be built and checked without WoW.
        """
        floor = -63.0
        level = floor + self.rng.gauss(0.0, 2.0)
        since_splash = now - self._splash_at
        if 0.0 <= since_splash < 1.2:
            level = -30.0 - 12.0 * since_splash + self.rng.gauss(0.0, 2.5)
        return audio_mod.DetectorLevel(
            t=now, level_db=level, floor_db=floor, threshold_db=floor + 10.9, armed=armed
        )


# --------------------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------------------


class Session:
    """Run one fishing session to completion on the calling thread.

    Control methods are thread-safe: hotkeys and the (future) WebSocket call them from
    other threads; the loop applies them at its next tick, at most ~10 ms later.
    """

    def __init__(
        self,
        profile: Profile,
        *,
        dry_run: bool = False,
        simulate: bool = False,
        clock_fn: Callable[[], float] = clock.now,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        on_event: Callable[[FsmEvent], None] | None = None,
        on_levels: Callable[[audio_mod.DetectorLevel, float], None] | None = None,
        on_bridge: Callable[[BridgeState | None], None] | None = None,
        cast_limit: int | None = None,
        tick_s: float = TICK_S,
    ) -> None:
        self.profile = profile
        self.dry_run = dry_run or simulate
        self.simulate = simulate
        self.clock_fn = clock_fn
        self.sleep_fn = sleep_fn
        self.rng = rng or random.Random()
        self.on_event = on_event or (lambda event: None)
        self.on_levels = on_levels
        self.on_bridge = on_bridge
        self.cast_limit = cast_limit
        self.tick_s = tick_s

        self.controller = InputController(dry_run=self.dry_run)
        self.window: capture.WindowInfo | None = None
        self.fsm = FishingFSM(profile, InputActions(self.controller, None, profile.mouse), self.rng)

        self._lock = threading.Lock()
        self._requests: list[tuple[str, object]] = []
        self._last_levels = -LEVELS_INTERVAL_S
        self._last_bridge = -BRIDGE_INTERVAL_S

    # -- control (any thread) ----------------------------------------------------------

    def request(self, command: str, payload: object = None) -> None:
        """Queue a command for the next tick.

        ``pause`` / ``stop`` / ``toggle_pause`` take an optional reason string,
        ``update_profile`` a :class:`~engine.config.Profile`; ``resume``,
        ``emergency_stop`` and ``apply_lure`` take nothing.
        """
        with self._lock:
            self._requests.append((command, payload))

    @property
    def stats(self) -> SessionStats:
        return self.fsm.stats

    # -- run ---------------------------------------------------------------------------

    def run(self, *, duration_s: float | None = None) -> SessionStats:
        """Fish until stopped (or ``duration_s`` of session clock has passed)."""
        world: SimulatedWorld | None = None
        bites: AudioBites | None = None
        source: audio_mod.AudioSource | None = None
        bridge: BridgeReader | None = None

        try:
            if self.simulate:
                world = SimulatedWorld(self.rng)
            else:
                self._check_supported()
                self.window = capture.find_game_window(
                    self.profile.capture.process_name, self.profile.capture.window_title_contains
                )
                self.fsm.actions = InputActions(self.controller, self.window, self.profile.mouse)
                detection = self.profile.detection.audio
                pid = self.window.pid if detection.process_loopback else None
                source = audio_mod.open_audio_source(pid, device_index=detection.device_index)
                bites = AudioBites(source, audio_mod.SplashDetector(detection, source.sample_rate))
                source.start()
                bridge = BridgeReader(self.window)
                if not self.dry_run and not focus_window(self.window):
                    msg = "could not bring WoW to the front; click into the game and retry"
                    raise SessionError(msg)

            started = self.clock_fn()
            self.fsm.start(started)
            self._flush()

            while True:
                now = self.clock_fn()
                self._apply_requests(now)

                if self.fsm.status is Status.STOPPED:
                    break
                if duration_s is not None and now - started >= duration_s:
                    self.fsm.stop(now, f"run ended after {duration_s:g}s")
                    self._flush()
                    break

                if world is not None:
                    signals = world.poll(self.fsm, now)
                    if self.on_levels is not None:
                        self._emit_levels(world.level(now, self.fsm.listening(now)), now)
                else:
                    assert bites is not None and self.window is not None
                    bite = bites.poll(self.fsm.listening(now))
                    focused = None if self.dry_run else has_focus(self.window)
                    state = bridge.poll(now) if bridge is not None else None
                    signals = Signals(bite=bite, focused=focused, bridge=state)
                    self._emit_levels(bites.detector.last, now)

                self.fsm.tick(now, signals)
                self._emit_bridge(signals.bridge, now)
                self._finish_cast_limit(now)
                self._flush()
                self.sleep_fn(self.tick_s)
        finally:
            self.controller.release_all()
            if source is not None:
                source.close()

        return self.fsm.stats

    # -- internals ---------------------------------------------------------------------

    def _check_supported(self) -> None:
        """Refuse configurations that need parts not built yet, before touching the game."""
        if self.profile.mode is Mode.RIGHT_CLICK:
            msg = "mode B needs bobber vision, which is not built yet; use mode A or C"
            raise SessionError(msg)
        if self.profile.detection.fusion is not FusionMode.AUDIO:
            msg = f"fusion '{self.profile.detection.fusion.value}' needs pixel detection; use audio"
            raise SessionError(msg)

    def _apply_requests(self, now: float) -> None:
        with self._lock:
            requests, self._requests = self._requests, []
        for command, payload in requests:
            reason = payload if isinstance(payload, str) else ""
            if command == "emergency_stop":
                self.fsm.emergency_stop(now)
            elif command == "stop":
                self.fsm.stop(now, reason or "stopped by user")
            elif command == "pause":
                self.fsm.pause(now, reason or "paused by user")
            elif command == "resume":
                self.fsm.resume(now)
            elif command == "toggle_pause":
                if self.fsm.status is Status.PAUSED:
                    self.fsm.resume(now)
                else:
                    self.fsm.pause(now, reason or "paused by user")
            elif command == "apply_lure":
                self.fsm.apply_lure_now()
            elif command == "update_profile" and isinstance(payload, Profile):
                self.profile = payload
                self.fsm.update_profile(payload)
            else:
                logger.warning("unknown session command %r", command)
        self._flush()

    def _emit_levels(self, level: audio_mod.DetectorLevel | None, now: float) -> None:
        """Hand the detector's view to the UI chart, at most every ``LEVELS_INTERVAL_S``."""
        if self.on_levels is None or level is None:
            return
        if now - self._last_levels < LEVELS_INTERVAL_S:
            return
        self._last_levels = now
        self.on_levels(level, now)

    def _emit_bridge(self, state: BridgeState | None, now: float) -> None:
        if self.on_bridge is None or now - self._last_bridge < BRIDGE_INTERVAL_S:
            return
        self._last_bridge = now
        self.on_bridge(state)

    def _finish_cast_limit(self, now: float) -> None:
        """Test runs ("Test one cast", calibration's five casts): stop once enough casts
        have an outcome, and say how they went."""
        if self.cast_limit is None or self.fsm.status is not Status.RUNNING:
            return
        stats = self.fsm.stats
        done = stats.catches + stats.misses
        if done >= self.cast_limit and self.fsm.state is State.RECOVER:
            self.fsm.stop(now, f"test done: {stats.catches} of {done} casts hooked")

    def _flush(self) -> None:
        for event in self.fsm.drain_events():
            self.on_event(event)
