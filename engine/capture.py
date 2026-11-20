"""Window discovery, monitor geometry and fast ROI capture.

Three things have to be right before anything else works:

* the process is **per-monitor DPI aware**, so every coordinate below is a physical pixel
  and two monitors with different scaling do not shift the ROI;
* geometry is anchored to the WoW **client area**, not the window frame, so the title bar
  and borders never offset a right-click;
* capture happens on a worker thread, never on the asyncio loop.

Frames come back as ``numpy`` arrays shaped ``(height, width, 3)`` in **BGR** order, which
is what OpenCV expects, regardless of which backend produced them.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import win32api
import win32con
import win32gui
import win32process

from engine.config import CaptureBackend, RoiFraction

logger = logging.getLogger(__name__)

# WoW has shipped under several executable names; Forever uses a 1.60.1 client, so the
# Classic name is the likely one. The active profile overrides this.
DEFAULT_WOW_PROCESSES: tuple[str, ...] = (
    "WowClassic.exe",
    "Wow.exe",
    "WowT.exe",
    "WowB.exe",
    "World of Warcraft.exe",
)


class CaptureError(RuntimeError):
    """Capture could not be set up or produced no frames."""


class WindowNotFoundError(CaptureError):
    """No window matched the configured process name."""


# --------------------------------------------------------------------------------------
# DPI awareness
# --------------------------------------------------------------------------------------

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_dpi_lock = threading.Lock()
_dpi_ready = False


def ensure_dpi_awareness() -> bool:
    """Make this process per-monitor DPI aware. Safe to call repeatedly.

    Must run before the first window or monitor query, otherwise Windows reports virtualised
    coordinates and every ROI is wrong on a scaled monitor. Returns whether awareness is in
    effect (it is already set when the host app set it first, which Windows reports as an
    error — that case counts as success).
    """
    global _dpi_ready
    with _dpi_lock:
        if _dpi_ready:
            return True
        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
                _dpi_ready = True
                return True
            # ERROR_ACCESS_DENIED (5) means it was already set, which is what we want.
            if ctypes.get_last_error() == 5 or ctypes.GetLastError() == 5:
                _dpi_ready = True
                return True
        try:  # Windows 8.1 fallback: 2 == PROCESS_PER_MONITOR_DPI_AWARE
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            _dpi_ready = True
            return True
        except OSError:
            logger.warning("could not set per-monitor DPI awareness; ROIs may be offset")
            return False


# --------------------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle in physical pixels. ``right`` and ``bottom`` are exclusive."""

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
    device: str  # e.g. "\\\\.\\DISPLAY9"
    handle: int  # HMONITOR
    rect: Rect
    work_rect: Rect
    is_primary: bool


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A top-level window plus the geometry the bot actually aims at."""

    hwnd: int
    pid: int
    process_name: str
    title: str
    window_rect: Rect
    client_rect: Rect  # client area in screen coordinates
    monitor: MonitorInfo

    def roi(self, fraction: RoiFraction) -> Rect:
        """Convert a profile's relative ROI into screen-space physical pixels."""
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


def list_monitors() -> list[MonitorInfo]:
    ensure_dpi_awareness()
    monitors: list[MonitorInfo] = []
    for index, (handle, _dc, _rect) in enumerate(win32api.EnumDisplayMonitors()):
        info = win32api.GetMonitorInfo(handle)
        monitors.append(
            MonitorInfo(
                index=index,
                device=info["Device"],
                handle=int(handle),
                rect=Rect(*info["Monitor"]),
                work_rect=Rect(*info["Work"]),
                is_primary=bool(info["Flags"] & win32con.MONITORINFOF_PRIMARY),
            )
        )
    return monitors


def _monitor_for_window(hwnd: int, monitors: Sequence[MonitorInfo]) -> MonitorInfo:
    handle = int(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
    for monitor in monitors:
        if monitor.handle == handle:
            return monitor
    return monitors[0]


def _process_name(pid: int) -> str:
    """Executable name for a pid, or "" when the process cannot be opened."""
    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_uint32(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


def describe_window(hwnd: int, monitors: Sequence[MonitorInfo] | None = None) -> WindowInfo:
    """Collect everything the engine needs about one window."""
    ensure_dpi_awareness()
    monitors = monitors if monitors is not None else list_monitors()
    _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)

    window_rect = Rect(*win32gui.GetWindowRect(hwnd))
    # GetClientRect is client-relative; its origin in screen space comes from ClientToScreen.
    _, _, client_width, client_height = win32gui.GetClientRect(hwnd)
    origin_x, origin_y = win32gui.ClientToScreen(hwnd, (0, 0))

    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        process_name=_process_name(pid),
        title=win32gui.GetWindowText(hwnd),
        window_rect=window_rect,
        client_rect=Rect(origin_x, origin_y, origin_x + client_width, origin_y + client_height),
        monitor=_monitor_for_window(hwnd, monitors),
    )


