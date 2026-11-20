"""Key names, the same on every operating system.

Profiles, settings and hotkeys name keys in plain words (``"]"``, ``"f8"``, ``"space"``,
``"numpad5"``). This module decides which names exist and what their canonical spelling is;
each native backend (``engine.native``) then maps a canonical name to its own codes —
scancodes on Windows, virtual keycodes on macOS. Keeping the grammar here means a profile
written on one machine is valid on the other, and a dry run can check every key without a
backend at all.
"""

from __future__ import annotations


class InputError(RuntimeError):
    """A key name is unknown, or the operating system refused the synthetic input."""


#: Names that map to a dedicated key. Single printable characters are valid too.
NAMED_KEYS: frozenset[str] = frozenset(
    {
        "space",
        "enter",
        "tab",
        "escape",
        "backspace",
        "delete",
        "insert",
        "home",
        "end",
        "pageup",
        "pagedown",
        "up",
        "down",
        "left",
        "right",
        "lshift",
        "rshift",
        "lctrl",
        "rctrl",
        "lalt",
        "ralt",
        "capslock",
        "numpadadd",
        "numpadsubtract",
        "numpadmultiply",
        "numpaddivide",
        "numpaddecimal",
        "numpadenter",
        *(f"f{i}" for i in range(1, 25)),
        *(f"numpad{i}" for i in range(10)),
    }
)

#: Alternative spellings, folded into the canonical name.
ALIASES: dict[str, str] = {
    "return": "enter",
    "esc": "escape",
    "shift": "lshift",
    "ctrl": "lctrl",
    "alt": "lalt",
}


def normalize_key(name: str) -> str:
    """The canonical spelling of a key name; raises :class:`InputError` for unknown names.

    Letters are lower-cased. Any other single printable character (``"]"``, ``"-"``) is
    accepted as-is: whether the active keyboard layout has it is the backend's question.
    """
    key = name.strip().lower()
    if not key:
        msg = "empty key name"
        raise InputError(msg)
    key = ALIASES.get(key, key)
    if key in NAMED_KEYS:
        return key
    if len(key) == 1 and key.isprintable():
        return key
    msg = f"unknown key name {name!r}"
    raise InputError(msg)
