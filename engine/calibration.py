"""The calibration wizard's engine side: measure a new fishing spot, write it into the profile.

Six steps (docs/PLAN.md, "Calibration wizard"); the UI walks through them in order:

1. **Window** — pick the WoW window; the profile records how to find it again and what the
   client looked like (size, monitor), so a later change can ask for a new calibration.
2. **Search area** — the UI's ROI editor; nothing to measure here.
3. **Bobber** — click the bobber on a snapshot: its picture is saved as a template and its
   colour becomes the feather colour range.
4. **Sound** — twenty seconds of the spot's ambience with nobody casting. The loudest
   moments set how far above the background a splash has to rise.
5. **Timing** — the bot casts a few times and times the landing splash; with FishBridge
   it also reads how long a fishing channel lasts.
6. **Confirm** — five real casts (the controller's test run), then finish.

Jobs that listen or press keys run one at a time on a worker thread and report progress as
``calibration`` events. The analyses are plain functions over numbers, tested without the
game.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from engine import audio as audio_mod
from engine import capture, clock
from engine import config as cfg
from engine.bridge import BridgeReader
from engine.input import InputController, focus_window, has_focus

logger = logging.getLogger(__name__)

SOUND_SECONDS = 20.0
#: Sensitivity the suggestion never goes below or above, in dB over the floor. The live
#: session saw splashes start at +11 dB and peak at +27 to +38 dB.
MIN_OVER_DB = 9.0
MAX_OVER_DB = 16.0
AMBIENT_MARGIN_DB = 4.0
AMBIENT_PERCENTILE = 99.5
#: Below this the "background" is digital silence: WoW played nothing.
SILENT_DBFS = -90.0

LANDING_WINDOW_S = 5.0
LANDING_IGNORE_S = 0.3  # the cast's own whoosh right after the key
LANDING_MARGIN_S = 0.6
BETWEEN_CASTS_S = 2.5


class CalibrationError(RuntimeError):
    """A calibration step cannot run (no game window, a session is running, ...)."""


# --------------------------------------------------------------------------------------
# analyses (pure)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AmbientReport:
    floor_db: float
    #: How far the loudest ambient moments rise above the floor.
    peak_over_db: float
    suggested_over_db: float

    @property
    def suggested_ratio(self) -> float:
        return round(10 ** (self.suggested_over_db / 20), 2)


def analyse_ambient(levels_db: Sequence[float], floor_min_dbfs: float = -60.0) -> AmbientReport:
    """From a quiet stretch of in-band levels, how sensitive the detector can safely be.

    The floor is the median, clamped at ``floor_min_dbfs`` exactly as the detector clamps
    it — otherwise the suggestion would be relative to a floor the detector never uses. The
    threshold has to clear the ambience's loudest moments (a high percentile, so one odd
    click does not decide it) by a margin, and stays within the range where real splashes
    were seen to break it.

    A median at digital silence means WoW played nothing at all, which is a setup problem
    (sound off, or WoW not in front with "Sound in Background" off), not a quiet spot.
    """
    if not levels_db:
        msg = "no sound was captured; is WoW's sound on?"
        raise CalibrationError(msg)
    levels = np.asarray(levels_db, dtype=float)
    median = float(np.median(levels))
    if median < SILENT_DBFS:
        msg = (
            "WoW was silent the whole time. Turn its sound on, and keep WoW in front "
            "(or enable Sound in Background in its sound options), then listen again."
        )
        raise CalibrationError(msg)
    floor = max(median, floor_min_dbfs)
    peak_over = max(0.0, float(np.percentile(levels, AMBIENT_PERCENTILE)) - floor)
    suggested = min(MAX_OVER_DB, max(MIN_OVER_DB, peak_over + AMBIENT_MARGIN_DB))
    return AmbientReport(
        floor_db=round(floor, 1),
        peak_over_db=round(peak_over, 1),
        suggested_over_db=round(suggested * 2) / 2,
    )


def suggest_land_delay_ms(landings_s: Sequence[float]) -> int | None:
    """Wait past the slowest landing seen, plus a margin, rounded up to 100 ms."""
    if not landings_s:
        return None
    return int(math.ceil((max(landings_s) + LANDING_MARGIN_S) * 10) * 100)


def hsv_range_from_patch(patch_bgr: np.ndarray) -> cfg.HsvRange:
    """The feather's colour range from a small BGR patch under the click."""
    mean = patch_bgr.reshape(-1, 3).mean(axis=0).astype(np.uint8).reshape(1, 1, 3)
    hue, saturation, value = (int(v) for v in cv2.cvtColor(mean, cv2.COLOR_BGR2HSV)[0, 0])
    return cfg.HsvRange(
        h_min=max(0, hue - 8),
        h_max=min(179, hue + 8),
        s_min=max(40, saturation - 70),
        s_max=255,
        v_min=max(40, value - 70),
        v_max=255,
    )


