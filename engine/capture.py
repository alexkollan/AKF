"""Window discovery, monitor geometry and fast ROI capture.

Three things have to be right before anything else works:

* every coordinate is in the operating system's own global screen space: **physical
  pixels** on Windows (the process is per-monitor DPI aware), **points** on macOS (the
  Quartz display space that window bounds, events and capture all use). Either way two
  monitors with different scaling do not shift the ROI;
* geometry is anchored to the WoW **client area**, not the window frame, so the title bar
  and borders never offset a right-click;
* capture happens on a worker thread, never on the asyncio loop.

Frames come back as ``numpy`` arrays shaped ``(height, width, 3)`` in **BGR** order, which
is what OpenCV expects, regardless of which backend produced them. A frame is always at the
display's native resolution: ``MonitorInfo.scale`` pixels per coordinate unit, so a region
of 400x300 points on a Retina display arrives as an 800x600 frame (on Windows the scale is
always 1).

This module is portable. Window and monitor discovery and the fast capture path come from
the operating system's backend (``engine.native``); ``mss`` is the fallback everywhere.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from engine import native
from engine.config import CaptureBackend, RoiFraction

logger = logging.getLogger(__name__)

# WoW Forever runs on the Mainline (retail) client as Wow.exe on Windows and as the
# executable "World of Warcraft" (inside _forever_/World of Warcraft.app) on macOS, both
# confirmed. The rest are fallbacks for other Blizzard clients; the active profile
# overrides this list.
DEFAULT_WOW_PROCESSES: tuple[str, ...] = (
    "Wow.exe",
    "WowT.exe",
    "WowB.exe",
    "WowClassic.exe",
    "World of Warcraft.exe",
    "World of Warcraft",
)


class CaptureError(RuntimeError):
    """Capture could not be set up or produced no frames."""


class WindowNotFoundError(CaptureError):
    """No window matched the configured process name."""


def ensure_dpi_awareness() -> None:
    """Prepare the process for physical-pixel geometry (per-monitor DPI awareness on
    Windows). Safe to call repeatedly; must run before the first window or monitor query."""
    native.current().prepare()


# --------------------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle in screen coordinates (pixels on Windows, points on macOS).

    ``right`` and ``bottom`` are exclusive.
    """

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def translated(self, dx: int, dy: int) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def clamped_to(self, bounds: Rect) -> Rect:
        """Clip to ``bounds``; raises when the two do not overlap."""
        left = max(self.left, bounds.left)
        top = max(self.top, bounds.top)
        right = min(self.right, bounds.right)
        bottom = min(self.bottom, bounds.bottom)
        if right <= left or bottom <= top:
            msg = f"{self} does not overlap {bounds}"
            raise CaptureError(msg)
        return Rect(left, top, right, bottom)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    """One display, in virtual-screen coordinates (the second monitor may be negative)."""

    index: int
    device: str  # e.g. "\\\\.\\DISPLAY9" on Windows
    handle: int  # the OS's own monitor id (HMONITOR on Windows)
    rect: Rect
    work_rect: Rect
    is_primary: bool
    #: Captured pixels per coordinate unit: 2.0 on a Retina display, always 1.0 on Windows.
    scale: float = 1.0

    def to_pixels(self, length: int) -> int:
        """A length in screen coordinates as captured pixels on this monitor."""
        return round(length * self.scale)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A top-level window plus the geometry the bot actually aims at."""

    hwnd: int  # the OS's own window id (HWND on Windows)
    pid: int
    process_name: str
    title: str
    window_rect: Rect
    client_rect: Rect  # client area in screen coordinates
    monitor: MonitorInfo

    def roi(self, fraction: RoiFraction) -> Rect:
        """Convert a profile's relative ROI into screen coordinates."""
        return roi_to_screen(self.client_rect, fraction)

    def client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        return (self.client_rect.left + x, self.client_rect.top + y)

    def screen_to_client(self, x: int, y: int) -> tuple[int, int]:
        return (x - self.client_rect.left, y - self.client_rect.top)


def roi_to_screen(client_rect: Rect, fraction: RoiFraction) -> Rect:
    """Map fractions of a client area to a screen-space rectangle, clamped to the client."""
    width, height = client_rect.width, client_rect.height
    left = client_rect.left + round(fraction.x * width)
    top = client_rect.top + round(fraction.y * height)
    right = left + max(1, round(fraction.w * width))
    bottom = top + max(1, round(fraction.h * height))
    return Rect(left, top, right, bottom).clamped_to(client_rect)


# --------------------------------------------------------------------------------------
# discovery (native backend)
# --------------------------------------------------------------------------------------


def list_monitors() -> list[MonitorInfo]:
    return native.current().list_monitors()


def process_path(pid: int) -> str:
    """Full executable path for a pid, or "" when the process cannot be opened."""
    return native.current().process_path(pid)


def describe_window(hwnd: int) -> WindowInfo:
    """Collect everything the engine needs about one window."""
    return native.current().describe_window(hwnd)


