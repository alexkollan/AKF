"""Windows: DPI awareness, monitors, windows and DXGI capture.

Every coordinate here is a physical pixel because the process is per-monitor DPI aware, and
window geometry is the *client area* in screen coordinates (see ``engine.capture``).
"""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Sequence

import numpy as np
import win32api
import win32con
import win32gui
import win32process

from engine.capture import CaptureError, MonitorInfo, Rect, WindowInfo

logger = logging.getLogger(__name__)

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
# monitors, processes, windows
# --------------------------------------------------------------------------------------


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


def process_path(pid: int) -> str:
    """Full executable path for a pid, or "" when the process cannot be opened."""
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
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _process_name(pid: int) -> str:
    """Executable name for a pid, or "" when the process cannot be opened."""
    return process_path(pid).rsplit("\\", 1)[-1]


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
    """Every top-level window with a title, newest Z-order first."""
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


# --------------------------------------------------------------------------------------
# DXGI capture
# --------------------------------------------------------------------------------------


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
