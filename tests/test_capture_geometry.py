"""Geometry maths, which is where a two-monitor setup breaks first.

Nothing here needs the game or a capture device.
"""

import pytest

from engine.capture import CaptureError, Rect, roi_to_screen
from engine.config import RoiFraction


def test_rect_dimensions():
    rect = Rect(10, 20, 110, 170)
    assert (rect.width, rect.height) == (100, 150)
    assert rect.center == (60, 95)
    assert rect.contains(10, 20)
    assert not rect.contains(110, 170)


def test_rect_translate_round_trip():
    rect = Rect(0, 0, 100, 100)
    assert rect.translated(-1920, 0).translated(1920, 0) == rect


def test_clamp_keeps_overlap():
    client = Rect(100, 100, 500, 400)
    assert Rect(50, 50, 300, 300).clamped_to(client) == Rect(100, 100, 300, 300)


def test_clamp_rejects_disjoint():
    with pytest.raises(CaptureError):
        Rect(0, 0, 50, 50).clamped_to(Rect(100, 100, 200, 200))


def test_roi_centre_default():
    client = Rect(0, 0, 1000, 1000)
    roi = roi_to_screen(client, RoiFraction())
    assert roi == Rect(200, 200, 800, 800)


def test_roi_on_negative_monitor():
    """The second monitor sits at a negative x; the ROI has to follow it."""
    client = Rect(-1920, 0, -920, 800)
    roi = roi_to_screen(client, RoiFraction(x=0.5, y=0.0, w=0.5, h=0.5))
    assert roi == Rect(-1420, 0, -920, 400)
    assert roi.width == 500


def test_roi_is_clamped_to_client():
    client = Rect(0, 0, 400, 400)
    roi = roi_to_screen(client, RoiFraction(x=0.9, y=0.9, w=0.5, h=0.5))
    assert roi.right == 400
    assert roi.bottom == 400


def test_roi_never_degenerates():
    client = Rect(0, 0, 1920, 1080)
    roi = roi_to_screen(client, RoiFraction(x=0.0, y=0.0, w=0.011, h=0.011))
    assert roi.width >= 1
    assert roi.height >= 1
