"""Global hotkeys that never reach the game.

A low-level keyboard hook sees the key before any application does. For the keys bound
here it runs the callback and then *suppresses* the event, so pressing the bot's pause key
does not also toggle a bag in WoW (F8-F12 are bag keys in the user's bindings). Every other
key passes through untouched.

Callbacks run on the hook thread and must return quickly: set a flag, do not do work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping

from pynput import keyboard

from engine.input import virtual_key

logger = logging.getLogger(__name__)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105


def parse_hotkey(spec: str) -> int:
    """``"<f12>"`` or ``"f12"`` to a virtual-key code. Single keys only, no chords."""
    name = spec.strip().strip("<>").strip()
    if "+" in name:
        msg = f"hotkey chords are not supported yet: {spec!r}"
        raise ValueError(msg)
    return virtual_key(name)


class SwallowingHotkeys:
    def __init__(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        self._callbacks: dict[int, Callable[[], None]] = {
            parse_hotkey(spec): callback for spec, callback in bindings.items()
        }
        self._held: set[int] = set()
        self._lock = threading.Lock()
        self._listener = keyboard.Listener(win32_event_filter=self._filter)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def _filter(self, msg: int, data: object) -> bool:
        vk = int(getattr(data, "vkCode", 0))
        callback = self._callbacks.get(vk)
        if callback is None:
            return True

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

        self._listener.suppress_event()  # raises; the key stops here
        return False
