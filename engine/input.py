"""Synthetic keyboard and mouse input, plus window focus.

Keys are named in plain words (``engine.keys``); the operating system's backend
(``engine.native``) turns a name into whatever the game reads — scancodes on Windows. Mouse
coordinates are screen (virtual desktop) pixels, so a click lands correctly on a second
monitor with its own scaling.

Two invariants the rest of the engine relies on:

* every key this module presses is tracked, so :meth:`InputController.release_all` can
  drop them all on an emergency stop;
* ``dry_run=True`` records actions instead of sending them, so the FSM and the modes can
  be exercised with the game open and untouched — on any OS, even one without a backend.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from engine import clock, native
from engine.capture import WindowInfo
from engine.keys import InputError, normalize_key

__all__ = [
    "InputAction",
    "InputController",
    "InputError",
    "MouseButton",
    "check_key",
    "focus_window",
    "has_focus",
]

logger = logging.getLogger(__name__)


class MouseButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


def check_key(name: str) -> str:
    """Validate a key name for this machine; returns its canonical spelling.

    Raises :class:`InputError` for a name no OS knows, or one this OS cannot press. On an
    OS without a backend only the portable grammar is checked.
    """
    key = normalize_key(name)
    backend = native.current()
    if backend.supported:
        backend.check_key(key)
    return key


# --------------------------------------------------------------------------------------
# window focus
# --------------------------------------------------------------------------------------


def has_focus(window: WindowInfo) -> bool:
    """True while the given window is the foreground one.

    The engine auto-pauses when this turns false, because synthetic input follows focus and
    would otherwise type into whatever the user switched to.
    """
    return native.current().has_focus(window)


def focus_window(window: WindowInfo, *, settle_ms: int = 120) -> bool:
    """Bring a window to the foreground. Returns whether it ended up in front."""
    return native.current().focus_window(window, settle_ms=settle_ms)


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
        canonical = check_key(key)
        self._record("key_down", key)
        self._held_keys.add(key)
        if self.dry_run:
            return
        native.current().send_key(canonical, down=True)

    def key_up(self, key: str) -> None:
        canonical = check_key(key)
        self._record("key_up", key)
        self._held_keys.discard(key)
        if self.dry_run:
            return
        native.current().send_key(canonical, down=False)

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
        native.current().move_cursor(x, y)

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
        self._record("button_down", button.value)
        self._held_buttons.add(button)
        if self.dry_run:
            return
        native.current().send_button(button, down=True)

    def button_up(self, button: MouseButton) -> None:
        self._record("button_up", button.value)
        self._held_buttons.discard(button)
        if self.dry_run:
            return
        native.current().send_button(button, down=False)

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
        return native.current().cursor_position()

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
