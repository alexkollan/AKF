"""Native backends: the only code that talks to the operating system.

Everything above this package — the detectors, the state machine, the session, the
controller, the server and the UI — is portable. What is not portable is a short, fixed
list: finding the game window and the monitors, a fast screen grab, synthetic keys and
mouse, window focus, game audio capture and global hotkeys. :class:`Platform` names that
list; each operating system implements it once.

* ``engine.native.windows`` — the original engine: DXGI, ``SendInput``, WASAPI, a
  low-level keyboard hook.
* ``engine.native.unsupported`` — any other OS until it has a real backend. Portable
  pieces keep working (``mss`` capture, simulated sessions, the UI); anything that needs
  the game raises a clear error instead of an ``ImportError``.

The public modules (``engine.capture``, ``engine.input``, ``engine.audio``,
``engine.hotkeys``) are the facade; nothing outside them should import from here, and
backends are imported lazily so that ``import engine.capture`` never loads ``pywin32`` on a
machine that does not have it.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from engine.audio import AudioDevice, AudioSource
    from engine.capture import FrameSource, MonitorInfo, Rect, WindowInfo
    from engine.input import MouseButton


class HotkeyHook(Protocol):
    """A started-on-demand global keyboard hook for a fixed set of keys."""

    def start(self) -> None: ...

    def stop(self) -> None: ...


class Platform(Protocol):
    """Everything the engine needs from the operating system."""

    #: ``sys.platform`` value this backend serves.
    name: str
    #: False for the placeholder backend: the game cannot be driven on this OS yet.
    supported: bool

    # -- display -----------------------------------------------------------------------

    def prepare(self) -> None:
        """One-time process setup (DPI awareness on Windows). Safe to call repeatedly."""

    def list_monitors(self) -> list[MonitorInfo]: ...

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]: ...

    def describe_window(self, handle: int) -> WindowInfo: ...

    def process_path(self, pid: int) -> str:
        """Full executable path for a pid, or ``""`` when it cannot be read."""

    def open_native_capture(
        self, region: Rect, monitor: MonitorInfo, target_fps: int
    ) -> FrameSource:
        """The fast capture path; raises ``CaptureError`` when there is none."""

    # -- input -------------------------------------------------------------------------

    def check_key(self, key: str) -> None:
        """Raise ``InputError`` when this OS cannot press the (canonical) key."""

    def send_key(self, key: str, *, down: bool) -> None: ...

    def move_cursor(self, x: int, y: int) -> None: ...

    def send_button(self, button: MouseButton, *, down: bool) -> None: ...

    def cursor_position(self) -> tuple[int, int]: ...

    def has_focus(self, window: WindowInfo) -> bool: ...

    def focus_window(self, window: WindowInfo, *, settle_ms: int = 120) -> bool: ...

    # -- audio -------------------------------------------------------------------------

    def list_audio_devices(self) -> list[AudioDevice]: ...

    def open_audio_source(
        self,
        pid: int | None,
        *,
        device_index: int | None,
        chunk_ms: int,
        allow_endpoint_fallback: bool,
    ) -> AudioSource: ...

    # -- hotkeys -----------------------------------------------------------------------

    def hotkey_hook(
        self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool
    ) -> HotkeyHook:
        """A hook calling ``bindings[key]`` once per press of each (canonical) key name.

        With ``swallow`` the bound keys never reach any other application.
        """


@functools.cache
def current() -> Platform:
    """The backend for the operating system this process runs on."""
    if sys.platform == "win32":
        from engine.native.windows import WindowsPlatform

        return WindowsPlatform()
    from engine.native.unsupported import UnsupportedPlatform

    return UnsupportedPlatform(sys.platform)
