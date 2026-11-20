"""The live listener's pieces that do not need audio: the meter, the gate, the key match.

The meter regression is the one the first in-game run hit: a status line wider than the
console wraps, and every in-place redraw becomes a new row of output.
"""

import pytest
from pynput import keyboard

from engine.audio import DetectorLevel
from tools.listen import KeyGate, KeyWatcher, meter

VK_OEM_6 = 0xDD  # the "]" key


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


def test_watcher_matches_the_key_by_virtual_key_code():
    watcher = KeyWatcher("]", origin=0.0)
    assert watcher._matches(keyboard.KeyCode.from_vk(VK_OEM_6))
    assert not watcher._matches(keyboard.KeyCode.from_vk(0x42))  # B
    assert not watcher._matches(keyboard.Key.space)
    assert not watcher._matches(None)


def test_watcher_counts_one_press_per_hold():
    watcher = KeyWatcher("]", origin=0.0)
    key = keyboard.KeyCode.from_vk(VK_OEM_6)
    for _ in range(4):  # auto-repeat while held
        watcher._down(key)
    watcher._up(key)
    watcher._down(key)
    assert len(watcher.drain()) == 2
    assert watcher.drain() == []