def template_path(profile_name: str) -> Path:
    return (
        cfg.config_dir()
        / "templates"
        / cfg.profile_filename(profile_name).replace(".json", "-bobber.png")
    )


# --------------------------------------------------------------------------------------
# the runner
# --------------------------------------------------------------------------------------


@dataclass
class CalibrationUpdate:
    """One progress report; the controller turns it into a wire event."""

    step: str
    status: str  # "running" | "done" | "error"
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)


class Calibrator:
    """Runs the steps that listen or press keys, one at a time, off the event loop."""

    def __init__(
        self,
        *,
        profile: Callable[[], cfg.Profile],
        settings: Callable[[], cfg.Settings],
        report: Callable[[CalibrationUpdate], None],
    ) -> None:
        self._profile = profile
        self._settings = settings
        self._report = report
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.busy

    # -- quick steps (called off-loop by the server, return at once) -------------------

    def window_patch(self, hwnd: int) -> dict[str, Any]:
        """Profile changes that make the engine find this window again, and remember it."""
        window = capture.describe_window(hwnd)
        if not window.process_name:
            msg = "that window has closed"
            raise CalibrationError(msg)
        others = [
            w
            for w in capture.list_windows()
            if w.process_name.lower() == window.process_name.lower() and w.hwnd != hwnd
        ]
        return {
            "capture": {
                "process_name": window.process_name,
                # Only pin the title when several windows of the same program are open.
                "window_title_contains": window.title if others else "",
            },
            "fingerprint": {
                "client_width": window.client_rect.width,
                "client_height": window.client_rect.height,
                "monitor_name": window.monitor.device,
            },
        }

    def bobber_patch(self, x: float, y: float) -> dict[str, Any]:
        """Save the bobber under (x, y) — fractions of the client — as template and colour."""
        profile = self._profile()
        window = capture.find_game_window(
            profile.capture.process_name, profile.capture.window_title_contains
        )
        with capture.capture_session(
            window.client_rect, window.monitor, backend=cfg.CaptureBackend.MSS
        ) as source:
            frame = source.grab()
        if frame is None:
            msg = "could not take a picture of the game window"
            raise CalibrationError(msg)

        height, width = frame.shape[:2]
        cx, cy = round(x * (width - 1)), round(y * (height - 1))
        half = profile.detection.pixel.bobber_roi_px // 2
        crop = frame[max(0, cy - half) : cy + half, max(0, cx - half) : cx + half]
        patch = frame[max(0, cy - 1) : cy + 2, max(0, cx - 1) : cx + 2]

        path = template_path(profile.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), crop):
            msg = f"could not save {path}"
            raise CalibrationError(msg)
        colour = hsv_range_from_patch(patch)
        return {
            "detection": {
                "bobber": {
                    "template_paths": [str(path)],
                    "hsv_ranges": [colour.model_dump()],
                }
            }
        }

    # -- jobs ------------------------------------------------------------------------------

    def start_sound(self, seconds: float = SOUND_SECONDS) -> None:
        self._start("sound", lambda: self._sound(seconds))

    def start_timing(self, casts: int = 3) -> None:
        self._start("timing", lambda: self._timing(casts))

    def _start(self, step: str, job: Callable[[], dict[str, Any]]) -> None:
        if self.busy:
            msg = "another calibration step is still running"
            raise CalibrationError(msg)
        self._cancel.clear()

        def run() -> None:
            try:
                result = job()
            except Exception as exc:  # reported to the UI, never swallowed silently
                logger.exception("calibration step %s failed", step)
                self._report(CalibrationUpdate(step, "error", message=str(exc)))
                return
            if self._cancel.is_set():
                self._report(CalibrationUpdate(step, "error", message="cancelled"))
            else:
                self._report(CalibrationUpdate(step, "done", 1.0, result=result))

        self._thread = threading.Thread(target=run, name=f"calibration-{step}", daemon=True)
        self._thread.start()

    def _open_audio(self) -> tuple[capture.WindowInfo, audio_mod.AudioSource]:
        profile = self._profile()
        window = capture.find_game_window(
            profile.capture.process_name, profile.capture.window_title_contains
        )
        audio = profile.detection.audio
        source = audio_mod.open_audio_source(
            window.pid if audio.process_loopback else None, device_index=audio.device_index
        )
        return window, source

    def _sound(self, seconds: float) -> dict[str, Any]:
        _window, source = self._open_audio()
        detector = audio_mod.SplashDetector(self._profile().detection.audio, source.sample_rate)
        levels: list[float] = []
        started = clock.now()
        last_report = 0.0
        with source:
            while not self._cancel.is_set():
                elapsed = clock.now() - started
                if elapsed >= seconds:
                    break
                chunk = source.read(timeout=0.2)
                if chunk is None:
                    continue
                before = detector.last
                detector.feed(chunk)
                if detector.last is not None and detector.last is not before:
                    levels.append(detector.last.level_db)
                if elapsed - last_report >= 0.5:
                    last_report = elapsed
                    self._report(
                        CalibrationUpdate(
                            "sound",
                            "running",
                            elapsed / seconds,
                            f"listening: {detector.last.level_db:.0f} dB now"
                            if detector.last
                            else "listening",
                        )
                    )
        report = analyse_ambient(levels, self._profile().detection.audio.floor_min_dbfs)
        return {
            "floorDb": report.floor_db,
            "peakOverDb": report.peak_over_db,
            "suggestedOverDb": report.suggested_over_db,
            "suggestedRatio": report.suggested_ratio,
        }

    def _timing(self, casts: int) -> dict[str, Any]:
        profile = self._profile()
        if profile.mode is cfg.Mode.RIGHT_CLICK:
            msg = "timing is measured in mode A or C; switch mode first"
            raise CalibrationError(msg)
        dry_run = self._settings().dry_run
        window, source = self._open_audio()
        controller = InputController(dry_run=dry_run)
        bridge = BridgeReader(window)
        detector = audio_mod.SplashDetector(profile.detection.audio, source.sample_rate)
        cast_key = (
            profile.keys.fish_key
            if profile.mode is cfg.Mode.SINGLE_BUTTON
            else profile.keys.cast_key
        )
        end_key = (
            profile.keys.fish_key
            if profile.mode is cfg.Mode.SINGLE_BUTTON
            else profile.keys.interact_key
        )
        landings: list[float] = []
        channel_s: int | None = None

        with source:
            if not dry_run and not focus_window(window):
                msg = "could not bring WoW to the front"
                raise CalibrationError(msg)
            try:
                for index in range(casts):
                    if self._cancel.is_set():
                        break
                    if not dry_run and not has_focus(window):
                        msg = "WoW lost focus; click into the game and start the step again"
                        raise CalibrationError(msg)
                    self._report(
                        CalibrationUpdate(
                            "timing", "running", index / casts, f"cast {index + 1} of {casts}"
                        )
                    )
                    landing, seen_channel = self._one_cast(
                        controller, cast_key, source, detector, bridge
                    )
                    if landing is not None:
                        landings.append(landing)
                    if seen_channel:
                        channel_s = seen_channel
                    controller.tap(end_key)  # reel in the empty line
                    self._sleep(BETWEEN_CASTS_S)
            finally:
                controller.release_all()

        return {
            "landingsS": [round(value, 2) for value in landings],
            "suggestedLandDelayMs": suggest_land_delay_ms(landings),
            "channelS": channel_s,
            "suggestedMaxWaitMs": channel_s * 1000 if channel_s else None,
            "dryRun": dry_run,
        }

    def _one_cast(
        self,
        controller: InputController,
        key: str,
        source: audio_mod.AudioSource,
        detector: audio_mod.SplashDetector,
        bridge: BridgeReader,
    ) -> tuple[float | None, int | None]:
        """Cast and time the first splash after the cast's own whoosh."""
        for _ in source.drain():  # forget what played before the key
            pass
        detector.disarm()
        pressed = clock.now()
        controller.tap(key)
        landing: float | None = None
        channel: int | None = None
        while clock.now() - pressed < LANDING_WINDOW_S and not self._cancel.is_set():
            state = bridge.poll(clock.now())
            if state is not None and state.channelling and state.channel_s:
                channel = state.channel_s
            if not detector.armed and clock.now() - pressed >= LANDING_IGNORE_S:
                detector.arm()
            chunk = source.read(timeout=0.05)
            if chunk is None:
                continue
            event = detector.feed(chunk)
            if event is not None and landing is None:
                landing = source.start_time + event.t - pressed
                detector.disarm()
        return landing, channel

    def _sleep(self, seconds: float) -> None:
        self._cancel.wait(seconds)
