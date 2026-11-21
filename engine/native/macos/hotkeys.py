"""macOS: a Quartz event tap for global hotkeys.

The tap sees every key event in the login session before any application does. For a bound
key it runs the callback once per press (auto-repeat while held fires nothing) and, when
swallowing, returns no event, so the bot's pause key does not also open a bag in WoW. Every
other key passes through untouched. This mirrors ``engine.native.windows.hotkeys``.

pynput is not used here: its macOS keyboard listener translates every event through the
Text Input Sources API, which recent macOS only allows on the main thread, and the engine
runs its hook on a worker thread.

**Swallowing does not hide a key from WoW.** It removes the event from the Quartz event
stream (verified: a tap after this one sees nothing), but WoW also reads the physical
keyboard through GameController/IOKit, beneath every event tap, so a bag bound to F10 still
opens. Only apps that read the event stream are spared. On macOS the bot's hotkeys must
therefore be keys WoW has no binding for (Settings → hotkeys). Synthetic keys are another
matter: WoW accepts ``CGEventPost`` events, which is how the engine plays.

Permissions: an active (swallowing) tap needs **Accessibility**; a listen-only tap needs
**Input Monitoring**. On a Mac keyboard the top row sends brightness and volume unless
"Use F1, F2, etc. keys as standard function keys" is on (System Settings → Keyboard →
Keyboard Shortcuts → Function Keys) or fn is held; those media events are not key events
and never reach the tap.

Callbacks run on the tap's thread and must return quickly: set a flag, do not do work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping

import Quartz

from engine.keys import InputError
from engine.native.macos.keyboard_mouse import keycode

logger = logging.getLogger(__name__)

#: How long ``start`` waits for the tap thread to come up.
START_TIMEOUT_S = 2.0
#: The run loop wakes this often to notice ``stop``.
RUN_SLICE_S = 0.25


class EventTapHook:
    def __init__(self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool) -> None:
        self._callbacks: dict[int, Callable[[], None]] = {
            keycode(name): callback for name, callback in bindings.items()
        }
        self._swallow = swallow
        self._held: set[int] = set()
        self._lock = threading.Lock()
        self._tap: object | None = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        if not self._callbacks or self._thread is not None:
            return
        self._stopping.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="hotkey-tap", daemon=True)
        self._thread.start()
        self._ready.wait(START_TIMEOUT_S)
        if self._error is not None:
            self._thread = None
            raise InputError(self._error)

    def stop(self) -> None:
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # -- the tap -----------------------------------------------------------------------

    def _run(self) -> None:
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(
            Quartz.kCGEventKeyUp
        )
        option = (
            Quartz.kCGEventTapOptionDefault if self._swallow else Quartz.kCGEventTapOptionListenOnly
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            option,
            mask,
            self._callback,
            None,
        )
        if tap is None:
            needed = "Accessibility" if self._swallow else "Input Monitoring"
            self._error = (
                f"macOS refused the keyboard hook: grant {needed} to the app running the "
                "engine (System Settings → Privacy & Security), then restart it. "
                "`uv run python -m tools.permissions` checks."
            )
            self._ready.set()
            return
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(tap, True)
        self._ready.set()
        try:
            while not self._stopping.is_set():
                Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, RUN_SLICE_S, False)
        finally:
            Quartz.CGEventTapEnable(tap, False)
            Quartz.CFRunLoopRemoveSource(loop, source, Quartz.kCFRunLoopDefaultMode)
            self._tap = None

    def _callback(
        self, _proxy: object, event_type: int, event: object, _refcon: object
    ) -> object | None:
        """Runs on the tap thread for every key event. Returning ``None`` swallows it."""
        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            # macOS turns a tap off when a callback was slow; turn it straight back on.
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        code = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        return self.handle(event_type, code, event)

    def handle(self, event_type: int, code: int, event: object) -> object | None:
        """Decide one key event: run the callback on a fresh press, then pass or swallow."""
        callback = self._callbacks.get(code)
        if callback is None:
            return event

        if event_type == Quartz.kCGEventKeyDown:
            with self._lock:
                first = code not in self._held
                self._held.add(code)
            if first:  # auto-repeat while held fires once
                try:
                    callback()
                except Exception:  # a hotkey must never kill the tap thread
                    logger.exception("hotkey callback failed")
        elif event_type == Quartz.kCGEventKeyUp:
            with self._lock:
                self._held.discard(code)

        return None if self._swallow else event
