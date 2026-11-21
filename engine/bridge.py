"""Read the FishBridge addon's colour strip off the screen.

The addon (``addon/FishBridge``) paints four solid cells in the top-left corner of the game
window. This module decodes them into a :class:`BridgeState`: free bag slots, combat,
whether a fishing pole is equipped, the lure and the current fishing channel. The layout is
documented at the top of ``FishBridge.lua`` and the two must change together;
:func:`encode` mirrors the Lua so the tests check both directions.

The addon is optional. When the strip is missing, unreadable or frozen (UI hidden with
Alt+Z, a loading screen), :class:`BridgeReader` reports ``None`` and the rules that depend
on it simply do not apply — the bot keeps fishing exactly as it does without the addon.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from engine import capture

logger = logging.getLogger(__name__)

CELL_PX = 4
CELLS = 4
#: Captured pixels per addon pixel that :func:`decode` tries, in order. The addon paints in
#: the game's render pixels; on Windows those are screen pixels (1). On a Retina Mac the
#: game may render at native resolution (1) or at half of it and be upscaled (2); a capture
#: at nominal resolution would halve the cells instead. The strip validates itself (magic
#: cell, version, end marker), so trying each size cannot mistake one for another.
CELL_SCALES: tuple[float, ...] = (1, 2, 0.5)
VERSION = 1
END_MARKER = 170

FLAG_COMBAT, FLAG_POLE, FLAG_LURE, FLAG_CHANNEL = 1, 2, 4, 8
VALID_BAGS, VALID_COMBAT, VALID_POLE, VALID_LURE, VALID_CHANNEL = 1, 2, 4, 8, 16

#: Colour channels can drift a little through the game's rendering; values are read with
#: this much tolerance, and every encoded step is far wider than it.
TOLERANCE = 35

READ_INTERVAL_S = 0.25
#: The addon bumps its heartbeat four times a second; this long without a change means
#: the strip is not being drawn (UI hidden, loading screen) and must not be trusted.
STALE_AFTER_S = 1.5


@dataclass(frozen=True, slots=True)
class BridgeState:
    """What the addon reported. A field is ``None`` when the addon could not read it."""

    heartbeat: int
    free_slots: int | None = None
    total_slots: int | None = None
    in_combat: bool | None = None
    pole_equipped: bool | None = None
    lure_active: bool | None = None
    lure_remaining_s: float | None = None
    channelling: bool | None = None
    channel_s: int | None = None

    @property
    def bags_full(self) -> bool:
        return (
            self.free_slots is not None
            and self.total_slots is not None
            and self.total_slots > 0
            and self.free_slots == 0
        )


def _cell_centre(index: int, cell: int = CELL_PX) -> tuple[int, int]:
    """(row, column) of the pixel sampled for a cell: its centre, away from edge blending."""
    return cell // 2, index * cell + cell // 2


def decode(strip: np.ndarray) -> BridgeState | None:
    """Decode a BGR image whose top-left corner is the strip; ``None`` if it is not there.

    Each size in :data:`CELL_SCALES` is tried until one reads as a valid strip.
    """
    if strip.ndim != 3:
        return None
    for scale in CELL_SCALES:
        state = _decode_at(strip, max(1, round(CELL_PX * scale)))
        if state is not None:
            return state
    return None


def _decode_at(strip: np.ndarray, cell: int) -> BridgeState | None:
    if strip.shape[0] < cell or strip.shape[1] < CELLS * cell:
        return None

    cells = []
    for index in range(CELLS):
        row, column = _cell_centre(index, cell)
        blue, green, red = (int(value) for value in strip[row, column, :3])
        cells.append((red, green, blue))

    magic, bags, state, check = cells
    if not (magic[0] >= 255 - TOLERANCE and magic[1] <= TOLERANCE and magic[2] >= 255 - TOLERANCE):
        return None
    if abs(check[2] - END_MARKER) > TOLERANCE // 2 or round(check[1] / 16) != VERSION:
        return None

    valid = round(check[0] / 8) & 0x1F
    flags = round(state[0] / 16) & 0x0F

    def flag(bit: int, valid_bit: int) -> bool | None:
        return bool(flags & bit) if valid & valid_bit else None

    lure_on = flag(FLAG_LURE, VALID_LURE)
    channelling = flag(FLAG_CHANNEL, VALID_CHANNEL)
    return BridgeState(
        heartbeat=bags[2],
        free_slots=bags[0] if valid & VALID_BAGS else None,
        total_slots=bags[1] if valid & VALID_BAGS else None,
        in_combat=flag(FLAG_COMBAT, VALID_COMBAT),
        pole_equipped=flag(FLAG_POLE, VALID_POLE),
        lure_active=lure_on,
        lure_remaining_s=float(state[1] * 10) if lure_on else (0.0 if lure_on is False else None),
        channelling=channelling,
        channel_s=state[2] if channelling else (0 if channelling is False else None),
    )


def encode(state: BridgeState) -> np.ndarray:
    """Paint a strip the way ``FishBridge.lua`` does, as a BGR image. For tests and tools."""

    def byte(value: float) -> int:
        return max(0, min(255, round(value)))

    flags = valid = 0
    for value, flag_bit, valid_bit in (
        (state.in_combat, FLAG_COMBAT, VALID_COMBAT),
        (state.pole_equipped, FLAG_POLE, VALID_POLE),
        (state.lure_active, FLAG_LURE, VALID_LURE),
        (state.channelling, FLAG_CHANNEL, VALID_CHANNEL),
    ):
        if value is not None:
            valid |= valid_bit
            if value:
                flags |= flag_bit
    if state.free_slots is not None:
        valid |= VALID_BAGS

    rgb = [
        (255, 0, 255),
        (byte(state.free_slots or 0), byte(state.total_slots or 0), state.heartbeat % 256),
        (flags * 16, byte((state.lure_remaining_s or 0) / 10), byte(state.channel_s or 0)),
        (valid * 8, VERSION * 16, END_MARKER),
    ]
    image = np.zeros((CELL_PX, CELLS * CELL_PX, 3), dtype=np.uint8)
    for index, (red, green, blue) in enumerate(rgb):
        image[:, index * CELL_PX : (index + 1) * CELL_PX] = (blue, green, red)
    return image


class BridgeReader:
    """Reads the strip from the game window a few times a second.

    ``poll`` is cheap to call every tick: it only grabs when ``READ_INTERVAL_S`` has passed
    and otherwise returns the last reading. A frozen heartbeat makes the reading ``None``.
    Construct it on the thread that polls it — the mss grabber is per-thread.
    """

    def __init__(
        self,
        window: capture.WindowInfo | None = None,
        *,
        grab: Callable[[], np.ndarray | None] | None = None,
    ) -> None:
        if grab is None:
            if window is None:
                msg = "BridgeReader needs a window or a grab function"
                raise ValueError(msg)
            grab = self._screen_grabber(window)
        self._grab = grab
        self._next_read = float("-inf")
        self._last: BridgeState | None = None
        self._beat: int | None = None
        self._beat_changed = float("-inf")

    def poll(self, now: float) -> BridgeState | None:
        if now >= self._next_read:
            self._next_read = now + READ_INTERVAL_S
            self._read(now)
        if self._last is None or now - self._beat_changed > STALE_AFTER_S:
            return None
        return self._last

    def _read(self, now: float) -> None:
        try:
            image = self._grab()
        except Exception as exc:  # capture hiccups must never stop a session
            logger.debug("bridge grab failed: %s", exc)
            image = None
        state = decode(image) if image is not None else None
        if state is not None and state.heartbeat != self._beat:
            self._beat = state.heartbeat
            self._beat_changed = now
        self._last = state

    @staticmethod
    def _screen_grabber(window: capture.WindowInfo) -> Callable[[], np.ndarray | None]:
        """Grab the client's top-left corner, big enough for the largest cell size tried.

        The region is in screen coordinates and the frame in captured pixels, so the size
        is divided by the monitor's scale (points on a Retina Mac, pixels elsewhere).
        """
        largest = CELL_PX * max(CELL_SCALES)
        scale = max(window.monitor.scale, 1e-6)
        width = math.ceil(CELLS * largest / scale)
        height = math.ceil(largest / scale)
        left, top = window.client_rect.left, window.client_rect.top
        region = capture.Rect(left, top, left + width, top + height)
        source = capture.MssSource(region, window.monitor, 0)
        return source.grab
