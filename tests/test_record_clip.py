"""The recorder's hotkey filter, driven directly so no key ever has to be pressed.

The filter is what keeps F8-F12 away from WoW (where they toggle bags), so the tests pin
two things: owned keys become markers *and* get suppressed, and every other key passes
through untouched.
"""

from types import SimpleNamespace

import pytest

from engine import clock
from engine.clips import MARKER_BITE, MARKER_CAST, MARKER_LANDED
from tools.record_clip import (
    VK_F7,
    VK_F8,
    VK_F9,
    VK_F10,
    VK_F12,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    MarkerListener,
)

VK_B = 0x42


class Suppressed(Exception):
    """Stands in for pynput's internal SuppressException."""


@pytest.fixture
def make_listener(monkeypatch):
    def build(t0: float | None = None) -> MarkerListener:
        listener = MarkerListener(clock.now() if t0 is None else t0)

        def suppress() -> None:
            raise Suppressed

        monkeypatch.setattr(listener._listener, "suppress_event", suppress)
        return listener

    return build


def press(listener: MarkerListener, vk: int, msg: int = WM_KEYDOWN) -> bool:
    """Feed one event; returns True when it was suppressed."""
    try:
        listener._filter(msg, SimpleNamespace(vkCode=vk))
    except Suppressed:
        return True
    return False


def tap(listener: MarkerListener, vk: int) -> bool:
    down = press(listener, vk, WM_KEYDOWN)
    up = press(listener, vk, WM_KEYUP)
    return down and up


def test_marker_keys_are_recorded_and_swallowed(make_listener):
    listener = make_listener(clock.now() - 5.0)

    assert tap(listener, VK_F7)
    assert tap(listener, VK_F8)
    assert tap(listener, VK_F9)

    pending = listener.drain()
    assert [kind for _t, kind, _label in pending] == [MARKER_CAST, MARKER_LANDED, MARKER_BITE]
    assert all(4.9 < t < 6.0 for t, _kind, _label in pending)
    assert listener.drain() == []


def test_auto_repeat_is_one_marker(make_listener):
    listener = make_listener()
    for _ in range(5):  # holding F9 sends repeated keydowns
        assert press(listener, VK_F9, WM_KEYDOWN)
    assert press(listener, VK_F9, WM_KEYUP)
    assert len(listener.drain()) == 1

    tap(listener, VK_F9)  # released, so the next press counts again
    assert len(listener.drain()) == 1


def test_syskeydown_counts_as_a_press(make_listener):
    """F10 arrives as WM_SYSKEYDOWN on Windows, because it is the menu key."""
    listener = make_listener()
    assert press(listener, VK_F10, WM_SYSKEYDOWN)
    assert listener.stop_requested


def test_stop_and_discard(make_listener):
    stop = make_listener()
    assert tap(stop, VK_F10)
    assert stop.stop_requested
    assert not stop.discard_requested

    discard = make_listener()
    assert tap(discard, VK_F12)
    assert discard.discard_requested


def test_other_keys_pass_through(make_listener):
    listener = make_listener()
    assert listener._filter(WM_KEYDOWN, SimpleNamespace(vkCode=VK_B)) is True
    assert listener._filter(WM_KEYUP, SimpleNamespace(vkCode=VK_B)) is True
    assert listener.drain() == []
    assert not listener.stop_requested
