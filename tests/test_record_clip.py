"""The recorder's hotkey handling, driven directly so no key ever has to be pressed."""

import time

from pynput import keyboard

from engine.clips import MARKER_BITE, MARKER_CAST, MARKER_LANDED
from tools.record_clip import MarkerListener


def test_marker_keys_are_timestamped_against_t0():
    t0 = time.monotonic() - 5.0
    listener = MarkerListener(t0)

    listener._on_press(keyboard.Key.f7)
    listener._on_press(keyboard.Key.f8)
    listener._on_press(keyboard.Key.f9)

    pending = listener.drain()
    assert [kind for _t, kind, _label in pending] == [MARKER_CAST, MARKER_LANDED, MARKER_BITE]
    assert all(4.9 < t < 6.0 for t, _kind, _label in pending)
    assert listener.drain() == []


def test_stop_and_discard_keys():
    listener = MarkerListener(time.monotonic())
    listener._on_press(keyboard.Key.f10)
    assert listener.stop_requested
    assert not listener.discard_requested

    other = MarkerListener(time.monotonic())
    other._on_press(keyboard.Key.esc)
    assert other.stop_requested

    discard = MarkerListener(time.monotonic())
    discard._on_press(keyboard.Key.f12)
    assert discard.discard_requested


def test_other_keys_are_ignored():
    listener = MarkerListener(time.monotonic())
    listener._on_press(keyboard.KeyCode.from_char("b"))
    listener._on_press(keyboard.Key.space)
    listener._on_press(None)
    assert listener.drain() == []
    assert not listener.stop_requested
