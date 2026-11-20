"""The engine's single point of control: what the WebSocket and the hotkeys both call.

The controller owns the active profile and settings, starts and stops sessions on a worker
thread, and turns everything that happens into wire events on a thread-safe queue. It
knows nothing about FastAPI or asyncio: the server drains :meth:`drain_events` from its
own loop, the hotkeys call methods from the hook thread, and the session thread reports in
through callbacks — none of them block each other.

Commands that touch the disk or the screen (profiles, ``screenshot_roi``) are plain
blocking methods; the server runs them in a worker thread, never on the event loop.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import cv2

from engine import audio as audio_mod
from engine import capture, clock
from engine import config as cfg
from engine.fsm import FsmEvent, Status
from engine.protocol import (
    CatchEvent,
    CatchWire,
    ConfigEvent,
    ErrorEvent,
    Event,
    Levels,
    LevelsEvent,
    LogEvent,
    LogLine,
    ProfilesEvent,
    RoiEvent,
    StateEvent,
    StatsEvent,
)
from engine.session import Session, SessionError
from engine.stats import SessionStats

logger = logging.getLogger(__name__)

SessionFactory = Callable[..., Session]


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into a copy of ``base``; lists and scalars replace."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def epoch_ms(t: float) -> float:
    """A shared-clock timestamp as wall-clock epoch milliseconds, for display."""
    return (time.time() - (clock.now() - t)) * 1000.0


class Controller:
    def __init__(
        self,
        *,
        profile: cfg.Profile | None = None,
        settings: cfg.Settings | None = None,
        session_factory: SessionFactory = Session,
        persist: bool = True,
    ) -> None:
        self.settings = settings or cfg.load_settings()
        self.profile = profile or self._initial_profile()
        self.session_factory = session_factory
        self.persist = persist
        self.on_settings_changed: Callable[[cfg.Settings], None] | None = None

        self._events: queue.SimpleQueue[Event] = queue.SimpleQueue()
        self._lock = threading.RLock()
        self._session: Session | None = None
        self._thread: threading.Thread | None = None
        self._last_state = StateEvent(status=Status.STOPPED.value, state="IDLE")
        self._last_stats = SessionStats()

    # ==================================================================================
    # queries
    # ==================================================================================

    @property
    def running(self) -> bool:
        with self._lock:
            return self._session is not None

    @property
    def status(self) -> str:
        return self._last_state.status

    def drain_events(self) -> list[Event]:
        events: list[Event] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def snapshot(self) -> list[Event]:
        """Everything a freshly connected client needs to draw the full picture."""
        return [
            self._config_event(),
            self._profiles_event(),
            self._last_state,
            self.stats_event(),
        ]

    def stats_event(self) -> StatsEvent:
        with self._lock:
            session = self._session
        stats = session.stats if session is not None else self._last_stats
        return StatsEvent(stats=stats.snapshot(clock.now()))

    # ==================================================================================
    # session control
    # ==================================================================================

    def start(self, *, single_cast: bool = False) -> None:
        with self._lock:
            if self._session is not None:
                if self._session.fsm.status is Status.PAUSED:
                    self._session.request("resume")
                return
            session = self.session_factory(
                self.profile,
                dry_run=self.settings.dry_run,
                on_event=self._on_fsm_event,
                on_levels=self._on_levels,
                single_cast=single_cast,
            )
            self._session = session
            self._thread = threading.Thread(
                target=self._run_session, args=(session,), name="fishing-session", daemon=True
            )
            self._thread.start()

    def test_cast(self) -> None:
        """One cast, one outcome, then stop: the Detection screen's "Test one cast"."""
        self.start(single_cast=True)

    def stop(self, reason: str = "stopped by user") -> None:
        self._request("stop", reason)

    def pause(self, reason: str = "paused by user") -> None:
        self._request("pause", reason)

    def resume(self) -> None:
        self._request("resume")

    def toggle_pause(self) -> None:
        self._request("toggle_pause")

    def toggle_start_stop(self) -> None:
        """The start/stop hotkey: start when idle, stop when running or paused."""
        if self.running:
            self.stop()
        else:
            self.start()

    def emergency_stop(self) -> None:
        """Release every key and stop. Reaches the session within one tick (~10 ms)."""
        with self._lock:
            session = self._session
        if session is not None:
            session.request("emergency_stop")
        else:
            self._push_state(
                StateEvent(status=Status.STOPPED.value, state="IDLE", reason="emergency stop")
            )

    def apply_lure(self) -> None:
        self._request("apply_lure")

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the current session thread ends. For tests and shutdown."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # ==================================================================================
    # configuration
    # ==================================================================================

    def set_config(self, patch: dict[str, Any]) -> None:
        """Merge into the active profile; a running session picks it up at its next cast."""
        merged = deep_merge(self.profile.model_dump(mode="json"), patch)
        profile = cfg.Profile.model_validate(merged)  # raises on a bad patch
        with self._lock:
            self.profile = profile
            session = self._session
        if session is not None:
            session.request("update_profile", profile)
        self._push(self._config_event())

    def set_settings(self, patch: dict[str, Any]) -> None:
        merged = deep_merge(self.settings.model_dump(mode="json"), patch)
        self.settings = cfg.Settings.model_validate(merged)
        if self.persist:
            cfg.save_settings(self.settings)
        if self.on_settings_changed is not None:
            self.on_settings_changed(self.settings)
        self._push(self._config_event())

    def list_profiles(self) -> None:
        self._push(self._profiles_event())

    def load_profile(self, name: str) -> None:
        profile = cfg.load_profile(name)
        with self._lock:
            self.profile = profile
            session = self._session
        if session is not None:
            session.request("update_profile", profile)
        self._remember_active(profile.name)
        self._push(self._config_event())
        self._push(self._profiles_event())

    def save_profile(self, name: str | None = None) -> None:
        profile = self.profile if name is None else self.profile.model_copy(update={"name": name})
        if self.persist:
            cfg.save_profile(profile)
        with self._lock:
            self.profile = profile
        self._remember_active(profile.name)
        self._push(self._config_event())
        self._push(self._profiles_event())

    # ==================================================================================
    # screen
    # ==================================================================================

    def screenshot_roi(self) -> RoiEvent:
        """The search ROI as a PNG, for the Detection screen. Blocking: call off-loop."""
        window = capture.find_game_window(
            self.profile.capture.process_name, self.profile.capture.window_title_contains
        )
        roi = window.roi(self.profile.detection.search_roi)
        with capture.capture_session(
            roi, window.monitor, backend=self.profile.capture.backend
        ) as source:
            deadline = clock.now() + 2.0
            frame = None
            while frame is None and clock.now() < deadline:
                frame = source.grab()
        if frame is None:
            msg = "no frame arrived from the capture backend"
            raise capture.CaptureError(msg)
        ok, png = cv2.imencode(".png", frame)
        if not ok:
            msg = "could not encode the ROI screenshot"
            raise capture.CaptureError(msg)
        event = RoiEvent(
            png_base64=base64.b64encode(png.tobytes()).decode("ascii"),
            width=int(frame.shape[1]),
            height=int(frame.shape[0]),
        )
        self._push(event)
        return event

    def report_error(self, message: str, command: str | None = None) -> None:
        self._push(ErrorEvent(message=message, command=command))

    # ==================================================================================
    # internals
    # ==================================================================================

    def _initial_profile(self) -> cfg.Profile:
        name = self.settings.active_profile
        if name in cfg.list_profiles():
            try:
                return cfg.load_profile(name)
            except ValueError:
                logger.exception("profile %r is invalid; starting from defaults", name)
        return cfg.Profile(name=name)

    def _remember_active(self, name: str) -> None:
        if self.settings.active_profile == name:
            return
        self.settings = self.settings.model_copy(update={"active_profile": name})
        if self.persist:
            cfg.save_settings(self.settings)

    def _request(self, command: str, payload: object = None) -> None:
        with self._lock:
            session = self._session
        if session is not None:
            session.request(command, payload)

    def _run_session(self, session: Session) -> None:
        try:
            session.run()
        except SessionError as exc:
            self._push_state(StateEvent(status=Status.ERROR.value, state="IDLE", reason=str(exc)))
            self._log("error", str(exc))
        except Exception as exc:  # the thread must report, never vanish
            logger.exception("session crashed")
            self._push_state(
                StateEvent(status=Status.ERROR.value, state="IDLE", reason=f"crashed: {exc}")
            )
            self._log("error", f"session crashed: {exc}")
        finally:
            with self._lock:
                if self._session is session:
                    self._session = None
                self._last_stats = session.stats
            self._push(self.stats_event())

    def _on_fsm_event(self, event: FsmEvent) -> None:
        if event.kind == "state" and event.status is not None and event.state is not None:
            self._push_state(
                StateEvent(status=event.status.value, state=event.state.value, reason=event.reason)
            )
            self._push(self.stats_event())
        elif event.kind == "catch" and event.record is not None:
            self._push(
                CatchEvent(
                    record=CatchWire(
                        t=epoch_ms(event.t),
                        wait_ms=round(event.record.wait_s * 1000),
                        reaction_ms=round(event.record.reaction_s * 1000),
                    )
                )
            )
            self._push(self.stats_event())
        elif event.kind == "log":
            level = event.level if event.level in ("debug", "info", "warn", "error") else "info"
            self._push(
                LogEvent(line=LogLine(t=epoch_ms(event.t), level=level, message=event.message))
            )

    def _on_levels(self, level: audio_mod.DetectorLevel, t: float) -> None:
        self._push(
            LevelsEvent(
                levels=Levels(
                    t=epoch_ms(t),
                    audio_level=round(level.level_db, 1),
                    audio_floor=round(level.floor_db, 1),
                    audio_threshold=round(level.threshold_db, 1),
                    armed=level.armed,
                )
            )
        )

    def _log(self, level: str, message: str) -> None:
        self._push(LogEvent(line=LogLine(t=time.time() * 1000, level=level, message=message)))

    def _config_event(self) -> ConfigEvent:
        return ConfigEvent(
            profile=self.profile.model_dump(mode="json"),
            settings=self.settings.model_dump(mode="json"),
        )

    def _profiles_event(self) -> ProfilesEvent:
        names = cfg.list_profiles() if self.persist else []
        return ProfilesEvent(names=names, active=self.profile.name)

    def _push_state(self, event: StateEvent) -> None:
        self._last_state = event
        self._push(event)

    def _push(self, event: Event) -> None:
        self._events.put(event)
