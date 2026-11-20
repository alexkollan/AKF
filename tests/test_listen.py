"""The live listener's pieces that do not need audio: the meter, the gate, the key match.

The meter regression is the one the first in-game run hit: a status line wider than the
console wraps, and every in-place redraw becomes a new row of output.
"""

import pytest

from engine import clock
from engine.audio import DetectorLevel
from tools.listen import KeyGate, KeyWatcher, meter


@pytest.mark.parametrize("width", [40, 60, 80, 100, 160])
@pytest.mark.parametrize("level_db", [-120.0, -63.4, -36.0, 0.0])
def test_meter_always_fits_the_console(width, level_db):
    level = DetectorLevel(t=1.0, level_db=level_db, floor_db=-60.0, threshold_db=-49.1, armed=True)
    assert len(meter(level, width)) <= width


def test_meter_marks_floor_and_threshold():
    level = DetectorLevel(t=1.0, level_db=-70.0, floor_db=-60.0, threshold_db=-49.0, armed=True)
    line = meter(level, 100)
    assert ":" in line
    assert "|" in line
    assert "-70 dB" in line


def test_gate_is_open_before_the_first_press():
    assert KeyGate(arm_after=5.0).is_open(0.0)


def test_gate_closes_on_a_press_and_reopens_after_the_delay():
    gate = KeyGate(arm_after=5.0)
    gate.press(10.0)
    assert not gate.is_open(10.0)
    assert not gate.is_open(14.9)
    assert gate.is_open(15.0)


def test_gate_closes_after_a_bite_until_the_next_press():
    gate = KeyGate(arm_after=5.0)
    gate.press(10.0)
    gate.hooked()
    assert not gate.is_open(30.0)  # the bot hooked; it waits for the loot/recast press
    gate.press(31.0)
    assert gate.is_open(36.0)


def test_since_press_text():
    gate = KeyGate(arm_after=5.0)
    assert gate.since_press(3.0) == ""
    gate.press(10.0)
    assert gate.since_press(12.5).strip() == "2.5s after key"


def test_watcher_stamps_each_press_against_its_origin():
    watcher = KeyWatcher("]", origin=clock.now() - 5.0)
    watcher.pressed()
    watcher.pressed()
    presses = watcher.drain()
    assert len(presses) == 2
    assert all(4.9 < t < 6.0 for t in presses)
    assert watcher.drain() == []
