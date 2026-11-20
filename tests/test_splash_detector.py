"""The splash detector on synthetic audio.

Real thresholds get tuned on recorded fixtures (M3 metrics). These tests pin the behaviour
that must hold whatever the thresholds are: it measures level correctly, it judges against
the floor rather than absolute loudness, it ignores sound outside the band, it only fires
when armed, and the way audio is chunked never changes the answer.
"""

import numpy as np
import pytest

from engine.audio import AudioChunk, SplashDetector, from_dbfs, to_dbfs
from engine.config import AudioDetectorConfig

RATE = 48000


def tone(freq: float, seconds: float, dbfs: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    # A sine's RMS is amplitude / sqrt(2); size the amplitude so its RMS hits ``dbfs``.
    return np.sin(2 * np.pi * freq * t) * from_dbfs(dbfs) * np.sqrt(2)


def noise(seconds: float, dbfs: float, seed: int = 0) -> np.ndarray:
    """Band-limited-ish noise: white noise, which puts most of its energy in band."""
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, from_dbfs(dbfs), int(RATE * seconds))


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds))


def as_chunks(signal: np.ndarray, size: int = 480, t0: float = 0.0) -> list[AudioChunk]:
    """Cut a float signal into int16 stereo chunks, the shape loopback delivers."""
    pcm = np.clip(signal * 32767, -32768, 32767).astype(np.int16)
    stereo = np.stack([pcm, pcm], axis=1)
    return [
        AudioChunk(t=t0 + start / RATE, samples=stereo[start : start + size])
        for start in range(0, len(stereo), size)
    ]


def run(detector: SplashDetector, chunks: list[AudioChunk]) -> list[float]:
    """Feed every chunk; return the bite times."""
    return [event.t for chunk in chunks if (event := detector.feed(chunk)) is not None]


def armed(config: AudioDetectorConfig | None = None) -> SplashDetector:
    detector = SplashDetector(config or AudioDetectorConfig(), RATE)
    detector.arm()
    return detector


def test_level_reads_the_rms_of_an_in_band_sine():
    detector = SplashDetector(AudioDetectorConfig(), RATE)
    run(detector, as_chunks(tone(1000, 0.2, -20.0)))
    assert detector.last.level_db == pytest.approx(-20.0, abs=0.5)


def test_dbfs_round_trip():
    assert to_dbfs(from_dbfs(-37.5)) == pytest.approx(-37.5)
    assert to_dbfs(0.0) == pytest.approx(-120.0)


def test_splash_after_quiet_ambience_fires_once_at_the_right_time():
    signal = np.concatenate([noise(2.0, -45.0), noise(0.25, -20.0, seed=1), noise(1.0, -45.0)])
    bites = run(armed(), as_chunks(signal))
    assert len(bites) == 1
    assert bites[0] == pytest.approx(2.0, abs=0.03)  # within about one window


def test_splash_after_digital_silence_fires():
    """Process loopback is truly silent between sounds; the floor clamp must still allow
    a real splash through."""
    signal = np.concatenate([silence(1.0), noise(0.2, -30.0)])
    assert len(run(armed(), as_chunks(signal))) == 1


def test_whisper_after_digital_silence_does_not_fire():
    """Below floor_min_dbfs + trigger ratio, a sound over silence is not a bite."""
    signal = np.concatenate([silence(1.0), noise(0.2, -75.0)])
    assert run(armed(), as_chunks(signal)) == []


def test_a_small_rise_over_the_floor_is_not_a_bite():
    # trigger_ratio 3.5 is about +10.9 dB; +5 dB must not fire.
    signal = np.concatenate([noise(2.0, -40.0), noise(0.3, -35.0, seed=2)])
    assert run(armed(), as_chunks(signal)) == []


def test_threshold_is_relative_not_absolute():
    """Same +20 dB jump at two very different volumes: both fire."""
    for base in (-55.0, -25.0):
        signal = np.concatenate([noise(2.0, base), noise(0.2, base + 20.0, seed=3)])
        assert len(run(armed(), as_chunks(signal))) == 1, f"no bite at base {base} dBFS"


def test_sound_outside_the_band_is_ignored():
    signal = np.concatenate([silence(1.0), tone(9000, 0.3, -10.0)])
    assert run(armed(), as_chunks(signal)) == []


def test_disarmed_detector_measures_but_never_fires():
    detector = SplashDetector(AudioDetectorConfig(), RATE)
    signal = np.concatenate([noise(2.0, -45.0), noise(0.2, -15.0, seed=4)])
    assert run(detector, as_chunks(signal)) == []
    assert detector.last is not None
    assert detector.last.armed is False


