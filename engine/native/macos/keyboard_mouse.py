"""macOS: synthetic keyboard and mouse through ``CGEventPost``, plus app focus.

Keys are posted as **virtual keycodes** (``kVK_*``), which name a physical key position
like Windows scancodes do, so a Greek or other non-US layout still presses the key the user
bound. Modifiers are posted as their own key events *and* carried in every event's flags
while held, because games read either. Mouse coordinates are global points.

Every function here needs the **Accessibility** permission for the process that runs the
engine (System Settings → Privacy & Security → Accessibility); without it macOS drops the
events silently, which :func:`check_trusted` turns into an error.
"""

from __future__ import annotations

import logging
import threading
import time

import AppKit
import ApplicationServices as AX
import Quartz

from engine.capture import WindowInfo
from engine.input import MouseButton
from engine.keys import InputError, normalize_key

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# key names -> virtual keycodes (Carbon's kVK_* constants, HIToolbox/Events.h)
# --------------------------------------------------------------------------------------

_ANSI: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
    "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
    "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18,
    "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E, "o": 0x1F, "u": 0x20,
    "[": 0x21, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29,
    "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F, "`": 0x32,
}  # fmt: skip

#: Shifted characters press their unshifted key, like ``VkKeyScan`` on Windows.
_SHIFTED: dict[str, str] = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./", strict=True))

_NAMED: dict[str, int] = {
    "space": 0x31,
    "enter": 0x24,
    "tab": 0x30,
    "escape": 0x35,
    "backspace": 0x33,  # kVK_Delete: the key labelled delete on a Mac
    "delete": 0x75,  # kVK_ForwardDelete
    "home": 0x73,
    "end": 0x77,
    "pageup": 0x74,
    "pagedown": 0x79,
    "up": 0x7E,
    "down": 0x7D,
    "left": 0x7B,
    "right": 0x7C,
    "lshift": 0x38,
    "rshift": 0x3C,
    "lctrl": 0x3B,
    "rctrl": 0x3E,
    "lalt": 0x3A,  # option
    "ralt": 0x3D,
    "capslock": 0x39,
    "numpadadd": 0x45,
    "numpadsubtract": 0x4E,
    "numpadmultiply": 0x43,
    "numpaddivide": 0x4B,
    "numpaddecimal": 0x41,
    "numpadenter": 0x4C,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62,
    "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F, "f13": 0x69, "f14": 0x6B,
    "f15": 0x71, "f16": 0x6A, "f17": 0x40, "f18": 0x4F, "f19": 0x50, "f20": 0x5A,
    "numpad0": 0x52, "numpad1": 0x53, "numpad2": 0x54, "numpad3": 0x55, "numpad4": 0x56,
    "numpad5": 0x57, "numpad6": 0x58, "numpad7": 0x59, "numpad8": 0x5B, "numpad9": 0x5C,
}  # fmt: skip

#: Canonical names a Mac keyboard has no key for.
_MISSING: frozenset[str] = frozenset({"insert", "f21", "f22", "f23", "f24"})

_MODIFIER_FLAGS: dict[str, int] = {
    "lshift": Quartz.kCGEventFlagMaskShift,
    "rshift": Quartz.kCGEventFlagMaskShift,
    "lctrl": Quartz.kCGEventFlagMaskControl,
    "rctrl": Quartz.kCGEventFlagMaskControl,
    "lalt": Quartz.kCGEventFlagMaskAlternate,
    "ralt": Quartz.kCGEventFlagMaskAlternate,
}

_NUMPAD_KEYS = frozenset(
    {f"numpad{i}" for i in range(10)}
    | {"numpadadd", "numpadsubtract", "numpadmultiply", "numpaddivide"}
    | {"numpaddecimal", "numpadenter"}
)
_ARROWS = frozenset({"up", "down", "left", "right"})
_FN_KEYS = frozenset({f"f{i}" for i in range(1, 21)} | {"home", "end", "pageup", "pagedown"})
_FN_KEYS |= {"delete"} | _ARROWS


def keycode(name: str) -> int:
    """The virtual keycode for a key name; raises :class:`InputError` when there is none."""
    key = normalize_key(name)
    if key in _MISSING:
        msg = f"a Mac keyboard has no {key!r} key; bind another one"
        raise InputError(msg)
    if key in _NAMED:
        return _NAMED[key]
    key = _SHIFTED.get(key, key)
    if key in _ANSI:
        return _ANSI[key]
    msg = f"no Mac key for {name!r}"
    raise InputError(msg)


def _device_flags(key: str) -> int:
    """The flags real hardware puts on this key's events (keypad, fn)."""
    flags = 0
    if key in _NUMPAD_KEYS or key in _ARROWS:
        flags |= Quartz.kCGEventFlagMaskNumericPad
    if key in _FN_KEYS:
        flags |= Quartz.kCGEventFlagMaskSecondaryFn
    return flags


# --------------------------------------------------------------------------------------
# posting
# --------------------------------------------------------------------------------------

_lock = threading.Lock()
_held_modifiers: set[str] = set()
_held_buttons: set[MouseButton] = set()
_source: Quartz.CGEventSourceRef | None = None


def check_trusted() -> None:
    """Raise unless this process may post input (the Accessibility permission)."""
    if not AX.AXIsProcessTrusted():
        msg = (
            "this process is not allowed to control the computer: grant Accessibility to "
            "the app running the engine (System Settings → Privacy & Security → "
            "Accessibility), then restart it. `uv run python -m tools.permissions` checks."
        )
        raise InputError(msg)


def _event_source() -> Quartz.CGEventSourceRef:
    global _source
    if _source is None:
        _source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        # By default the system ignores the real mouse and keyboard for 0.25 s after each
        # synthetic event; the player must keep full control while the bot runs.
        Quartz.CGEventSourceSetLocalEventsSuppressionInterval(_source, 0.0)
    return _source


def _modifier_flags() -> int:
    flags = 0
    for key in _held_modifiers:
        flags |= _MODIFIER_FLAGS[key]
    return flags


def _post(event: Quartz.CGEventRef | None, what: str) -> None:
    if event is None:
        msg = f"could not create the {what} event"
        raise InputError(msg)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def send_key(key: str, *, down: bool) -> None:
    check_trusted()
    canonical = normalize_key(key)
    code = keycode(canonical)
    with _lock:
        if canonical in _MODIFIER_FLAGS:
            if down:
                _held_modifiers.add(canonical)
            else:
                _held_modifiers.discard(canonical)
        event = Quartz.CGEventCreateKeyboardEvent(_event_source(), code, down)
        if event is not None:
            Quartz.CGEventSetFlags(event, _modifier_flags() | _device_flags(canonical))
        _post(event, f"key {canonical!r}")


def cursor_position() -> tuple[int, int]:
    location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (round(location.x), round(location.y))


_BUTTONS: dict[MouseButton, tuple[int, int, int, int]] = {
    # button number, down, up, dragged
    MouseButton.LEFT: (
        Quartz.kCGMouseButtonLeft,
        Quartz.kCGEventLeftMouseDown,
        Quartz.kCGEventLeftMouseUp,
        Quartz.kCGEventLeftMouseDragged,
    ),
    MouseButton.RIGHT: (
        Quartz.kCGMouseButtonRight,
        Quartz.kCGEventRightMouseDown,
        Quartz.kCGEventRightMouseUp,
        Quartz.kCGEventRightMouseDragged,
    ),
    MouseButton.MIDDLE: (
        Quartz.kCGMouseButtonCenter,
        Quartz.kCGEventOtherMouseDown,
        Quartz.kCGEventOtherMouseUp,
        Quartz.kCGEventOtherMouseDragged,
    ),
}


def move_cursor(x: int, y: int) -> None:
    """Move the cursor to a point in global coordinates (points).

    The event is a drag while a button is held, as real hardware would send, and carries
    the movement delta, which is what games that read relative motion look at.
    """
    check_trusted()
    with _lock:
        previous = cursor_position()
        event_type = Quartz.kCGEventMouseMoved
        button = Quartz.kCGMouseButtonLeft
        for held in (MouseButton.LEFT, MouseButton.RIGHT, MouseButton.MIDDLE):
            if held in _held_buttons:
                button, _down, _up, event_type = _BUTTONS[held]
                break
        point = Quartz.CGPointMake(float(x), float(y))
        event = Quartz.CGEventCreateMouseEvent(_event_source(), event_type, point, button)
        if event is not None:
            Quartz.CGEventSetIntegerValueField(
                event, Quartz.kCGMouseEventDeltaX, round(x - previous[0])
            )
            Quartz.CGEventSetIntegerValueField(
                event, Quartz.kCGMouseEventDeltaY, round(y - previous[1])
            )
            Quartz.CGEventSetFlags(event, _modifier_flags())
        _post(event, "mouse move")


def send_button(button: MouseButton, *, down: bool) -> None:
    check_trusted()
    number, press, release, _drag = _BUTTONS[button]
    with _lock:
        x, y = cursor_position()
        event = Quartz.CGEventCreateMouseEvent(
            _event_source(), press if down else release, Quartz.CGPointMake(x, y), number
        )
        if event is not None:
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 1)
            Quartz.CGEventSetFlags(event, _modifier_flags())
        _post(event, f"{button.value} button")
        if down:
            _held_buttons.add(button)
        else:
            _held_buttons.discard(button)


# --------------------------------------------------------------------------------------
# focus
# --------------------------------------------------------------------------------------


def frontmost_pid() -> int:
    """The pid of the application in front.

    Asked through Accessibility first: ``NSWorkspace.frontmostApplication`` is only kept
    current by notifications on the main thread's run loop, and the engine checks focus
    from a worker thread of a process that runs no AppKit loop, where it goes stale.
    """
    if AX.AXIsProcessTrusted():
        system = AX.AXUIElementCreateSystemWide()
        error, app = AX.AXUIElementCopyAttributeValue(
            system, AX.kAXFocusedApplicationAttribute, None
        )
        if error == 0 and app is not None:
            error, pid = AX.AXUIElementGetPid(app, None)
            if error == 0:
                return int(pid)
    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(front.processIdentifier()) if front is not None else 0


def has_focus(window: WindowInfo) -> bool:
    return frontmost_pid() == window.pid


def focus_window(window: WindowInfo, *, settle_ms: int = 120) -> bool:
    """Bring the game to the front. Returns whether it ended up there.

    Accessibility's ``AXFrontmost`` works from a background process; plain activation is
    the fallback, and since macOS 14 the system may decline it for an app that is not
    itself in front.
    """
    if frontmost_pid() == window.pid:
        return True
    if AX.AXIsProcessTrusted():
        app = AX.AXUIElementCreateApplication(window.pid)
        error = AX.AXUIElementSetAttributeValue(app, AX.kAXFrontmostAttribute, True)
        if error != 0:
            logger.warning("AXFrontmost on pid %s failed (%s)", window.pid, error)
    running = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
    if running is not None and frontmost_pid() != window.pid:
        running.activateWithOptions_(AppKit.NSApplicationActivateAllWindows)
    if settle_ms:
        time.sleep(settle_ms / 1000)
    return frontmost_pid() == window.pid
