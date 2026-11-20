"""Synthetic keyboard and mouse input, plus window focus.

The game reads **scancodes**, not virtual keys, so every key event here carries
``KEYEVENTF_SCANCODE`` and a scancode derived from the physical key position. Mouse moves
are absolute over the whole virtual desktop, which is what makes them land correctly on a
second monitor with its own scaling.

Two invariants the rest of the engine relies on:

* every key this module presses is tracked, so :meth:`InputController.release_all` can
  drop them all on an emergency stop;
* ``dry_run=True`` records actions instead of sending them, so the FSM and the modes can
  be exercised with the game open and untouched.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum

import win32api
import win32con
import win32gui
import win32process

from engine import clock
from engine.capture import Rect, WindowInfo, ensure_dpi_awareness

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)


class InputError(RuntimeError):
    """Windows refused the synthetic input."""


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
# key names
# --------------------------------------------------------------------------------------

_NAMED_KEYS: dict[str, int] = {
    "space": win32con.VK_SPACE,
    "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "escape": win32con.VK_ESCAPE,
    "esc": win32con.VK_ESCAPE,
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
    "shift": win32con.VK_LSHIFT,
    "lshift": win32con.VK_LSHIFT,
    "rshift": win32con.VK_RSHIFT,
    "ctrl": win32con.VK_LCONTROL,
    "lctrl": win32con.VK_LCONTROL,
    "rctrl": win32con.VK_RCONTROL,
    "alt": win32con.VK_LMENU,
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
_NAMED_KEYS |= {f"f{i}": win32con.VK_F1 + i - 1 for i in range(1, 25)}
_NAMED_KEYS |= {f"numpad{i}": win32con.VK_NUMPAD0 + i for i in range(10)}

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
    key = name.strip().lower()
    if not key:
        msg = "empty key name"
        raise InputError(msg)
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1:
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
    extended = vk in _EXTENDED_VKS or name.strip().lower() == "numpadenter"
    return code, extended


class MouseButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


_BUTTON_FLAGS: dict[MouseButton, tuple[int, int]] = {
    MouseButton.LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    MouseButton.RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    MouseButton.MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


# --------------------------------------------------------------------------------------
# window focus
# --------------------------------------------------------------------------------------


def foreground_hwnd() -> int:
    return int(win32gui.GetForegroundWindow())


def has_focus(window: WindowInfo) -> bool:
    """True while the given window is the foreground one.

    The engine auto-pauses when this turns false, because SendInput follows focus and
    would otherwise type into whatever the user switched to.
    """
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


def virtual_screen_rect() -> Rect:
    left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    return Rect(left, top, left + width, top + height)


# --------------------------------------------------------------------------------------
# controller
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InputAction:
    """One recorded action, used by ``--dry-run`` and by the FSM tests."""

    kind: str  # "key_down", "key_up", "move", "button_down", "button_up"
    target: str
    at: float


@dataclass
class InputController:
    """Everything the engine is allowed to do to the game.

    Set ``dry_run`` to record actions instead of performing them; ``actions`` then holds
    the log. The held-key bookkeeping is what makes a clean emergency stop possible.
    """

    dry_run: bool = False
    actions: list[InputAction] = field(default_factory=list)
    _held_keys: set[str] = field(default_factory=set, repr=False)
    _held_buttons: set[MouseButton] = field(default_factory=set, repr=False)

    # -- keyboard ----------------------------------------------------------------------

    def key_down(self, key: str) -> None:
        code, extended = scancode(key)
        self._record("key_down", key)
        self._held_keys.add(key)
        if self.dry_run:
            return
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        _send(
            _Input(
                type=INPUT_KEYBOARD,
                ki=_KeyboardInput(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0),
            )
        )

    def key_up(self, key: str) -> None:
        code, extended = scancode(key)
        self._record("key_up", key)
        self._held_keys.discard(key)
        if self.dry_run:
            return
        flags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        _send(
            _Input(
                type=INPUT_KEYBOARD,
                ki=_KeyboardInput(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0),
            )
        )

    def tap(self, key: str, hold_ms: int = 45) -> None:
        """Press and release. The hold is there because the game drops zero-length taps."""
        self.key_down(key)
        self._sleep(hold_ms)
        self.key_up(key)

    def tap_with_modifiers(
        self, key: str, modifiers: tuple[str, ...] = (), hold_ms: int = 45
    ) -> None:
        for modifier in modifiers:
            self.key_down(modifier)
        try:
            self.tap(key, hold_ms)
        finally:
            for modifier in reversed(modifiers):
                self.key_up(modifier)

    # -- mouse -------------------------------------------------------------------------

    def move_to(self, x: int, y: int) -> None:
        """Move the cursor to a point in screen (virtual desktop) coordinates."""
        self._record("move", f"{x},{y}")
        if self.dry_run:
            return
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

    def move_to_client(self, window: WindowInfo, x: int, y: int) -> None:
        """Move to a point given relative to the WoW client area."""
        screen_x, screen_y = window.client_to_screen(x, y)
        self.move_to(screen_x, screen_y)

    def glide_to(self, x: int, y: int, steps: int = 6, step_ms: int = 8) -> None:
        """Move in a straight line instead of teleporting.

        WoW only updates its soft-target and tooltip state on mouse movement, so a single
        jump sometimes lands without the bobber cursor ever appearing.
        """
        start_x, start_y = self.cursor_position()
        for index in range(1, max(1, steps) + 1):
            ratio = index / max(1, steps)
            self.move_to(
                round(start_x + (x - start_x) * ratio),
                round(start_y + (y - start_y) * ratio),
            )
            if index < steps:
                self._sleep(step_ms)

    def button_down(self, button: MouseButton) -> None:
        down, _ = _BUTTON_FLAGS[button]
        self._record("button_down", button.value)
        self._held_buttons.add(button)
        if self.dry_run:
            return
        _send(_Input(type=INPUT_MOUSE, mi=_MouseInput(dwFlags=down)))

    def button_up(self, button: MouseButton) -> None:
        _, up = _BUTTON_FLAGS[button]
        self._record("button_up", button.value)
        self._held_buttons.discard(button)
        if self.dry_run:
            return
        _send(_Input(type=INPUT_MOUSE, mi=_MouseInput(dwFlags=up)))

    def click(
        self,
        button: MouseButton = MouseButton.RIGHT,
        *,
        modifiers: tuple[str, ...] = (),
        hold_ms: int = 40,
    ) -> None:
        for modifier in modifiers:
            self.key_down(modifier)
        try:
            self.button_down(button)
            self._sleep(hold_ms)
            self.button_up(button)
        finally:
            for modifier in reversed(modifiers):
                self.key_up(modifier)

    def cursor_position(self) -> tuple[int, int]:
        if self.dry_run:
            return (0, 0)
        return win32api.GetCursorPos()

    # -- safety ------------------------------------------------------------------------

    def release_all(self) -> None:
        """Drop every key and button this controller is holding.

        Called by the emergency stop, so it must never raise: a half-released keyboard is
        worse than a logged failure.
        """
        for key in list(self._held_keys):
            try:
                self.key_up(key)
            except Exception:  # a stuck key is worse than a logged failure
                logger.exception("could not release key %s", key)
                self._held_keys.discard(key)
        for button in list(self._held_buttons):
            try:
                self.button_up(button)
            except Exception:  # a stuck key is worse than a logged failure
                logger.exception("could not release button %s", button)
                self._held_buttons.discard(button)

    @property
    def held(self) -> tuple[tuple[str, ...], tuple[MouseButton, ...]]:
        return tuple(sorted(self._held_keys)), tuple(sorted(self._held_buttons))

    # -- internals ---------------------------------------------------------------------

    def _record(self, kind: str, target: str) -> None:
        action = InputAction(kind=kind, target=target, at=clock.now())
        self.actions.append(action)
        if self.dry_run:
            logger.info("dry-run %s %s", kind, target)

    def _sleep(self, milliseconds: int) -> None:
        """Short blocking sleep. Only ever called from a worker thread, never a coroutine."""
        if milliseconds > 0 and not self.dry_run:
            time.sleep(milliseconds / 1000)
