"""Scoring the bite detector against markers, on a synthetic fishing session.

The clip below is shaped like the real thing: constant ambience, a loud cast whoosh, a
landing plop, then either a splash or nothing. Each cast is built to produce one specific
outcome, so the test pins both the arming policy (whoosh and plop must be ignored) and the
way outcomes are classified and rolled up into the M3 pass/fail checks.
"""

import numpy as np
import pytest

from engine.audio import from_dbfs
from engine.clips import MARKER_BITE, MARKER_CAST, MARKER_LANDED, CastSegment, ClipWriter
from engine.config import TimingConfig
from tools import metrics

RATE = 48000
DURATION = 45.0
AMBIENCE_DB = -45.0
LOUD_DB = -15.0


def build_clip(tmp_path, name, events, markers):
    """``events``: (start, seconds, dbfs) bursts over ambience; ``markers``: (t, kind)."""
    rng = np.random.default_rng(0)
    audio = rng.normal(0.0, from_dbfs(AMBIENCE_DB), int(RATE * DURATION))
    for start, seconds, dbfs in events:
        a, b = int(start * RATE), int((start + seconds) * RATE)
        audio[a:b] = rng.normal(0.0, from_dbfs(dbfs), b - a)
    pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)

    writer = ClipWriter(tmp_path / name, audio_rate=RATE, audio_channels=1, with_audio=True)
    for index in range(int(DURATION * 2) + 1):
        writer.add_frame(index / 2, np.zeros((8, 8, 3), np.uint8))
    writer.add_audio(pcm, at=0.0)
    for t, kind in markers:
        writer.add_marker(t, kind)
    return writer.close()


def cast(start, *, splash=None, splash_db=LOUD_DB, distraction=None, bite_marker=None):
    """One cast: whoosh, landing plop 1.3 s later, optional distraction and splash."""
    landed = start + 1.3
    events = [(start + 0.1, 0.3, LOUD_DB), (landed, 0.25, LOUD_DB)]
    if distraction is not None:
        events.append((distraction, 0.3, LOUD_DB))
    if splash is not None:
        events.append((splash, 0.4, splash_db))
    markers = [(start, MARKER_CAST), (landed, MARKER_LANDED)]
    if bite_marker is not None:
        markers.append((bite_marker, MARKER_BITE))
    return events, markers


def session(*casts):
    events, markers = [], []
    for cast_events, cast_markers in casts:
        events += cast_events
        markers += cast_markers
    return events, markers


@pytest.fixture
def mixed_clip(tmp_path):
    events, markers = session(
        cast(0.5, splash=6.0, bite_marker=6.25),  # hit: splash, marker a human beat later
        cast(9.0),  # quiet: nothing bit, nothing fired
        cast(18.0, distraction=22.0),  # spurious: someone else's noise, no bite
        cast(27.0, splash=33.0, splash_db=AMBIENCE_DB + 5, bite_marker=33.25),  # miss: too faint
        cast(36.0, distraction=39.0, splash=42.0, bite_marker=42.25),  # early: hooked at 39
    )
    return build_clip(tmp_path, "mixed", events, markers)


def test_each_cast_gets_the_outcome_it_was_built_for(mixed_clip):
    scores = metrics.score_clip(mixed_clip)
    assert [s.outcome for s in scores] == [
        metrics.HIT,
        metrics.QUIET,
        metrics.SPURIOUS,
        metrics.MISS,
        metrics.EARLY,
    ]


def test_whoosh_and_plop_are_never_detected(mixed_clip):
    """The detector is armed only after the landing, so neither may produce a detection."""
    for score in metrics.score_clip(mixed_clip):
        if score.detection is not None:
            assert score.detection > score.armed_at


def test_hit_latency_is_measured_against_the_marker(mixed_clip):
    hit = metrics.score_clip(mixed_clip)[0]
    # Detected at the splash (~6.02 s), marker pressed at 6.25 s: about -230 ms.
    assert hit.latency == pytest.approx(-0.23, abs=0.04)


def test_report_rolls_up_and_fails_the_bad_session(mixed_clip):
    report = metrics.score_clips([mixed_clip])
    assert report.bites == 3
    assert report.count(metrics.HIT) == 1
    assert report.false_positives == 2
    assert report.false_positives_per_20 == pytest.approx(8.0)
    assert not report.passed
    text = metrics.format_report(report)
    assert "[FAIL] bite detection" in text
    assert "[FAIL] false positives" in text


def test_a_clean_session_passes(tmp_path):
    events, markers = session(
        *[cast(start, splash=start + 5.0, bite_marker=start + 5.25) for start in (0.5, 9.0, 18.0)],
        cast(27.0),
    )
    report = metrics.score_clips([build_clip(tmp_path, "clean", events, markers)])
    assert [s.outcome for s in report.casts] == [metrics.HIT] * 3 + [metrics.QUIET]
    assert report.passed, metrics.format_report(report)


def test_sweep_lists_every_ratio(mixed_clip):
    table = metrics.sweep([mixed_clip], [2.0, 3.5, 6.0])
    assert len(table.splitlines()) == 4  # header + three ratios


def test_clips_without_audio_are_skipped(tmp_path):
    writer = ClipWriter(tmp_path / "mute")
    writer.add_frame(0.0, np.zeros((8, 8, 3), np.uint8))
    writer.add_marker(0.0, MARKER_CAST)
    assert metrics.score_clips([writer.close()]).casts == []


@pytest.mark.parametrize(
    ("bite", "detection", "expected"),
    [
        (None, None, metrics.QUIET),
        (None, 5.0, metrics.SPURIOUS),
        (10.0, None, metrics.MISS),
        (10.0, 9.7, metrics.HIT),  # before the marker: the usual case
        (10.0, 9.0, metrics.HIT),  # exactly at the early tolerance
        (10.0, 8.9, metrics.EARLY),
        (10.0, 10.5, metrics.HIT),  # exactly at the late tolerance
        (10.0, 10.6, metrics.MISS),
    ],
)
def test_classify(bite, detection, expected):
    assert metrics.classify(bite, detection) == expected


def test_arm_time_uses_landing_or_falls_back_to_land_delay():
    timing = TimingConfig(land_delay_ms=1500)
    landed = CastSegment(index=0, start=10.0, end=20.0, landed=11.2, bites=())
    unlanded = CastSegment(index=1, start=10.0, end=20.0, landed=None, bites=())
    assert metrics.arm_time(landed, timing) == pytest.approx(11.2 + metrics.ARM_AFTER_LANDED_S)
    assert metrics.arm_time(unlanded, timing) == pytest.approx(11.5)