def test_arming_mid_stream_catches_the_next_splash_only():
    detector = SplashDetector(AudioDetectorConfig(), RATE)
    first = np.concatenate([noise(1.5, -45.0), noise(0.2, -15.0, seed=5), noise(1.0, -45.0)])
    assert run(detector, as_chunks(first)) == []  # landing plop, detector not armed yet

    detector.arm()
    second = np.concatenate([noise(1.0, -45.0, seed=6), noise(0.2, -15.0, seed=7)])
    bites = run(detector, as_chunks(second, t0=2.7))
    assert bites == [pytest.approx(3.7, abs=0.03)]


def test_refractory_period_merges_one_splash_into_one_bite():
    signal = np.concatenate(
        [noise(2.0, -45.0), noise(0.1, -15.0, seed=8), noise(0.2, -45.0), noise(0.1, -15.0)]
    )
    assert len(run(armed(), as_chunks(signal))) == 1


def test_two_splashes_far_apart_are_two_bites():
    gap = noise(2.0, -45.0, seed=9)
    burst = noise(0.15, -15.0, seed=10)
    signal = np.concatenate([gap, burst, gap, burst])
    assert len(run(armed(), as_chunks(signal))) == 2


def test_a_burst_does_not_raise_the_floor():
    detector = armed()
    run(detector, as_chunks(noise(4.0, -45.0)))
    quiet_floor = detector.last.floor_db
    tail = np.concatenate([noise(0.3, -10.0, seed=11), noise(0.5, -45.0, seed=12)])
    run(detector, as_chunks(tail, t0=4.0))
    assert detector.last.floor_db == pytest.approx(quiet_floor, abs=1.5)


def test_floor_is_learned_when_ambience_starts_loud():
    """Regression: ambience above floor_min_dbfs * ratio from the first window used to keep
    every window out of the history, leaving the detector deaf for the whole session."""
    detector = armed()
    run(detector, as_chunks(noise(3.0, -25.0)))
    assert detector.last.floor_db > -40.0  # learned the ~-33 dB in-band ambience


def test_floor_follows_ambience_that_really_gets_louder():
    detector = armed()
    signal = np.concatenate([noise(4.0, -50.0), noise(6.0, -30.0, seed=13)])
    bites = run(detector, as_chunks(signal))
    assert len(bites) == 1  # the step itself is one onset, not a stream of bites
    assert detector.last.floor_db > -45.0


def test_no_bite_during_warmup():
    signal = noise(0.15, -10.0)  # loud from the very first window
    assert run(armed(), as_chunks(signal)) == []


@pytest.mark.parametrize("size", [100, 480, 960, 1024, 4800])
def test_chunk_size_does_not_change_the_answer(size):
    signal = np.concatenate([noise(2.0, -45.0), noise(0.2, -15.0, seed=12), noise(0.5, -45.0)])
    bites = run(armed(), as_chunks(signal, size=size))
    assert bites == [pytest.approx(2.0, abs=0.03)]


def test_a_hole_in_the_timestamps_restarts_the_window():
    detector = armed()
    before = as_chunks(noise(2.0, -45.0))
    after = as_chunks(np.concatenate([noise(0.5, -45.0, 13), noise(0.2, -15.0, 14)]), t0=10.0)
    bites = run(detector, before + after)
    assert bites == [pytest.approx(10.5, abs=0.03)]


def test_template_mode_is_refused_until_it_exists():
    with pytest.raises(ValueError, match="template"):
        SplashDetector(AudioDetectorConfig(use_template=True), RATE)


def test_band_must_be_ordered():
    with pytest.raises(ValueError):
        AudioDetectorConfig(band_hz=(4000, 300))


def test_arming_during_a_ringing_sound_waits_for_a_new_onset():
    """Armed while the landing plop still rings: its tail must not count as the bite."""
    detector = SplashDetector(AudioDetectorConfig(), RATE)
    run(detector, as_chunks(np.concatenate([noise(2.0, -45.0), noise(0.1, -15.0, seed=14)])))
    detector.arm()  # mid-plop
    tail = np.concatenate([noise(0.3, -15.0, seed=15), noise(1.5, -45.0, seed=16)])
    assert run(detector, as_chunks(tail, t0=2.1)) == []

    bite = np.concatenate([noise(0.2, -15.0, seed=17), noise(0.2, -45.0, seed=18)])
    assert run(detector, as_chunks(bite, t0=3.9)) == [pytest.approx(3.92, abs=0.03)]
