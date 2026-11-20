"""Round-trip the fixture format: write a synthetic clip, read it back, check it matches.

These exercise the real FFV1 writer and the real wav writer, because the thing worth
testing is that a clip written on this machine reads back with its pixels and its timing
intact. If that ever stops being true, every detector threshold measured from fixtures is
measured against the wrong data.
"""

import json

import numpy as np
import pytest

from engine.clips import (
    MARKER_BITE,
    MARKER_CAST,
    MARKER_LANDED,
    ClipError,
    ClipGeometry,
    ClipWriter,
    FrameReader,
    Marker,
    list_clips,
    load_clip,
)
from engine.config import RoiFraction

WIDTH, HEIGHT = 64, 48
FPS = 30
FRAMES = 60
RATE = 48000
CHANNELS = 2


def make_frame(index: int) -> np.ndarray:
    """A frame whose pixels encode its index, so lossy compression would be obvious."""
    frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    frame[:, :] = (index * 3 % 256, 40, 200)
    frame[0:4, 0:4] = index % 256
    return frame


def make_audio(seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    wave = np.sin(2 * np.pi * 440 * t) * 0.2
    wave[int(RATE * 1.0) : int(RATE * 1.05)] *= 4  # a "splash" at t = 1.0s
    mono = (wave * 32767).astype(np.int16)
    return np.stack([mono, mono], axis=1)


@pytest.fixture
def clip(tmp_path, request):
    codec = getattr(request, "param", "ffv1")
    writer = ClipWriter(
        tmp_path / "clip",
        codec=codec,
        fps_target=FPS,
        audio_rate=RATE,
        audio_channels=CHANNELS,
        audio_device_name="test loopback",
        with_audio=True,
        geometry=ClipGeometry(
            client_rect=(0, 0, 1920, 1080),
            roi_screen=(100, 100, 100 + WIDTH, 100 + HEIGHT),
            roi_fraction=RoiFraction(),
            monitor_device="\\\\.\\DISPLAY1",
            process_name="WowClassic.exe",
        ),
        tags=["night", "sea"],
        notes="synthetic",
    )
    writer.set_audio_start(0.02)
    for index in range(FRAMES):
        writer.add_frame(index / FPS, make_frame(index))
    writer.add_audio(make_audio())
    writer.add_marker(0.1, MARKER_CAST)
    writer.add_marker(0.6, MARKER_LANDED)
    writer.add_marker(1.25, MARKER_BITE)
    writer.add_marker(1.4, MARKER_CAST)
    return writer.close()


def test_meta_round_trip(clip):
    assert clip.meta.video.codec == "ffv1"
    assert (clip.meta.video.width, clip.meta.video.height) == (WIDTH, HEIGHT)
    assert clip.meta.video.frame_count == FRAMES
    assert clip.meta.audio.sample_rate == RATE
    assert clip.meta.audio.channels == CHANNELS
    assert clip.meta.audio.start_offset == pytest.approx(0.02)
    assert clip.meta.tags == ["night", "sea"]
    assert clip.meta.geometry.process_name == "WowClassic.exe"
    assert clip.meta.duration == pytest.approx((FRAMES - 1) / FPS)


def test_frame_times_are_recorded_not_assumed(clip):
    assert len(clip.frame_times) == FRAMES
    assert clip.frame_times[0] == pytest.approx(0.0)
    assert clip.frame_times[-1] == pytest.approx((FRAMES - 1) / FPS)


@pytest.mark.parametrize("clip", ["ffv1", "png"], indirect=True)
def test_frames_are_lossless(clip):
    """The whole fixture format rests on this: what went in comes back out."""
    for index, (timestamp, frame) in enumerate(clip.frames()):
        # frames.json stores microseconds, which is all a 30 fps clip can use.
        assert timestamp == pytest.approx(index / FPS, abs=1e-6)
        assert np.array_equal(frame, make_frame(index)), f"frame {index} was altered"


def test_frame_index_lookup(clip):
    assert clip.frame_index_at(0.0) == 0
    assert clip.frame_index_at(1.0) == 30
    assert clip.frame_index_at(0.99 / FPS) == 0
    assert clip.frame_index_at(999.0) == FRAMES - 1


def test_frame_reader_random_access(clip):
    with FrameReader(clip) as reader:
        assert np.array_equal(reader.read(40), make_frame(40))
        assert np.array_equal(reader.read(5), make_frame(5))  # backwards seek
        assert np.array_equal(reader.read(6), make_frame(6))  # sequential after a seek
        assert np.array_equal(reader.read(FRAMES - 1), make_frame(FRAMES - 1))


def test_frame_reader_rejects_out_of_range(clip):
    with FrameReader(clip) as reader, pytest.raises(ClipError):
        reader.read(FRAMES)


def test_audio_round_trip(clip):
    samples, rate = clip.read_audio()
    assert rate == RATE
    assert samples.shape == (RATE * 2, CHANNELS)
    assert np.array_equal(samples, make_audio())


def test_audio_chunks_match_the_live_stream_shape(clip):
    chunks = list(clip.audio_chunks(chunk_ms=20))
    expected_size = RATE * 20 // 1000
    assert all(chunk.shape == (expected_size, CHANNELS) for _t, chunk in chunks)
    # Timestamps start at the recorded offset and advance by one chunk each.
    assert chunks[0][0] == pytest.approx(0.02)
    assert chunks[1][0] == pytest.approx(0.04)


def test_audio_chunk_timestamps_find_the_splash(clip):
    """A timestamp has to point at the sound it carries, or the markers mean nothing."""
    loudest = max(clip.audio_chunks(10), key=lambda item: np.abs(item[1]).max())
    assert loudest[0] == pytest.approx(1.0 + 0.02, abs=0.02)


def test_casts_are_segmented_by_markers(clip):
    casts = clip.casts()
    assert len(casts) == 2
    first, second = casts
    assert first.start == pytest.approx(0.1)
    assert first.end == pytest.approx(1.4)
    assert first.landed == pytest.approx(0.6)
    assert first.bites == pytest.approx((1.25,))
    assert second.landed is None
    assert second.bites == ()


def test_casts_fall_back_to_landed_markers(tmp_path):
    writer = ClipWriter(tmp_path / "nocast", fps_target=FPS)
    for index in range(10):
        writer.add_frame(index / FPS, make_frame(index))
    writer.add_marker(0.1, MARKER_LANDED)
    clip = writer.close()
    assert len(clip.casts()) == 1


def test_markers_can_be_corrected_and_saved(clip):
    bite = clip.markers_of(MARKER_BITE)[0]
    clip.markers.remove(bite)
    clip.markers.append(Marker(t=1.0, kind=MARKER_BITE, note="moved onto the splash"))
    clip.save_markers()

    reloaded = load_clip(clip.path)
    assert [m.t for m in reloaded.markers_of(MARKER_BITE)] == [1.0]
    assert [m.t for m in reloaded.markers] == sorted(m.t for m in reloaded.markers)


def test_nearest_marker(clip):
    assert clip.nearest_marker(0.62).kind == MARKER_LANDED
    assert clip.nearest_marker(5.0) is None


def test_markers_json_is_hand_editable(clip):
    payload = json.loads((clip.path / "markers.json").read_text(encoding="utf-8"))
    assert payload[0] == {"t": 0.1, "kind": MARKER_CAST, "note": ""}


def test_list_clips(clip):
    assert list_clips(clip.path.parent) == [clip.path]
    assert list_clips(clip.path / "nope") == []


def test_load_rejects_a_non_clip(tmp_path):
    with pytest.raises(ClipError):
        load_clip(tmp_path)


def test_load_rejects_inconsistent_metadata(clip):
    meta_path = clip.path / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["video"]["frame_count"] = FRAMES + 5
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ClipError, match="frames"):
        load_clip(clip.path)


def test_writer_refuses_a_non_empty_directory(clip):
    with pytest.raises(ClipError):
        ClipWriter(clip.path)


def test_writer_rejects_a_frame_size_change(tmp_path):
    writer = ClipWriter(tmp_path / "sizes", fps_target=FPS)
    writer.add_frame(0.0, make_frame(0))
    with pytest.raises(ClipError, match="size changed"):
        writer.add_frame(0.1, np.zeros((HEIGHT * 2, WIDTH, 3), np.uint8))
    writer.discard()


def test_failed_recording_is_discarded(tmp_path):
    """A crash mid-recording must not leave a half-written clip in the fixture set."""
    path = tmp_path / "boom"
    with pytest.raises(RuntimeError), ClipWriter(path, fps_target=FPS) as writer:
        writer.add_frame(0.0, make_frame(0))
        msg = "recording blew up"
        raise RuntimeError(msg)
    assert not path.exists()
