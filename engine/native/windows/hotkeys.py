"""Windows: a low-level keyboard hook for global hotkeys.

The hook sees a key before any application does. For a bound key it runs the callback once
per press (auto-repeat while held fires nothing) and, when swallowing, *suppresses* the
event, so the bot's pause key does not also toggle a bag in WoW (F8-F12 are bag keys in the
user's bindings). Every other key passes through untouched.

Callbacks run on the hook thread and must return quickly: set a flag, do not do work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping

from pynput import keyboard

from engine.native.windows.keyboard_mouse import virtual_key

logger = logging.getLogger(__name__)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105


class LowLevelHook:
    def __init__(self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool) -> None:
        self._callbacks: dict[int, Callable[[], None]] = {
            virtual_key(name): callback for name, callback in bindings.items()
        }
        self._swallow = swallow
        self._held: set[int] = set()
        self._lock = threading.Lock()
        self._listener = keyboard.Listener(win32_event_filter=self._filter)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def _filter(self, msg: int, data: object) -> bool:
        """pynput ``win32_event_filter``: runs on the hook thread for every key event."""
        vk = int(getattr(data, "vkCode", 0))
        callback = self._callbacks.get(vk)
        if callback is None:
            return True

        # F10 arrives as WM_SYSKEYDOWN, because it is the menu key.
        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            with self._lock:
                first = vk not in self._held
                self._held.add(vk)
            if first:  # auto-repeat while held fires once
                try:
                    callback()
                except Exception:  # a hotkey must never kill the hook thread
                    logger.exception("hotkey callback failed")
        elif msg in (WM_KEYUP, WM_SYSKEYUP):
            with self._lock:
                self._held.discard(vk)

        if not self._swallow:
            return True
        self._listener.suppress_event()  # raises; the key stops here
        return False
