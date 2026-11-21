"""The backend for an operating system that does not have one yet (macOS, until it does).

What is portable keeps working: monitors come from ``mss``, screen capture falls back to
``mss``, simulated sessions, dry runs, the server and the UI all run. Anything that needs the
game — finding its window, its audio, pressing keys in it — raises the same error type the
Windows backend would raise for a missing device, with a message that says why, so the UI
reports it instead of the engine crashing on an ``ImportError``. Global hotkeys log a warning
and do nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import NoReturn

from engine.audio import AudioDevice, AudioError, AudioSource
from engine.capture import CaptureError, FrameSource, MonitorInfo, Rect, WindowInfo
from engine.input import InputError, MouseButton
from engine.native import Permission

logger = logging.getLogger(__name__)


class _NoHotkeys:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class UnsupportedPlatform:
    supported = False

    def __init__(self, name: str) -> None:
        self.name = name

    def _missing(self, what: str) -> str:
        return f"{what} is not implemented on {self.name} yet (see docs/MACOS_PORT.md)"

    def _input(self, what: str) -> NoReturn:
        raise InputError(self._missing(what))

    # -- display -----------------------------------------------------------------------

    def prepare(self) -> None:
        return None

    def permissions(self, *, request: bool = False) -> list[Permission]:
        return []

    def list_monitors(self) -> list[MonitorInfo]:
        """Monitors as ``mss`` sees them. Index 0 of ``mss.monitors`` is the union; skip it."""
        try:
            import mss

            with mss.mss() as sct:
                boxes = sct.monitors[1:]
        except Exception as exc:  # degrade: no monitors is a valid answer for the UI
            logger.warning("could not list monitors with mss: %s", exc)
            return []
        monitors: list[MonitorInfo] = []
        for index, box in enumerate(boxes):
            rect = Rect(
                box["left"], box["top"], box["left"] + box["width"], box["top"] + box["height"]
            )
            monitors.append(
                MonitorInfo(
                    index=index,
                    device=f"mss:{index + 1}",
                    handle=index + 1,
                    rect=rect,
                    work_rect=rect,
                    is_primary=index == 0,
                )
            )
        return monitors

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        raise CaptureError(self._missing("window discovery"))

    def describe_window(self, handle: int) -> WindowInfo:
        raise CaptureError(self._missing("window discovery"))

    def process_path(self, pid: int) -> str:
        return ""

    def open_native_capture(
        self, region: Rect, monitor: MonitorInfo, target_fps: int
    ) -> FrameSource:
        raise CaptureError(self._missing("fast capture"))

    # -- input -------------------------------------------------------------------------

    def check_key(self, key: str) -> None:
        return None

    def send_key(self, key: str, *, down: bool) -> None:
        self._input("keyboard input")

    def move_cursor(self, x: int, y: int) -> None:
        self._input("mouse input")

    def send_button(self, button: MouseButton, *, down: bool) -> None:
        self._input("mouse input")

    def cursor_position(self) -> tuple[int, int]:
        self._input("reading the cursor")

    def has_focus(self, window: WindowInfo) -> bool:
        self._input("window focus")

    def focus_window(self, window: WindowInfo, *, settle_ms: int = 120) -> bool:
        self._input("window focus")

    # -- audio -------------------------------------------------------------------------

    def list_audio_devices(self) -> list[AudioDevice]:
        raise AudioError(self._missing("game audio capture"))

    def open_audio_source(
        self,
        pid: int | None,
        *,
        device_index: int | None,
        chunk_ms: int,
        allow_endpoint_fallback: bool,
    ) -> AudioSource:
        raise AudioError(self._missing("game audio capture"))

    # -- hotkeys -----------------------------------------------------------------------

    def hotkey_hook(
        self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool
    ) -> _NoHotkeys:
        logger.warning("%s; use the UI's buttons instead", self._missing("global hotkeys"))
        return _NoHotkeys()
