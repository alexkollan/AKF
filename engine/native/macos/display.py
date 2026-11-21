"""macOS: monitors, windows and process paths through Quartz and AppKit.

Geometry is in **points** of the Quartz global display space: origin at the top-left of the
main display, y growing down, the same space ``CGWindowListCopyWindowInfo``, ``CGEvent``
and ScreenCaptureKit use. A Retina display has ``scale`` = 2 captured pixels per point;
frames are delivered at that native resolution (see ``engine.capture``).

Window titles (``kCGWindowName``) are only readable with the Screen Recording permission;
without it every title is empty, which does not stop the game from being found by its
process name.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import threading

import AppKit
import Quartz

from engine.capture import CaptureError, MonitorInfo, Rect, WindowInfo

logger = logging.getLogger(__name__)

#: The layer ordinary application windows live on. Menus, the Dock, overlays and the menu
#: bar sit on other layers and are never the game.
NORMAL_WINDOW_LAYER = 0

_prepare_lock = threading.Lock()
_prepared = False


def prepare() -> None:
    """Ask ``mss`` for native-resolution frames, like the fast path delivers.

    ``mss`` defaults to ``kCGWindowImageNominalResolution`` on macOS, which halves a Retina
    capture to one pixel per point. Dropping that flag makes every capture backend hand out
    the same native pixels, which is what the FishBridge strip and the ROI frames assume.
    """
    global _prepared
    with _prepare_lock:
        if _prepared:
            return
        try:
            import mss.darwin

            mss.darwin.IMAGE_OPTIONS &= ~mss.darwin.kCGWindowImageNominalResolution
        except (ImportError, AttributeError) as exc:  # degrade: mss stays at 1x
            logger.warning("could not switch mss to native resolution: %s", exc)
        _prepared = True


def _rect(bounds: Quartz.CGRect) -> Rect:
    left = round(bounds.origin.x)
    top = round(bounds.origin.y)
    return Rect(left, top, left + round(bounds.size.width), top + round(bounds.size.height))


def _bounds_rect(bounds: dict[str, float]) -> Rect:
    """``kCGWindowBounds`` (a dict of X, Y, Width, Height in points) as a Rect."""
    left = round(float(bounds.get("X", 0)))
    top = round(float(bounds.get("Y", 0)))
    return Rect(
        left,
        top,
        left + round(float(bounds.get("Width", 0))),
        top + round(float(bounds.get("Height", 0))),
    )


# --------------------------------------------------------------------------------------
# monitors
# --------------------------------------------------------------------------------------


def _screens_by_display() -> dict[int, AppKit.NSScreen]:
    screens: dict[int, AppKit.NSScreen] = {}
    for screen in AppKit.NSScreen.screens() or ():
        number = screen.deviceDescription().get("NSScreenNumber")
        if number is not None:
            screens[int(number)] = screen
    return screens


def list_monitors() -> list[MonitorInfo]:
    """Active displays, main display first, in Quartz global (top-left) coordinates."""
    error, ids, count = Quartz.CGGetActiveDisplayList(32, None, None)
    if error:
        msg = f"CGGetActiveDisplayList failed ({error})"
        raise CaptureError(msg)
    displays = sorted(ids[:count], key=lambda d: not Quartz.CGDisplayIsMain(d))
    screens = _screens_by_display()
    main_height = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID()).size.height

    monitors: list[MonitorInfo] = []
    for index, display in enumerate(displays):
        rect = _rect(Quartz.CGDisplayBounds(display))
        work = rect
        scale = 1.0
        name = f"display {display}"
        screen = screens.get(int(display))
        if screen is not None:
            scale = float(screen.backingScaleFactor())
            name = str(screen.localizedName()) if hasattr(screen, "localizedName") else name
            # NSScreen frames are bottom-left origin on the main display; flip to Quartz.
            visible = screen.visibleFrame()
            top = main_height - (visible.origin.y + visible.size.height)
            work = Rect(
                round(visible.origin.x),
                round(top),
                round(visible.origin.x + visible.size.width),
                round(top + visible.size.height),
            )
        monitors.append(
            MonitorInfo(
                index=index,
                device=name,
                handle=int(display),
                rect=rect,
                work_rect=work,
                is_primary=bool(Quartz.CGDisplayIsMain(display)),
                scale=scale,
            )
        )
    return monitors


def monitor_for(rect: Rect, monitors: list[MonitorInfo]) -> MonitorInfo:
    """The monitor holding most of ``rect`` (the main one when it is off every screen)."""
    if not monitors:
        msg = "no displays found"
        raise CaptureError(msg)

    def overlap(monitor: MonitorInfo) -> int:
        width = min(rect.right, monitor.rect.right) - max(rect.left, monitor.rect.left)
        height = min(rect.bottom, monitor.rect.bottom) - max(rect.top, monitor.rect.top)
        return max(0, width) * max(0, height)

    best = max(monitors, key=overlap)
    if overlap(best) == 0:
        return next((m for m in monitors if m.is_primary), monitors[0])
    return best


# --------------------------------------------------------------------------------------
# processes
# --------------------------------------------------------------------------------------

_PROC_PIDPATHINFO_MAXSIZE = 4096
_libproc: ctypes.CDLL | None = None


def process_path(pid: int) -> str:
    """Full executable path for a pid (``proc_pidpath``), or "" when it cannot be read."""
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib")
    buffer = ctypes.create_string_buffer(_PROC_PIDPATHINFO_MAXSIZE)
    length = _libproc.proc_pidpath(int(pid), buffer, _PROC_PIDPATHINFO_MAXSIZE)
    if length <= 0:
        return ""
    return os.fsdecode(buffer.value)


def process_name(pid: int, owner_name: str = "") -> str:
    """The executable's file name ("World of Warcraft"), the analogue of ``Wow.exe``.

    Falls back to the window owner's name (the app's display name, "Wow") when the path
    cannot be read.
    """
    path = process_path(pid)
    return os.path.basename(path) if path else owner_name


# --------------------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------------------


def _client_rect(window_rect: Rect, *, titled: bool) -> Rect:
    """The window's content area.

    Quartz only reports the whole window frame. A fullscreen or borderless window *is* its
    content; a titled window loses its title bar, whose height AppKit reports for the
    standard style mask. WoW in "Windowed (Fullscreen)" mode covers the whole display and
    needs no correction, which is the recommended setup.
    """
    if not titled:
        return window_rect
    probe = AppKit.NSMakeRect(0, 0, window_rect.width, window_rect.height)
    content = AppKit.NSWindow.contentRectForFrameRect_styleMask_(
        probe, AppKit.NSWindowStyleMaskTitled
    )
    title_bar = max(0, round(window_rect.height - content.size.height))
    if title_bar >= window_rect.height:
        return window_rect
    return Rect(
        window_rect.left, window_rect.top + title_bar, window_rect.right, window_rect.bottom
    )


def _looks_titled(window_rect: Rect, monitor: MonitorInfo) -> bool:
    """A window that does not cover its whole display is assumed to have a title bar."""
    return window_rect != monitor.rect


def _describe(entry: dict, monitors: list[MonitorInfo]) -> WindowInfo:
    window_rect = _bounds_rect(entry.get(Quartz.kCGWindowBounds, {}))
    monitor = monitor_for(window_rect, monitors)
    pid = int(entry.get(Quartz.kCGWindowOwnerPID, 0))
    owner = str(entry.get(Quartz.kCGWindowOwnerName, "") or "")
    return WindowInfo(
        hwnd=int(entry.get(Quartz.kCGWindowNumber, 0)),
        pid=pid,
        process_name=process_name(pid, owner),
        title=str(entry.get(Quartz.kCGWindowName, "") or ""),
        window_rect=window_rect,
        client_rect=_client_rect(window_rect, titled=_looks_titled(window_rect, monitor)),
        monitor=monitor,
    )


def _is_app_window(entry: dict) -> bool:
    if int(entry.get(Quartz.kCGWindowLayer, -1)) != NORMAL_WINDOW_LAYER:
        return False
    bounds = entry.get(Quartz.kCGWindowBounds, {})
    return float(bounds.get("Width", 0)) > 1 and float(bounds.get("Height", 0)) > 1


def _counts_as_visible(entry: dict) -> bool:
    """On screen, or a titled window that is merely elsewhere.

    Quartz calls a window in another Space (WoW in fullscreen while the player looks at the
    desktop) or behind a fullscreen app "off screen". Windows would still call it visible,
    and the engine brings it to the front before using it, so it must be findable. Untitled
    off-screen windows are the helper surfaces every app keeps around and are left out.
    Titles need Screen Recording; without it only on-screen windows are found.
    """
    return bool(entry.get(Quartz.kCGWindowIsOnscreen)) or bool(entry.get(Quartz.kCGWindowName))


def list_windows(*, visible_only: bool = True) -> list[WindowInfo]:
    """Ordinary application windows, front to back (Quartz's own order)."""
    options = Quartz.kCGWindowListExcludeDesktopElements | Quartz.kCGWindowListOptionAll
    entries = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or ()
    monitors = list_monitors()
    own_pid = os.getpid()
    return [
        _describe(entry, monitors)
        for entry in entries
        if _is_app_window(entry)
        and int(entry.get(Quartz.kCGWindowOwnerPID, 0)) != own_pid
        and (not visible_only or _counts_as_visible(entry))
    ]


def describe_window(handle: int) -> WindowInfo:
    """Everything the engine needs about one window, by its ``kCGWindowNumber``."""
    entries = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionIncludingWindow, int(handle)
    )
    for entry in entries or ():
        if int(entry.get(Quartz.kCGWindowNumber, 0)) == int(handle):
            return _describe(entry, list_monitors())
    msg = f"no window with id {handle}"
    raise CaptureError(msg)
