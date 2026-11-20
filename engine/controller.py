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
import json
import logging
import queue
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from engine import audio as audio_mod
from engine import capture, clock
from engine import config as cfg
from engine.bridge import BridgeState
from engine.calibration import CalibrationError, CalibrationUpdate, Calibrator
from engine.fsm import FsmEvent, Status
from engine.protocol import (
    AudioDevice,
    AudioDevicesEvent,
    BridgeEvent,
    CalibrationEvent,
    CatchEvent,
    CatchWire,
    ClientShotEvent,
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
    WindowItem,
    WindowsEvent,
)
from engine.session import Session, SessionError
from engine.stats import SessionStats

logger = logging.getLogger(__name__)

SessionFactory = Callable[..., Session]

#: The ROI editor does not need more than this; a 3440 px PNG is a slow WebSocket frame.
CLIENT_SHOT_MAX_WIDTH = 960


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into a copy of ``base``; lists and scalars replace."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


#: State reasons that are worth a notification: the bot stopped or paused by itself.
NOTIFY_PREFIXES = ("Stopped:", "WoW lost focus", "crashed", "emergency stop")
WEBHOOK_TIMEOUT_S = 5.0


def post_json(url: str, payload: dict[str, Any]) -> None:
    """POST a JSON body; failures are logged, never raised."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "fishbot"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_S):
            pass
    except OSError as exc:
        logger.warning("webhook notification failed: %s", exc)


def _png_base64(frame: np.ndarray) -> str:
    ok, png = cv2.imencode(".png", frame)
    if not ok:
        msg = "could not encode the screenshot"
        raise capture.CaptureError(msg)
    return base64.b64encode(png.tobytes()).decode("ascii")


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
        self.calibrator = Calibrator(
            profile=lambda: self.profile,
            settings=lambda: self.settings,
            report=self._on_calibration,
        )

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
        now = clock.now()
        if session is None:
            return StatsEvent(stats=self._last_stats.snapshot(now))
        remaining = session.fsm.lures.remaining(now)
        return StatsEvent(
            stats=session.stats.snapshot(now),
            lure_remaining_s=round(remaining, 1) if remaining is not None else None,
        )

    # ==================================================================================
    # session control
    # ==================================================================================

    def start(self, *, cast_limit: int | None = None) -> None:
        if self.calibrator.busy:
            # Both would press keys in the same game; never let them overlap.
            self.report_error("finish or cancel the calibration step first", "start")
            return
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
                on_bridge=self._on_bridge,
                cast_limit=cast_limit,
            )
            self._session = session
            self._thread = threading.Thread(
                target=self._run_session, args=(session,), name="fishing-session", daemon=True
            )
            self._thread.start()

    def test_cast(self, count: int = 1) -> None:
        """``count`` casts with real outcomes, then stop: "Test one cast" and the
        calibration's confirming run."""
        self.start(cast_limit=count)

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

    def import_profile(self, payload: dict[str, Any]) -> None:
        """Replace the active profile with an imported one, and keep it."""
        profile = cfg.Profile.model_validate(payload)  # raises on anything malformed
        with self._lock:
            self.profile = profile
            session = self._session
        if session is not None:
            session.request("update_profile", profile)
        self.save_profile()

    def delete_profile(self, name: str) -> None:
        """Remove a saved profile. The active one stays loaded until something replaces it."""
        if self.persist:
            cfg.delete_profile(name)
        self._push(self._profiles_event())

    def list_audio_devices(self) -> None:
        devices = audio_mod.list_loopback_devices()
        self._push(
            AudioDevicesEvent(
                devices=[
                    AudioDevice(
                        index=device.index,
                        name=device.name,
                        channels=device.channels,
                        sample_rate=device.sample_rate,
                        is_default=device.is_default,
                    )
                    for device in devices
                ]
            )
        )

    # ==================================================================================
    # calibration
    # ==================================================================================

    def list_windows(self) -> None:
        known = {name.lower() for name in capture.DEFAULT_WOW_PROCESSES}
        windows = [
            WindowItem(
                hwnd=w.hwnd,
                title=w.title,
                process=w.process_name,
                width=w.client_rect.width,
                height=w.client_rect.height,
                monitor=w.monitor.index,
                likely_game=w.process_name.lower() in known,
            )
            for w in capture.list_windows()
            if w.client_rect.width >= 320 and w.client_rect.height >= 240
        ]
        windows.sort(key=lambda item: (not item.likely_game, item.process.lower()))
        self._push(WindowsEvent(windows=windows))

    def select_window(self, hwnd: int) -> None:
        self.set_config(self.calibrator.window_patch(hwnd))
        self._on_calibration(CalibrationUpdate("window", "done", 1.0))

    def capture_bobber(self, x: float, y: float) -> None:
        patch = self.calibrator.bobber_patch(x, y)
        self.set_config(patch)
        path = patch["detection"]["bobber"]["template_paths"][0]
        self._on_calibration(
            CalibrationUpdate("bobber", "done", 1.0, result={"templatePath": path})
        )

    def calibrate_sound(self, seconds: float) -> None:
        self._refuse_while_fishing()
        self.calibrator.start_sound(seconds)

    def measure_timing(self, casts: int) -> None:
        self._refuse_while_fishing()
        self.calibrator.start_timing(casts)

    def cancel_calibration(self) -> None:
        self.calibrator.cancel()

    def finish_calibration(self) -> None:
        with self._lock:
            self.profile = cfg.touch_calibration(self.profile)
        self.save_profile()
        self._on_calibration(CalibrationUpdate("finish", "done", 1.0))

    def _refuse_while_fishing(self) -> None:
        if self.running:
            msg = "stop fishing before calibrating"
            raise CalibrationError(msg)

    def _on_calibration(self, update: CalibrationUpdate) -> None:
        self._push(
            CalibrationEvent(
                step=update.step,
                status=update.status,
                progress=round(update.progress, 3),
                message=update.message,
                result=update.result,
            )
        )

    # ==================================================================================
    # screen
    # ==================================================================================

    def screenshot_client(self) -> ClientShotEvent:
        """The WoW client area, downscaled, for the ROI editor. Blocking: call off-loop."""
        window = self._game_window()
        frame = self._grab(window.client_rect, window)
        height, width = frame.shape[:2]
        scale = min(1.0, CLIENT_SHOT_MAX_WIDTH / width)
        if scale < 1.0:
            frame = cv2.resize(
                frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
            )
        event = ClientShotEvent(
            png_base64=_png_base64(frame),
            width=int(frame.shape[1]),
            height=int(frame.shape[0]),
            client_width=width,
            client_height=height,
        )
        self._push(event)
        return event

    def screenshot_roi(self) -> RoiEvent:
        """The search ROI as a PNG, for the Detection screen. Blocking: call off-loop."""
        window = self._game_window()
        frame = self._grab(window.roi(self.profile.detection.search_roi), window)
        event = RoiEvent(
            png_base64=_png_base64(frame),
            width=int(frame.shape[1]),
            height=int(frame.shape[0]),
        )
        self._push(event)
        return event

    def _game_window(self) -> capture.WindowInfo:
        return capture.find_game_window(
            self.profile.capture.process_name, self.profile.capture.window_title_contains
        )

    def _grab(self, region: capture.Rect, window: capture.WindowInfo) -> np.ndarray:
        with capture.capture_session(
            region, window.monitor, backend=self.profile.capture.backend
        ) as source:
            deadline = clock.now() + 2.0
            frame = None
            while frame is None and clock.now() < deadline:
                frame = source.grab()
        if frame is None:
            msg = "no frame arrived from the capture backend"
            raise capture.CaptureError(msg)
        return frame

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

    def _on_bridge(self, state: BridgeState | None) -> None:
        if state is None:
            self._push(BridgeEvent(present=False))
            return
        self._push(
            BridgeEvent(
                present=True,
                free_slots=state.free_slots,
                total_slots=state.total_slots,
                in_combat=state.in_combat,
                pole_equipped=state.pole_equipped,
                lure_active=state.lure_active,
                lure_remaining_s=state.lure_remaining_s,
                channelling=state.channelling,
                channel_s=state.channel_s,
            )
        )

    def _log(self, level: str, message: str) -> None:
        self._push(LogEvent(line=LogLine(t=time.time() * 1000, level=level, message=message)))

    def _config_event(self) -> ConfigEvent:
        return ConfigEvent(
            profile=self.profile.model_dump(mode="json"),
            settings=self.settings.model_dump(mode="json"),
            defaults={
                "profile": cfg.Profile().model_dump(mode="json"),
                "settings": cfg.Settings().model_dump(mode="json"),
            },
        )

    def _profiles_event(self) -> ProfilesEvent:
        names = cfg.list_profiles() if self.persist else []
        return ProfilesEvent(names=names, active=self.profile.name)

    def _push_state(self, event: StateEvent) -> None:
        self._last_state = event
        self._push(event)
        if event.reason and event.reason.startswith(NOTIFY_PREFIXES):
            self._notify(event)

    def _notify(self, event: StateEvent) -> None:
        """Tell the user the bot stopped on its own, via the configured webhook.

        Runs on its own short-lived thread: a slow or dead webhook must never hold up the
        session thread that reported the stop.
        """
        url = self.settings.notifications.webhook_url
        if not url:
            return
        message = f"Fishbot {event.status}: {event.reason}"
        stats = self.stats_event().stats
        payload = {
            "content": message,  # Discord
            "text": message,  # Slack-style and most automation tools
            "status": event.status,
            "reason": event.reason,
            "catches": stats.catches,
            "casts": stats.casts,
        }
        threading.Thread(target=post_json, args=(url, payload), daemon=True).start()

    def _push(self, event: Event) -> None:
        self._events.put(event)
