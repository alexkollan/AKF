"""The macOS backend: Quartz windows and events, ScreenCaptureKit, a Quartz event tap.

Geometry is in points of the Quartz global display space and frames are at native pixel
resolution (``MonitorInfo.scale`` pixels per point); see ``engine.native.macos.display``.
Submodules import ``pyobjc`` at the top, so nothing outside this package imports them;
``engine.native.current()`` loads this package only on macOS.

The process running the engine needs Screen Recording (audio, titles, capture),
Accessibility (input, focus, swallowing hotkeys) and Input Monitoring (hotkeys);
``uv run python -m tools.permissions`` checks them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from engine.audio import AudioDevice, AudioError, AudioSource
from engine.capture import FrameSource, MonitorInfo, Rect, WindowInfo
from engine.input import MouseButton
from engine.native import Permission
from engine.native.macos import display, keyboard_mouse
from engine.native.macos import permissions as mac_permissions
from engine.native.macos.hotkeys import EventTapHook

logger = logging.getLogger(__name__)

#: The one "device" the whole-system fallback offers: ScreenCaptureKit hears every app.
ALL_APPS_DEVICE = AudioDevice(
    index=0,
    name="Every app (ScreenCaptureKit)",
    channels=2,
    sample_rate=48_000,
    is_default=True,
)


class MacPlatform:
    name = "darwin"
    supported = True

    # -- display -----------------------------------------------------------------------

    def prepare(self) -> None:
        display.prepare()

    def permissions(self, *, request: bool = False) -> list[Permission]:
        return mac_permissions.permissions(request=request)

    def list_monitors(self) -> list[MonitorInfo]:
        return display.list_monitors()

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        return display.list_windows(visible_only=visible_only)

    def describe_window(self, handle: int) -> WindowInfo:
        return display.describe_window(handle)

    def process_path(self, pid: int) -> str:
        return display.process_path(pid)

    def open_native_capture(
        self, region: Rect, monitor: MonitorInfo, target_fps: int
    ) -> FrameSource:
        from engine.native.macos.screencapturekit import ScreenCaptureFrames

        return ScreenCaptureFrames(region, monitor, target_fps)

    # -- input -------------------------------------------------------------------------

    def check_key(self, key: str) -> None:
        keyboard_mouse.keycode(key)

    def send_key(self, key: str, *, down: bool) -> None:
        keyboard_mouse.send_key(key, down=down)

    def move_cursor(self, x: int, y: int) -> None:
        keyboard_mouse.move_cursor(x, y)

    def send_button(self, button: MouseButton, *, down: bool) -> None:
        keyboard_mouse.send_button(button, down=down)

    def cursor_position(self) -> tuple[int, int]:
        return keyboard_mouse.cursor_position()

    def has_focus(self, window: WindowInfo) -> bool:
        return keyboard_mouse.has_focus(window)

    def focus_window(self, window: WindowInfo, *, settle_ms: int = 120) -> bool:
        return keyboard_mouse.focus_window(window, settle_ms=settle_ms)

    # -- audio -------------------------------------------------------------------------

    def list_audio_devices(self) -> list[AudioDevice]:
        """macOS has no loopback devices; the fallback is ScreenCaptureKit over every app."""
        return [ALL_APPS_DEVICE]

    def open_audio_source(
        self,
        pid: int | None,
        *,
        device_index: int | None,
        chunk_ms: int,
        allow_endpoint_fallback: bool,
    ) -> AudioSource:
        """WoW's audio alone when there is a pid, every app's otherwise.

        ScreenCaptureKit picks its own buffer size, so ``chunk_ms`` does not apply (the
        detector re-cuts chunks into its own windows), and there is only one fallback
        "device". Whether the per-app capture works is only known once it starts (it needs
        Screen Recording), so a failure surfaces from ``start()``.
        """
        from engine.native.macos.screencapturekit import ScreenCaptureAudioStream

        if device_index not in (None, ALL_APPS_DEVICE.index):
            logger.warning("audio device %s does not exist on macOS; ignoring it", device_index)
        if pid is None and not allow_endpoint_fallback:
            msg = "no game process to capture, and hearing every app is not allowed"
            raise AudioError(msg)
        return ScreenCaptureAudioStream(pid)

    # -- hotkeys -----------------------------------------------------------------------

    def hotkey_hook(
        self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool
    ) -> EventTapHook:
        return EventTapHook(bindings, swallow=swallow)
