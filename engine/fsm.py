"""The fishing state machine: cast, wait, hook, repeat — in modes A, B and C.

States (``docs/PLAN.md``, "fishing state machine")::

    IDLE ──start──▶ RECOVER ──lure due──▶ LURE ──cast time──▶ CAST
                       ▲                                       │ land delay
                       │                    (mode B / pixel) LOCATE ──bobber──▶ WATCH
                       │                                       │ (A, C)            │
                       │                                       └──────────────────▶│
                       │◀── post-hook delay ── HOOK ◀──────── bite heard ─────────┘
                       └◀──────────────────── miss (timeout / no bobber) ──────────┘

The machine is pure: it owns no clock, thread, capture or input. Each :meth:`tick` gets the
time and the latest signals (a bite heard, a bobber located, whether WoW has focus), and
every side effect goes through an injected :class:`GameActions`. That is what lets the tests
drive half an hour of fishing in milliseconds with a fake clock, and ``--dry-run`` log
every key instead of pressing it.

Which key does what:

==========  ===================  ==================================================
 mode        cast                 hook
==========  ===================  ==================================================
 A           ``fish_key``         ``fish_key`` again — the one-button addon interacts
 B           ``cast_key``         (shift+)right-click on the located bobber
 C           ``cast_key``         ``interact_key`` (soft-target interact)
==========  ===================  ==================================================

The bite detector is armed only while :meth:`listening` is true: from ``land_delay`` after
the cast until the bite. The cast whoosh, the landing plop and the loot sound all fall
outside that window, which is exactly what the live test showed was needed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from engine.config import Lure, Mode, Profile
from engine.lures import LureScheduler
from engine.stats import CatchRecord, SessionStats

#: Mode B: how long to keep looking for the bobber after it should have landed.
LOCATE_TIMEOUT_S = 3.0
#: Extra wait after a lure's cast time before casting the line.
LURE_SETTLE_S = 0.5


class State(StrEnum):
    IDLE = "IDLE"
    LURE = "LURE"
    CAST = "CAST"
    LOCATE = "LOCATE"
    WATCH = "WATCH"
    HOOK = "HOOK"
    RECOVER = "RECOVER"


class Status(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class BiteSignal:
    """A bite, already fused across detectors. ``t`` is on the shared clock."""

    t: float
    strength_db: float = 0.0
    source: str = "audio"


@dataclass(frozen=True, slots=True)
class Signals:
    bite: BiteSignal | None = None
    #: Mode B: where the bobber is, in WoW client coordinates.
    bobber: tuple[int, int] | None = None
    #: Whether WoW has focus; ``None`` when not checked (dry run, simulation).
    focused: bool | None = None


NO_SIGNALS = Signals()


class GameActions(Protocol):
    """Everything the machine may do to the game."""

    def press(self, key: str) -> None: ...

    def right_click(self, x: int, y: int, *, shift: bool) -> None: ...

    def release_all(self) -> None: ...


@dataclass(frozen=True, slots=True)
class FsmEvent:
    """Something worth showing: a state change, a log line or a catch."""

    t: float
    kind: str  # "state" | "log" | "catch"
    message: str
    level: str = "info"
    state: State | None = None
    status: Status | None = None
    reason: str | None = None
    record: CatchRecord | None = None


@dataclass
class _Cast:
    """Book-keeping for the cast in flight."""

    at: float
    arm_at: float
    bite_at: float | None = None
    hook_at: float | None = None
    hooked: bool = False
    bobber: tuple[int, int] | None = None


@dataclass
class _Lure:
    lure: Lure
    pressed_at: float


@dataclass
class FishingFSM:
    profile: Profile
    actions: GameActions
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        self.stats = SessionStats()
        self.lures = LureScheduler(self.profile.lures)
        self.state = State.IDLE
        self.status = Status.STOPPED
        self.reason: str | None = None
        self.events: list[FsmEvent] = []
        self._until = 0.0
        self._cast: _Cast | None = None
        self._lure: _Lure | None = None
        self._pending_profile: Profile | None = None

    # ==================================================================================
    # control
    # ==================================================================================

    def start(self, now: float) -> None:
        if self.status is Status.RUNNING:
            return
        if self.status is Status.PAUSED:
            self.resume(now)
            return
        self.stats = SessionStats()
        self.stats.start(now)
        self.status = Status.RUNNING
        self.reason = None
        self._enter_recover(now, 0.0)
        self._log(now, f"started in mode {self.profile.mode.value}")

    def pause(self, now: float, reason: str = "paused") -> None:
        if self.status is not Status.RUNNING:
            return
        self.actions.release_all()
        self.stats.pause(now)
        self.status = Status.PAUSED
        self.reason = reason
        self._cast = None
        self._emit_state(now)

    def resume(self, now: float) -> None:
        if self.status is not Status.PAUSED:
            return
        self.stats.resume(now)
        self.status = Status.RUNNING
        self.reason = None
        # The line may or may not still be out; start clean with a fresh cast.
        self._enter_recover(now, 0.0)

    def stop(self, now: float, reason: str = "stopped") -> None:
        if self.status is Status.STOPPED:
            return
        self.actions.release_all()
        self.stats.pause(now)
        self.status = Status.STOPPED
        self.reason = reason
        self.state = State.IDLE
        self._cast = None
        self._lure = None
        self._emit_state(now)

    def emergency_stop(self, now: float) -> None:
        """Release everything first, then stop. Must be quick and must not raise."""
        try:
            self.actions.release_all()
        finally:
            self.stop(now, "emergency stop")

    def update_profile(self, profile: Profile) -> None:
        """Takes effect at the next cast, never in the middle of one."""
        self._pending_profile = profile

    def apply_lure_now(self) -> None:
        self.lures.apply_now()

    # ==================================================================================
    # queries
    # ==================================================================================

    def listening(self, now: float) -> bool:
        """Whether the bite detector should be armed right now."""
        return (
            self.status is Status.RUNNING
            and self.state is State.WATCH
            and self._cast is not None
            and now >= self._cast.arm_at
        )

    def wants_bobber(self) -> bool:
        return self.status is Status.RUNNING and self.state is State.LOCATE

    @property
    def cast_at(self) -> float | None:
        return self._cast.at if self._cast is not None else None

    def drain_events(self) -> list[FsmEvent]:
        events, self.events = self.events, []
        return events

    # ==================================================================================
    # the tick
    # ==================================================================================

    def tick(self, now: float, signals: Signals = NO_SIGNALS) -> None:
        if self.status is not Status.RUNNING:
            return
        if signals.focused is False and self.profile.stop.pause_on_focus_loss:
            self.pause(now, "WoW lost focus")
            return
        if self._duration_limit_reached(now):
            return

        if self.state is State.RECOVER:
            self._tick_recover(now)
        elif self.state is State.LURE:
            self._tick_lure(now)
        elif self.state is State.CAST:
            self._tick_cast(now)
        elif self.state is State.LOCATE:
            self._tick_locate(now, signals)
        elif self.state is State.WATCH:
            self._tick_watch(now, signals)
        elif self.state is State.HOOK:
            self._tick_hook(now)

    def _tick_recover(self, now: float) -> None:
        if now < self._until:
            return
        lure = self.lures.due(now)
        if lure is not None:
            self.actions.press(lure.key)
            self._lure = _Lure(lure, now)
            self._until = now + lure.cast_time_s + LURE_SETTLE_S + self._jitter()
            self._set_state(now, State.LURE)
            self._log(now, f"applying lure {lure.name} ({lure.key})")
            return
        self._do_cast(now)

    def _tick_lure(self, now: float) -> None:
        if now < self._until or self._lure is None:
            return
        self.lures.applied(self._lure.lure, now)
        self.stats.record_lure()
        self._log(now, f"lure {self._lure.lure.name} on for {self._lure.lure.duration_min:g} min")
        self._lure = None
        self._do_cast(now)

    def _do_cast(self, now: float) -> None:
        if self._pending_profile is not None:
            self.profile = self._pending_profile
            self.lures.config = self.profile.lures
            self._pending_profile = None
            self._log(now, "new settings in effect")

        key = (
            self.profile.keys.fish_key
            if self.profile.mode is Mode.SINGLE_BUTTON
            else (self.profile.keys.cast_key)
        )
        self.actions.press(key)
        self.stats.record_cast()
        land = self.profile.timing.land_delay_ms / 1000.0
        self._cast = _Cast(at=now, arm_at=now + land)
        self._until = now + land
        self._set_state(now, State.CAST)
        self._log(now, f"cast #{self.stats.casts} ({key})")

    def _tick_cast(self, now: float) -> None:
        if now < self._until:
            return
        if self.profile.requires_bobber_vision():
            self._until = now + LOCATE_TIMEOUT_S
            self._set_state(now, State.LOCATE)
        else:
            self._set_state(now, State.WATCH)

    def _tick_locate(self, now: float, signals: Signals) -> None:
        if self._cast is None:
            return
        if signals.bobber is not None:
            self._cast.bobber = signals.bobber
            self._cast.arm_at = max(self._cast.arm_at, now)
            self._set_state(now, State.WATCH)
            self._log(now, f"bobber at {signals.bobber}")
        elif now >= self._until:
            self._miss(now, "bobber not found")

    def _tick_watch(self, now: float, signals: Signals) -> None:
        cast = self._cast
        if cast is None:
            return
        bite = signals.bite
        if bite is not None and bite.t >= cast.arm_at:
            cast.bite_at = bite.t
            cast.hook_at = now + self.profile.timing.hook_delay_ms / 1000.0 + self._jitter()
            self._set_state(now, State.HOOK)
            self._log(
                now,
                f"bite after {bite.t - cast.at:.1f}s (+{bite.strength_db:.1f} dB, {bite.source})",
            )
            return
        if now - cast.at >= self.profile.timing.max_wait_ms / 1000.0:
            self._miss(now, f"no bite in {self.profile.timing.max_wait_ms / 1000:g}s")

    def _tick_hook(self, now: float) -> None:
        cast = self._cast
        if cast is None or cast.hook_at is None or cast.bite_at is None:
            return
        if not cast.hooked:
            if now < cast.hook_at:
                return
            self._do_hook(cast)
            cast.hooked = True
            record = CatchRecord(
                t=now, wait_s=cast.bite_at - cast.at, reaction_s=now - cast.bite_at
            )
            self.stats.record_catch(record)
            self.events.append(
                FsmEvent(
                    t=now, kind="catch", message=f"hooked #{self.stats.catches}", record=record
                )
            )
            self._until = now + self.profile.timing.post_hook_delay_ms / 1000.0 + self._jitter()
            return
        if now >= self._until:
            self._enter_recover(now, self.profile.timing.between_casts_ms / 1000.0)

    def _do_hook(self, cast: _Cast) -> None:
        mode = self.profile.mode
        if mode is Mode.SINGLE_BUTTON:
            self.actions.press(self.profile.keys.fish_key)
        elif mode is Mode.INTERACT_KEY:
            self.actions.press(self.profile.keys.interact_key)
        else:
            if cast.bobber is None:  # cannot happen: WATCH is only reached with a bobber
                return
            x, y = cast.bobber
            self.actions.right_click(x, y, shift=self.profile.mouse.shift_right_click)

    # ==================================================================================
    # outcomes and limits
    # ==================================================================================

    def _miss(self, now: float, why: str) -> None:
        self.stats.record_miss()
        self._log(now, f"miss: {why}", level="warn")
        limit = self.profile.stop.consecutive_misses
        if self.stats.consecutive_misses >= limit:
            self.pause(now, f"Stopped: {limit} misses in a row")
            return
        self._enter_recover(now, self.profile.timing.between_casts_ms / 1000.0)

    def _enter_recover(self, now: float, wait_s: float) -> None:
        limit = self.profile.stop.max_catches
        if limit is not None and self.stats.catches >= limit:
            self.stop(now, f"Stopped: {limit} catches")
            return
        self._cast = None
        self._until = now + wait_s + (self._jitter() if wait_s > 0 else 0.0)
        self._set_state(now, State.RECOVER)

    def _duration_limit_reached(self, now: float) -> bool:
        minutes = self.profile.stop.max_duration_min
        if minutes is None or self.stats.active_seconds(now) < minutes * 60:
            return False
        self.stop(now, f"Stopped: session reached {minutes} min")
        return True

    # ==================================================================================
    # helpers
    # ==================================================================================

    def _jitter(self) -> float:
        return self.rng.uniform(0.0, self.profile.timing.jitter_ms) / 1000.0

    def _set_state(self, now: float, state: State) -> None:
        if state is not self.state:
            self.state = state
            self._emit_state(now)

    def _emit_state(self, now: float) -> None:
        self.events.append(
            FsmEvent(
                t=now,
                kind="state",
                message=f"{self.status.value} {self.state.value}"
                + (f" - {self.reason}" if self.reason else ""),
                state=self.state,
                status=self.status,
                reason=self.reason,
            )
        )

    def _log(self, now: float, message: str, level: str = "info") -> None:
        self.events.append(FsmEvent(t=now, kind="log", message=message, level=level))
