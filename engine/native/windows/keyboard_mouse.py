"""Windows: synthetic keyboard and mouse through ``SendInput``, plus window focus.

The game reads **scancodes**, not virtual keys, so every key event here carries
``KEYEVENTF_SCANCODE`` and a scancode derived from the physical key position. Mouse moves
are absolute over the whole virtual desktop, which is what makes them land correctly on a
second monitor with its own scaling.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

from engine.capture import Rect, WindowInfo
from engine.input import MouseButton
from engine.keys import InputError, normalize_key
from engine.native.windows.display import ensure_dpi_awareness

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

# --------------------------------------------------------------------------------------
# SendInput plumbing
# --------------------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

MAPVK_VK_TO_VSC = 0

ULONG_PTR = wintypes.WPARAM


class _MouseInput(ctypes.Structure):
    _fields_ = (  # ctypes layout, not a mutable attribute
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _KeyboardInput(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _HardwareInput(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _InputUnion(ctypes.Union):
    _fields_ = (("mi", _MouseInput), ("ki", _KeyboardInput), ("hi", _HardwareInput))


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _InputUnion))


def _send(*events: _Input) -> None:
    count = len(events)
    array = (_Input * count)(*events)
    sent = user32.SendInput(count, ctypes.byref(array), ctypes.sizeof(_Input))
    if sent != count:
        error = ctypes.get_last_error()
        msg = (
            f"SendInput delivered {sent}/{count} events (error {error}). "
            "If the game runs as administrator, this app has to as well."
        )
        raise InputError(msg)


# --------------------------------------------------------------------------------------
# key names -> virtual keys -> scancodes
# --------------------------------------------------------------------------------------

_NAMED_VKS: dict[str, int] = {
    "space": win32con.VK_SPACE,
    "enter": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "escape": win32con.VK_ESCAPE,
    "backspace": win32con.VK_BACK,
    "delete": win32con.VK_DELETE,
    "insert": win32con.VK_INSERT,
    "home": win32con.VK_HOME,
    "end": win32con.VK_END,
    "pageup": win32con.VK_PRIOR,
    "pagedown": win32con.VK_NEXT,
    "up": win32con.VK_UP,
    "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT,
    "lshift": win32con.VK_LSHIFT,
    "rshift": win32con.VK_RSHIFT,
    "lctrl": win32con.VK_LCONTROL,
    "rctrl": win32con.VK_RCONTROL,
    "lalt": win32con.VK_LMENU,
    "ralt": win32con.VK_RMENU,
    "capslock": win32con.VK_CAPITAL,
    "numpadadd": win32con.VK_ADD,
    "numpadsubtract": win32con.VK_SUBTRACT,
    "numpadmultiply": win32con.VK_MULTIPLY,
    "numpaddivide": win32con.VK_DIVIDE,
    "numpaddecimal": win32con.VK_DECIMAL,
    "numpadenter": win32con.VK_RETURN,
}
_NAMED_VKS |= {f"f{i}": win32con.VK_F1 + i - 1 for i in range(1, 25)}
_NAMED_VKS |= {f"numpad{i}": win32con.VK_NUMPAD0 + i for i in range(10)}

# Keys whose scancode needs the E0 prefix, or the game sees the numpad twin instead.
_EXTENDED_VKS: frozenset[int] = frozenset(
    {
        win32con.VK_INSERT,
        win32con.VK_DELETE,
        win32con.VK_HOME,
        win32con.VK_END,
        win32con.VK_PRIOR,
        win32con.VK_NEXT,
        win32con.VK_UP,
        win32con.VK_DOWN,
        win32con.VK_LEFT,
        win32con.VK_RIGHT,
        win32con.VK_RCONTROL,
        win32con.VK_RMENU,
        win32con.VK_DIVIDE,
        win32con.VK_NUMLOCK,
    }
)


def virtual_key(name: str) -> int:
    """Resolve a key name from a profile to a virtual-key code.

    Letters and digits map by position rather than through the active keyboard layout, so
    a Greek or other non-US layout still presses the key the user bound.
    """
    key = normalize_key(name)
    if key in _NAMED_VKS:
        return _NAMED_VKS[key]
    if "a" <= key <= "z":
        return ord(key.upper())
    if "0" <= key <= "9":
        return ord(key)
    scan = win32api.VkKeyScan(key)
    if scan != -1:
        return scan & 0xFF
    msg = f"unknown key name {name!r}"
    raise InputError(msg)


def scancode(name: str) -> tuple[int, bool]:
    """``(scancode, is_extended)`` for a key name."""
    vk = virtual_key(name)
    code = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    if code == 0:
        msg = f"no scancode for key {name!r} (vk {vk})"
        raise InputError(msg)
    extended = vk in _EXTENDED_VKS or normalize_key(name) == "numpadenter"
    return code, extended


_BUTTON_FLAGS: dict[MouseButton, tuple[int, int]] = {
    MouseButton.LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    MouseButton.RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    MouseButton.MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


# --------------------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------------------


def send_key(key: str, *, down: bool) -> None:
    code, extended = scancode(key)
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    _send(
        _Input(
            type=INPUT_KEYBOARD,
            ki=_KeyboardInput(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0),
        )
    )


def virtual_screen_rect() -> Rect:
    left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    return Rect(left, top, left + width, top + height)


def move_cursor(x: int, y: int) -> None:
    """Move the cursor to a point in screen (virtual desktop) coordinates."""
    screen = virtual_screen_rect()
    # SendInput's absolute range is 0..65535 across the whole virtual desktop.
    dx = round((x - screen.left) * 65535 / max(1, screen.width - 1))
    dy = round((y - screen.top) * 65535 / max(1, screen.height - 1))
    _send(
        _Input(
            type=INPUT_MOUSE,
            mi=_MouseInput(
                dx=dx,
                dy=dy,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0,
                dwExtraInfo=0,
            ),
        )
    )


def send_button(button: MouseButton, *, down: bool) -> None:
    press, release = _BUTTON_FLAGS[button]
    _send(_Input(type=INPUT_MOUSE, mi=_MouseInput(dwFlags=press if down else release)))


def cursor_position() -> tuple[int, int]:
    x, y = win32api.GetCursorPos()
    return (int(x), int(y))


# --------------------------------------------------------------------------------------
# window focus
# --------------------------------------------------------------------------------------


def foreground_hwnd() -> int:
    return int(win32gui.GetForegroundWindow())


def has_focus(window: WindowInfo) -> bool:
    return foreground_hwnd() == window.hwnd


def focus_window(window: WindowInfo, *, settle_ms: int = 120) -> bool:
    """Bring a window to the foreground, working around the foreground lock.

    Windows only lets the process that owns the foreground window hand it over, so this
    attaches to that window's input thread first. Returns whether the window ended up in
    front.
    """
    ensure_dpi_awareness()
    hwnd = window.hwnd
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    current = foreground_hwnd()
    if current == hwnd:
        return True

    our_thread = win32api.GetCurrentThreadId()
    their_thread, _pid = win32process.GetWindowThreadProcessId(current) if current else (0, 0)
    attached = False
    if their_thread and their_thread != our_thread:
        attached = bool(win32process.AttachThreadInput(our_thread, their_thread, True))
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # SetForegroundWindow may refuse
        logger.warning("SetForegroundWindow(%s) failed: %s", hwnd, exc)
    finally:
        if attached:
            win32process.AttachThreadInput(our_thread, their_thread, False)

    if settle_ms:
        time.sleep(settle_ms / 1000)
    return foreground_hwnd() == hwnd
