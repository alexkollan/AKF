"""Global hotkeys, by default swallowed so they never reach the game.

The bot's keys (F9 start/stop, F10 pause, F12 emergency stop) are also bag keys in the
user's WoW bindings, so a hook that sees them before any application runs the callback and
then *suppresses* the event. Every other key passes through untouched. With
``swallow=False`` the bound keys still reach the game — for watching the player's own cast
key, say.

On macOS swallowing only hides a key from apps that read the event stream; WoW reads the
physical keyboard through GameController/HID as well, so there the bound keys must be ones
the game does not use (see ``engine.native.macos.hotkeys``).

The hook itself is the operating system's (``engine.native``). Callbacks run on its thread
and must return quickly: set a flag, do not do work. Each press fires once, however long
the key is held.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from engine import native
from engine.input import check_key


def parse_hotkey(spec: str) -> str:
    """``"<f12>"`` or ``"F12"`` to the canonical key name ``"f12"``. Single keys only."""
    name = spec.strip().strip("<>").strip()
    if "+" in name:
        msg = f"hotkey chords are not supported yet: {spec!r}"
        raise ValueError(msg)
    return check_key(name)


class GlobalHotkeys:
    def __init__(self, bindings: Mapping[str, Callable[[], None]], *, swallow: bool = True) -> None:
        resolved = {parse_hotkey(spec): callback for spec, callback in bindings.items()}
        self._hook = native.current().hotkey_hook(resolved, swallow=swallow)

    def start(self) -> None:
        self._hook.start()

    def stop(self) -> None:
        self._hook.stop()
