"""Where the desktop window opens, on the user's real layout: a 3440x1440 primary at (0,0)
and a 1920x1080 second monitor to its left at (-1920,0). No window is created here."""

import pytest

from engine.app import MIN_SIZE, TITLE, monitor_index_at, place_window
from engine.capture import MonitorInfo, Rect
from engine.config import WindowState

PRIMARY = MonitorInfo(
    index=0,
    device="\\\\.\\DISPLAY9",
    handle=1,
    rect=Rect(0, 0, 3440, 1440),
    work_rect=Rect(0, 0, 3440, 1392),
    is_primary=True,
)
SECOND = MonitorInfo(
    index=1,
    device="\\\\.\\DISPLAY10",
    handle=2,
    rect=Rect(-1920, 0, 0, 1080),
    work_rect=Rect(-1920, 0, 0, 1032),
    is_primary=False,
)
MONITORS = [PRIMARY, SECOND]


def test_window_title_is_akf():
    assert TITLE == "AKF"


def test_default_opens_centred_on_the_second_monitor():
    placement = place_window(WindowState(), MONITORS)
    assert SECOND.work_rect.contains(placement.x, placement.y)
    assert placement.x == -1920 + (1920 - 1280) // 2
    assert (placement.width, placement.height) == (1280, 860)


def test_remembered_position_is_reused_when_still_on_that_monitor():
    state = WindowState(monitor_index=1, x=-1800, y=50, width=1200, height=800)
    placement = place_window(state, MONITORS)
    assert (placement.x, placement.y) == (-1800, 50)


def test_a_position_off_the_chosen_monitor_is_recentred():
    state = WindowState(monitor_index=1, x=500, y=50)  # remembered on the primary
    placement = place_window(state, MONITORS)
    assert SECOND.work_rect.contains(placement.x + placement.width // 2, placement.y)


def test_window_never_exceeds_the_work_area():
    state = WindowState(monitor_index=1, width=5000, height=3000)
    placement = place_window(state, MONITORS)
    assert placement.width == 1920
    assert placement.height == 1032


def test_window_is_never_smaller_than_the_minimum():
    placement = place_window(WindowState(width=200, height=100), MONITORS)
    assert (placement.width, placement.height) == MIN_SIZE


def test_a_missing_monitor_falls_back_to_the_primary():
    placement = place_window(WindowState(monitor_index=2), MONITORS)
    assert PRIMARY.work_rect.contains(placement.x, placement.y)


@pytest.mark.parametrize(("x", "y", "index"), [(100, 100, 0), (-500, 300, 1), (-5000, 0, None)])
def test_monitor_index_at(x, y, index):
    assert monitor_index_at(x, y, MONITORS) == index
