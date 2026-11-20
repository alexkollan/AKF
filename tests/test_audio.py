"""Audio plumbing that does not need a sound card.

Device enumeration and the live loopback stream are marked ``needs_game`` in spirit: they
need real hardware, so they stay out of the normal run and are exercised by
``tools.record_clip --list-devices`` instead.
"""

import numpy as np
import pytest

from engine.audio import AudioChunk, AudioError, WavWriter, read_wav

RATE = 48000


def test_chunk_mono_averages_channels():
    stereo = np.array([[32767, -32768], [0, 0], [16384, 16384]], dtype=np.int16)
    mono = AudioChunk(t=0.0, samples=stereo).mono()
    assert mono.dtype == np.float32
    assert mono[1] == pytest.approx(0.0)
    assert mono[2] == pytest.approx(0.5, abs=1e-3)
    # The first pair is +full scale and -full scale, which cancel to roughly zero.
    assert abs(mono[0]) < 0.01


def test_chunk_mono_passes_through_single_channel():
    mono_in = np.array([0, 16384, -16384], dtype=np.int16)
    mono = AudioChunk(t=0.0, samples=mono_in).mono()
    assert mono == pytest.approx([0.0, 0.5, -0.5], abs=1e-3)


def test_chunk_frames_counts_samples_not_bytes():
    stereo = np.zeros((480, 2), np.int16)
    assert AudioChunk(t=0.0, samples=stereo).frames == 480


def test_wav_round_trip(tmp_path):
    path = tmp_path / "a.wav"
    block = (np.random.default_rng(0).integers(-3000, 3000, (1000, 2))).astype(np.int16)

    with WavWriter(path, channels=2, sample_rate=RATE) as writer:
        writer.append(block)
        writer.append(block)
        assert writer.frames_written == 2000

    samples, rate = read_wav(path)
    assert rate == RATE
    assert samples.shape == (2000, 2)
    assert np.array_equal(samples[:1000], block)


def test_wav_mono(tmp_path):
    path = tmp_path / "m.wav"
    block = np.arange(-500, 500, dtype=np.int16)
    with WavWriter(path, channels=1, sample_rate=RATE) as writer:
        writer.append(block)

    samples, _rate = read_wav(path)
    assert samples.ndim == 1
    assert np.array_equal(samples, block)


def test_read_wav_rejects_other_sample_widths(tmp_path):
    import wave

    path = tmp_path / "wide.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(4)
        handle.setframerate(RATE)
        handle.writeframes(b"\x00" * 16)

    with pytest.raises(AudioError, match="32-bit"):
        read_wav(path)
