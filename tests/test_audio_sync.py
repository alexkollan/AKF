"""Audio/video alignment across silence — the bug a real recording surfaced.

WASAPI loopback stops delivering data while nothing plays. These tests pin the two pieces
that keep a clip's audio on the same clock as its video anyway: chunk timestamps that
re-anchor after a gap, and a writer that places audio at its timestamp.
"""

import numpy as np
import pytest

from engine.audio import GAP_TOLERANCE_S, stamp_chunk
from engine.clips import ClipWriter, load_clip

RATE = 48000
CHUNK = 960  # 20 ms


def test_continuous_audio_uses_the_sample_count():
    # The 5th chunk, arriving right on time: exact sample-based time, no clock jitter.
    t = stamp_chunk(frames_seen=4 * CHUNK, frame_count=CHUNK, rate=RATE, arrived=0.1)
    assert t == pytest.approx(0.08)


def test_delivery_jitter_does_not_move_the_timestamp():
    late = 0.1 + GAP_TOLERANCE_S / 2
    t = stamp_chunk(frames_seen=4 * CHUNK, frame_count=CHUNK, rate=RATE, arrived=late)
    assert t == pytest.approx(0.08)


def test_a_silent_gap_reanchors_to_the_wall_clock():
    """Ten seconds of silence, then a splash: it must be stamped at ~10 s, not 0.08 s."""
    t = stamp_chunk(frames_seen=4 * CHUNK, frame_count=CHUNK, rate=RATE, arrived=10.0)
    assert t == pytest.approx(10.0 - CHUNK / RATE)


def test_first_chunk_after_starting_in_silence():
    t = stamp_chunk(frames_seen=0, frame_count=CHUNK, rate=RATE, arrived=3.0)
    assert t == pytest.approx(3.0 - CHUNK / RATE)


def _frames(writer: ClipWriter, seconds: float, fps: int = 30) -> None:
    for index in range(int(seconds * fps) + 1):
        writer.add_frame(index / fps, np.zeros((8, 8, 3), np.uint8))


def test_writer_fills_gaps_with_silence(tmp_path):
    writer = ClipWriter(tmp_path / "gap", audio_rate=RATE, audio_channels=2, with_audio=True)
    _frames(writer, 2.0)
    tone = np.full((CHUNK, 2), 1000, np.int16)
    writer.add_audio(tone, at=0.0)
    writer.add_audio(tone, at=1.5)  # 1.48 s of nothing in between
    clip = writer.close()

    samples, _rate = clip.read_audio()
    second_start = int(1.5 * RATE)
    assert np.all(samples[:CHUNK] == 1000)
    assert np.all(samples[CHUNK:second_start] == 0)
    assert np.all(samples[second_start : second_start + CHUNK] == 1000)


def test_writer_appends_when_chunks_are_contiguous(tmp_path):
    writer = ClipWriter(tmp_path / "flow", audio_rate=RATE, audio_channels=1, with_audio=True)
    _frames(writer, 0.1)
    for index in range(5):
        writer.add_audio(np.full(CHUNK, index + 1, np.int16), at=index * CHUNK / RATE)
    clip = writer.close()

    samples, _rate = clip.read_audio()
    assert samples.shape == (5 * CHUNK,)
    assert list(samples[::CHUNK]) == [1, 2, 3, 4, 5]


def test_silent_recording_still_gets_a_full_length_wav(tmp_path):
    """The exact case from the probe: 6 s recorded, loopback delivered zero samples."""
    writer = ClipWriter(tmp_path / "quiet", audio_rate=RATE, audio_channels=2, with_audio=True)
    writer.set_audio_start(0.11)
    _frames(writer, 6.0)
    clip = writer.close()

    assert clip.audio_path.is_file()
    samples, _rate = clip.read_audio()
    assert len(samples) == round((6.0 - 0.11) * RATE)
    assert load_clip(clip.path).meta.audio.frame_count == len(samples)


def test_video_only_clip_has_no_wav(tmp_path):
    writer = ClipWriter(tmp_path / "mute")
    _frames(writer, 1.0)
    assert not writer.close().audio_path.exists()
