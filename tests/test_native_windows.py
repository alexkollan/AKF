"""The Windows backend's key mapping and keyboard hook, driven without pressing anything.

The hook filter is what keeps the bot's keys (and the recorder's F7-F12) away from WoW, so
these pin both halves: a bound key fires once per press *and* is suppressed when swallowing,
and every other key passes through untouched.
"""

import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")

if sys.platform == "win32":
    from engine.hotkeys import GlobalHotkeys
    from engine.input import InputError
    from engine.native.windows.hotkeys import WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN
    from engine.native.windows.keyboard_mouse import scancode, virtual_key
    from tools.record_clip import MarkerListener

VK_B, VK_F9, VK_F10 = 0x42, 0x78, 0x79
VK_OEM_6 = 0xDD  # the "]" key


class Suppressed(Exception):
    """Stands in for pynput's internal SuppressException."""


def test_letters_and_digits_map_by_position():
    assert virtual_key("a") == 0x41
    assert virtual_key("Z") == 0x5A
    assert virtual_key("7") == 0x37


def test_named_keys():
    assert virtual_key("f8") == virtual_key("F8") == 0x77
    assert virtual_key("shift") == virtual_key("lshift")
    assert virtual_key("esc") == virtual_key("escape")


def test_unknown_key_is_an_error():
    with pytest.raises(InputError):
        virtual_key("wiggle")


@pytest.mark.parametrize("key", ["a", "1", "f8", "space", "numpad5", "up", "delete", "]"])
def test_every_bindable_key_has_a_scancode(key):
    code, _extended = scancode(key)
    assert code > 0


@pytest.mark.parametrize("key", ["up", "down", "left", "right", "delete", "home", "numpadenter"])
def test_navigation_keys_are_extended(key):
    _code, extended = scancode(key)
    assert extended, f"{key} needs the E0 prefix or the game reads the numpad twin"


def test_letters_are_not_extended():
    assert scancode("a")[1] is False


def hook_of(hotkeys, monkeypatch):
    hook = hotkeys._hook

    def suppress():
        raise Suppressed

    monkeypatch.setattr(hook._listener, "suppress_event", suppress)
    return hook


def send(hook, vk: int, msg: int = WM_KEYDOWN) -> bool:
    """Feed one event; returns True when it was suppressed."""
    try:
        assert hook._filter(msg, SimpleNamespace(vkCode=vk)) is True
    except Suppressed:
        return True
    return False


def test_hotkeys_fire_once_per_press_and_are_swallowed(monkeypatch):
    calls = []
    hook = hook_of(GlobalHotkeys({"<f9>": lambda: calls.append("f9")}), monkeypatch)

    assert send(hook, VK_F9, WM_KEYDOWN)
    assert send(hook, VK_F9, WM_KEYDOWN)  # auto-repeat
    assert send(hook, VK_F9, WM_KEYUP)
    assert calls == ["f9"]
    assert not send(hook, VK_B)  # B passes through


def test_syskeydown_counts_as_a_press(monkeypatch):
    """F10 arrives as WM_SYSKEYDOWN on Windows, because it is the menu key."""
    calls = []
    hook = hook_of(GlobalHotkeys({"f10": lambda: calls.append("f10")}), monkeypatch)
    assert send(hook, VK_F10, WM_SYSKEYDOWN)
    assert calls == ["f10"]


def test_watching_without_swallowing_lets_the_key_through(monkeypatch):
    calls = []
    hook = hook_of(GlobalHotkeys({"]": lambda: calls.append("]")}, swallow=False), monkeypatch)
    for _ in range(4):  # auto-repeat while held
        assert not send(hook, VK_OEM_6, WM_KEYDOWN)
    assert not send(hook, VK_OEM_6, WM_KEYUP)
    assert not send(hook, VK_OEM_6, WM_KEYDOWN)
    assert calls == ["]", "]"]


def test_recorder_keys_become_markers_and_are_swallowed(monkeypatch):
    listener = MarkerListener(0.0)
    hook = hook_of(listener._hotkeys, monkeypatch)
    for vk in (0x76, 0x77, 0x78):  # F7 F8 F9
        assert send(hook, vk, WM_KEYDOWN)
        assert send(hook, vk, WM_KEYUP)
    assert [kind for _t, kind, _label in listener.drain()] == ["cast", "landed", "bite"]
    assert not send(hook, VK_B)
    assert not listener.stop_requested