def list_windows(*, visible_only: bool = True) -> list[WindowInfo]:
    """Every top-level window with a title, newest Z-order first.

    Used by the spike and the calibration wizard to let the user pick the right client when
    the process name is unknown.
    """
    return native.current().list_windows(visible_only=visible_only)


def find_game_window(
    process_name: str | None = None,
    title_contains: str = "",
    *,
    candidates: Sequence[str] = DEFAULT_WOW_PROCESSES,
) -> WindowInfo:
    """Locate the WoW client window.

    ``process_name`` wins when given; otherwise every name in ``candidates`` is tried. A
    ``process_name`` that is itself one of the known clients only *prefers* that name: when
    no window has it, the other known clients are tried, so a profile saying ``Wow.exe``
    still finds ``World of Warcraft`` on a Mac. When several windows match, the one with the
    largest client area is returned, which skips launcher and tooltip windows.
    """
    wanted = [process_name] if process_name else list(candidates)
    needle = title_contains.lower()
    windows = [
        window
        for window in list_windows()
        if (not needle or needle in window.title.lower())
        and window.client_rect.width > 0
        and window.client_rect.height > 0
    ]

    def matching(names: Sequence[str]) -> list[WindowInfo]:
        lowered = {name.lower() for name in names if name}
        return [window for window in windows if window.process_name.lower() in lowered]

    matches = matching(wanted)
    known = {name.lower() for name in candidates}
    if not matches and process_name and process_name.lower() in known:
        matches = matching(candidates)
    if not matches:
        msg = (
            f"no window found for process {wanted!r}"
            + (f" with title containing {title_contains!r}" if title_contains else "")
            + " — is the game running? Run `uv run python -m tools.spike --list` to see "
            "what is open."
        )
        raise WindowNotFoundError(msg)

    return max(matches, key=lambda w: w.client_rect.width * w.client_rect.height)


# --------------------------------------------------------------------------------------
# frame sources
# --------------------------------------------------------------------------------------


class FrameSource(Protocol):
    """A started capture over a fixed region. Frames are BGR ``uint8`` arrays."""

    backend_name: str
    region: Rect

    def grab(self) -> np.ndarray | None:
        """Latest frame, or ``None`` if none arrived within the backend's wait."""

    def close(self) -> None: ...


class MssSource:
    """Portable fallback. Slower, but works where the fast path is unavailable."""

    backend_name = "mss"

    def __init__(self, region: Rect, _monitor: MonitorInfo, _target_fps: int) -> None:
        import mss

        # The backend sets mss up for native-resolution frames (macOS asks it to drop its
        # nominal 1x scaling); idempotent, and the bridge builds this class directly.
        ensure_dpi_awareness()
        self.region = region
        # mss is not thread-safe; this instance belongs to the thread that built it.
        self._sct = mss.mss()
        self._box = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }

    def grab(self) -> np.ndarray | None:
        raw = self._sct.grab(self._box)
        # mss hands back BGRA; drop alpha without copying the whole buffer twice.
        return np.asarray(raw, dtype=np.uint8)[:, :, :3]

    def close(self) -> None:
        self._sct.close()


def open_capture(
    region: Rect,
    monitor: MonitorInfo,
    *,
    backend: CaptureBackend = CaptureBackend.AUTO,
    target_fps: int = 60,
) -> FrameSource:
    """Start capturing ``region``, preferring the OS's fast path (dxcam on Windows) and
    degrading to mss with a warning."""
    ensure_dpi_awareness()
    if region.width <= 0 or region.height <= 0:
        msg = f"empty capture region {region}"
        raise CaptureError(msg)

    if backend is CaptureBackend.MSS:
        return MssSource(region, monitor, target_fps)

    try:
        return native.current().open_native_capture(region, monitor, target_fps)
    except Exception as exc:  # any failure of the fast path is a fallback case
        if backend is CaptureBackend.DXCAM:
            msg = f"dxcam capture failed: {exc}"
            raise CaptureError(msg) from exc
        logger.warning("fast capture unavailable (%s); falling back to mss", exc)
        return MssSource(region, monitor, target_fps)


@contextmanager
def capture_session(
    region: Rect,
    monitor: MonitorInfo,
    *,
    backend: CaptureBackend = CaptureBackend.AUTO,
    target_fps: int = 60,
) -> Iterator[FrameSource]:
    source = open_capture(region, monitor, backend=backend, target_fps=target_fps)
    try:
        yield source
    finally:
        source.close()


def measure_fps(source: FrameSource, duration_s: float = 2.0) -> float:
    """Frames per second actually delivered, for the spike and the calibration report."""
    deadline = time.perf_counter() + duration_s
    frames = 0
    started = time.perf_counter()
    while time.perf_counter() < deadline:
        if source.grab() is not None:
            frames += 1
    elapsed = time.perf_counter() - started
    return frames / elapsed if elapsed > 0 else 0.0
