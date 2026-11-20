"""The recorder's marker keys, driven directly so no key ever has to be pressed.

That the owned keys are also *swallowed* (kept away from WoW, where F8-F12 toggle bags) is
the hook's job; ``test_native_windows.py`` pins it for the recorder's own keys.
"""

from engine import clock
from engine.clips import MARKER_BITE, MARKER_CAST, MARKER_LANDED
from tools.record_clip import DISCARD_KEY, OWNED_KEYS, STOP_KEY, MarkerListener


def test_the_recorder_owns_f7_to_f12_except_f11():
    assert OWNED_KEYS == {"f7", "f8", "f9", "f10", "f12"}


def test_marker_keys_are_recorded_in_order():
    listener = MarkerListener(clock.now())
    for key, at in (("f7", 1.0), ("f8", 2.5), ("f9", 9.0)):
        listener.handle(key, at)

    pending = listener.drain()
    assert pending == [
        (1.0, MARKER_CAST, "cast"),
        (2.5, MARKER_LANDED, "bobber landed"),
        (9.0, MARKER_BITE, "bite"),
    ]
    assert listener.drain() == []


def test_handlers_stamp_against_t0():
    listener = MarkerListener(clock.now() - 5.0)
    listener._handler("f9")()
    ((t, kind, _label),) = listener.drain()
    assert kind == MARKER_BITE
    assert 4.9 < t < 6.0


def test_stop_and_discard():
    stop = MarkerListener(clock.now())
    stop.handle(STOP_KEY, 1.0)
    assert stop.stop_requested
    assert not stop.discard_requested
    assert stop.drain() == []

    discard = MarkerListener(clock.now())
    discard.handle(DISCARD_KEY, 1.0)
    assert discard.discard_requested
