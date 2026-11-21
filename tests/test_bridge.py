"""The FishBridge strip decoder, against strips painted the way the Lua addon paints them."""

import numpy as np
import pytest

from engine.bridge import CELL_PX, STALE_AFTER_S, BridgeReader, BridgeState, decode, encode

FULL = BridgeState(
    heartbeat=42,
    free_slots=17,
    total_slots=80,
    in_combat=False,
    pole_equipped=True,
    lure_active=True,
    lure_remaining_s=540.0,
    channelling=True,
    channel_s=30,
)


def test_round_trip():
    assert decode(encode(FULL)) == FULL


def test_unknown_readings_stay_unknown():
    """A reading the addon could not make (a secret value, say) decodes to None, not False."""
    partial = BridgeState(heartbeat=1, in_combat=True)
    decoded = decode(encode(partial))
    assert decoded.in_combat is True
    assert decoded.free_slots is None
    assert decoded.pole_equipped is None
    assert decoded.lure_active is None
    assert decoded.channel_s is None


def test_small_colour_drift_is_tolerated():
    strip = encode(FULL).astype(np.int16)
    rng = np.random.default_rng(0)
    strip += rng.integers(-3, 4, strip.shape, dtype=np.int16)
    decoded = decode(np.clip(strip, 0, 255).astype(np.uint8))
    assert decoded.in_combat is False
    assert decoded.pole_equipped is True
    assert decoded.free_slots in (14, 15, 16, 17, 18, 19, 20)  # raw byte, drift shows


def test_cells_are_sampled_at_their_centre():
    """Blended edges from the game's rendering must not matter."""
    strip = encode(FULL)
    for index in range(1, 4):
        strip[:, index * CELL_PX] = (128, 128, 128)  # smear every left edge
    assert decode(strip) == FULL


@pytest.mark.parametrize(
    "picture",
    [
        np.zeros((4, 16, 3), np.uint8),  # nothing drawn
        np.full((4, 16, 3), 200, np.uint8),  # some bright UI
        np.zeros((2, 16, 3), np.uint8),  # too small
    ],
)
def test_no_strip_means_no_state(picture):
    assert decode(picture) is None


def test_wrong_version_is_rejected():
    strip = encode(FULL)
    strip[:, 3 * CELL_PX : 4 * CELL_PX, 1] = 2 * 16  # green of cell 3 = version 2
    assert decode(strip) is None


def test_bags_full():
    assert BridgeState(heartbeat=0, free_slots=0, total_slots=80).bags_full
    assert not BridgeState(heartbeat=0, free_slots=1, total_slots=80).bags_full
    assert not BridgeState(heartbeat=0).bags_full  # unknown is not full


def test_reader_goes_stale_when_the_heartbeat_stops():
    frames = [
        encode(BridgeState(heartbeat=beat, free_slots=5, total_slots=20)) for beat in (1, 2, 3)
    ]
    beats = iter(frames + [frames[-1]] * 100)
    reader = BridgeReader(grab=lambda: next(beats))

    assert reader.poll(0.0).heartbeat == 1
    assert reader.poll(0.3).heartbeat == 2
    assert reader.poll(0.6).heartbeat == 3
    t = 0.6
    while t < 0.6 + STALE_AFTER_S - 0.05:  # frozen, but not for long yet
        t += 0.25
        if t < 0.6 + STALE_AFTER_S:
            assert reader.poll(t) is not None
    assert reader.poll(0.6 + STALE_AFTER_S + 0.3) is None


def test_reader_survives_a_failing_grab():
    def broken():
        raise OSError("screen gone")

    assert BridgeReader(grab=broken).poll(0.0) is None


def test_reader_only_grabs_at_its_interval():
    calls = []

    def grab():
        calls.append(1)
        return encode(FULL)

    reader = BridgeReader(grab=grab)
    for step in range(10):
        reader.poll(step * 0.01)  # ten ticks inside 0.1 s
    assert len(calls) == 1


@pytest.mark.parametrize("factor", [2, 3])
def test_strip_rendered_larger_is_read(factor):
    """A Retina Mac whose game renders at half resolution shows every cell twice as big."""
    big = np.repeat(np.repeat(encode(FULL), factor, axis=0), factor, axis=1)
    if factor == 3:
        assert decode(big) is None  # not a size the addon can produce
    else:
        assert decode(big) == FULL


def test_strip_captured_at_half_resolution_is_read():
    small = encode(FULL)[::2, ::2]
    assert decode(small) == FULL


def test_a_larger_grab_still_reads_a_normal_strip():
    """The reader grabs room for the largest cells; a 1x strip sits in its corner."""
    canvas = np.full((CELL_PX * 2, CELL_PX * 8, 3), 30, dtype=np.uint8)
    strip = encode(FULL)
    canvas[: strip.shape[0], : strip.shape[1]] = strip
    assert decode(canvas) == FULL
