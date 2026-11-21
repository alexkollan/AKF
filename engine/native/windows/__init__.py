"""The Windows backend: DXGI capture, ``SendInput``, WASAPI audio, a low-level keyboard hook.

This is the engine as it was verified in game. Submodules import ``pywin32``, ``dxcam``,
``comtypes`` and ``PyAudioWPatch`` at the top, so nothing outside this package imports them;
``engine.native.current()`` loads this package only on Windows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from engine.audio import AudioDevice, AudioSource
from engine.capture import FrameSource, MonitorInfo, Rect, WindowInfo
from engine.input import MouseButton
from engine.native import Permission
from engine.native.windows import display, keyboard_mouse, wasapi
from engine.native.windows.hotkeys import LowLevelHook


class WindowsPlatform:
    name = "win32"
    supported = True

    # -- display -----------------------------------------------------------------------

    def prepare(self) -> None:
        display.ensure_dpi_awareness()

    def permissions(self, *, request: bool = False) -> list[Permission]:
        return []

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
        return display.DxcamSource(region, monitor, target_fps)

    # -- input -------------------------------------------------------------------------

    def check_key(self, key: str) -> None:
        keyboard_mouse.scancode(key)

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
        return wasapi.list_loopback_devices()

    def open_audio_source(
        self,
        pid: int | None,
        *,
        device_index: int | None,
        chunk_ms: int,
        allow_endpoint_fallback: bool,
    ) -> AudioSource:
        return wasapi.open_audio_source(
            pid,
            device_index=device_index,
            chunk_ms=chunk_ms,
            allow_endpoint_fallback=allow_endpoint_fallback,
        )

    # -- hotkeys -----------------------------------------------------------------------

    def hotkey_hook(
        self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool
    ) -> LowLevelHook:
        return LowLevelHook(bindings, swallow=swallow)