def list_windows(*, visible_only: bool = True) -> list[WindowInfo]:
    """Every top-level window with a title, newest Z-order first.

    Used by the spike and the calibration wizard to let the user pick the right client when
    the process name is unknown.
    """
    ensure_dpi_awareness()
    monitors = list_monitors()
    handles: list[int] = []

    def collect(hwnd: int, _: object) -> bool:
        if visible_only and not win32gui.IsWindowVisible(hwnd):
            return True
        if not win32gui.GetWindowText(hwnd):
            return True
        handles.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    return [describe_window(hwnd, monitors) for hwnd in handles]


def find_game_window(
    process_name: str | None = None,
    title_contains: str = "",
    *,
    candidates: Sequence[str] = DEFAULT_WOW_PROCESSES,
) -> WindowInfo:
    """Locate the WoW client window.

    ``process_name`` wins when given; otherwise every name in ``candidates`` is tried. When
    several windows match, the one with the largest client area is returned, which skips
    launcher and tooltip windows.
    """
    wanted = [process_name] if process_name else list(candidates)
    wanted_lower = {name.lower() for name in wanted if name}
    needle = title_contains.lower()

    matches = [
        window
        for window in list_windows()
        if window.process_name.lower() in wanted_lower
        and (not needle or needle in window.title.lower())
        and window.client_rect.width > 0
        and window.client_rect.height > 0
    ]
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


class DxcamSource:
    """DXGI desktop duplication. Fast path, > 100 fps on a small ROI."""

    backend_name = "dxcam"

    def __init__(self, region: Rect, monitor: MonitorInfo, target_fps: int) -> None:
        import dxcam

        self.region = region
        device_idx, output_idx = _dxcam_output_for(monitor)
        # dxcam regions are relative to the output's own top-left, not the virtual desktop.
        local = region.translated(-monitor.rect.left, -monitor.rect.top)
        local = local.clamped_to(Rect(0, 0, monitor.rect.width, monitor.rect.height))

        self._camera = dxcam.create(
            device_idx=device_idx, output_idx=output_idx, output_color="BGR"
        )
        if self._camera is None:
            msg = f"dxcam could not open device {device_idx} output {output_idx}"
            raise CaptureError(msg)
        # video_mode keeps delivering frames while the screen is static, which a fishing
        # ROI often is; without it grab() starves whenever the water stops moving.
        self._camera.start(region=local.as_tuple(), target_fps=target_fps, video_mode=True)

    def grab(self) -> np.ndarray | None:
        return self._camera.get_latest_frame()

    def close(self) -> None:
        try:
            self._camera.stop()
        finally:
            self._camera.release()


class MssSource:
    """Portable fallback. Slower, but works where desktop duplication is unavailable."""

    backend_name = "mss"

    def __init__(self, region: Rect, _monitor: MonitorInfo, _target_fps: int) -> None:
        import mss

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


def _dxcam_output_for(monitor: MonitorInfo) -> tuple[int, int]:
    """Map a monitor to dxcam's (device_idx, output_idx).

    dxcam indexes outputs per adapter and exposes no public lookup, so this reads the
    factory singleton it builds at import and matches on HMONITOR, falling back to the
    device name. Raises when the monitor is not among dxcam's outputs, which sends the
    caller to the mss backend.
    """
    import dxcam

    factory = dxcam.Singleton._instances.get(dxcam.DXFactory)
    if factory is None:
        msg = "dxcam factory is not initialised"
        raise CaptureError(msg)

    for device_idx, outputs in enumerate(factory.outputs):
        for output_idx, output in enumerate(outputs):
            if int(output.hmonitor) == monitor.handle:
                return device_idx, output_idx
            if output.devicename == monitor.device:
                return device_idx, output_idx

    msg = f"dxcam has no output for monitor {monitor.device}"
    raise CaptureError(msg)


def open_capture(
    region: Rect,
    monitor: MonitorInfo,
    *,
    backend: CaptureBackend = CaptureBackend.AUTO,
    target_fps: int = 60,
) -> FrameSource:
    """Start capturing ``region``, preferring dxcam and degrading to mss with a warning."""
    ensure_dpi_awareness()
    if region.width <= 0 or region.height <= 0:
        msg = f"empty capture region {region}"
        raise CaptureError(msg)

    if backend is CaptureBackend.MSS:
        return MssSource(region, monitor, target_fps)

    try:
        return DxcamSource(region, monitor, target_fps)
    except Exception as exc:  # any DXGI failure is a fallback case
        if backend is CaptureBackend.DXCAM:
            msg = f"dxcam capture failed: {exc}"
            raise CaptureError(msg) from exc
        logger.warning("dxcam unavailable (%s); falling back to mss", exc)
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
