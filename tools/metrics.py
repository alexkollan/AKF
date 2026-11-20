"""Score the bite detector against the hand-placed markers in recorded clips (M3).

Each cast is replayed the way the bot will run it: the detector is armed once the bobber
has landed and disarmed at its first detection, because that is when the bot hooks. Every
cast then has exactly one outcome:

==========  ============================================================================
 hit        first detection close to the ``bite`` marker
 early      first detection well before the marker — the bot would hook an empty bobber
 spurious   a detection in a cast that had no bite at all
 miss       a bite marker and no detection
 quiet      no bite, no detection — the correct result for a cast that timed out
==========  ============================================================================

``early`` and ``spurious`` are the false positives the plan budgets (< 1 per 20 casts).

**Latency** is ``detection - marker``. The marker is a human pressing F9, which lands a
quarter of a second or so *after* the splash, so a good detector usually shows a negative
latency here. The pass mark is "no later than +300 ms after the marker"; nudge markers onto
the splash in ``tools.replay`` and the number becomes the true latency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from engine.audio import AudioChunk, SplashDetector
from engine.clips import CastSegment, Clip
from engine.config import AudioDetectorConfig, TimingConfig

#: Arm this long after the ``landed`` marker, so the plop has rung out.
ARM_AFTER_LANDED_S = 0.3
#: A detection this far before the bite marker still counts as the bite: the marker is late.
EARLY_TOLERANCE_S = 1.0
#: ...and this far after it.
LATE_TOLERANCE_S = 0.5

#: Acceptance criteria from docs/PLAN.md, M3.
TARGET_DETECTION_RATE = 0.95
TARGET_LATENCY_S = 0.3
TARGET_FALSE_POSITIVES_PER_20 = 1.0

HIT, EARLY, SPURIOUS, MISS, QUIET = "hit", "early", "spurious", "miss", "quiet"


@dataclass(frozen=True, slots=True)
class CastScore:
    clip: str
    index: int
    outcome: str
    armed_at: float
    bite_marker: float | None
    detection: float | None

    @property
    def latency(self) -> float | None:
        if self.outcome != HIT or self.bite_marker is None or self.detection is None:
            return None
        return self.detection - self.bite_marker

    @property
    def false_positive(self) -> bool:
        return self.outcome in (EARLY, SPURIOUS)


@dataclass
class AudioReport:
    casts: list[CastScore] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(1 for cast in self.casts if cast.outcome == outcome)

    @property
    def bites(self) -> int:
        return sum(1 for cast in self.casts if cast.bite_marker is not None)

    @property
    def detection_rate(self) -> float:
        return self.count(HIT) / self.bites if self.bites else 0.0

    @property
    def latencies(self) -> list[float]:
        return [cast.latency for cast in self.casts if cast.latency is not None]

    @property
    def false_positives(self) -> int:
        return sum(1 for cast in self.casts if cast.false_positive)

    @property
    def false_positives_per_20(self) -> float:
        return self.false_positives * 20 / len(self.casts) if self.casts else 0.0

    def checks(self) -> list[tuple[str, bool, str]]:
        """``(criterion, passed, measured)`` for each acceptance criterion."""
        latencies = self.latencies
        worst = max(latencies) if latencies else float("nan")
        return [
            (
                f"bite detection >= {TARGET_DETECTION_RATE:.0%}",
                self.bites > 0 and self.detection_rate >= TARGET_DETECTION_RATE,
                f"{self.count(HIT)}/{self.bites} = {self.detection_rate:.1%}",
            ),
            (
                f"latency <= +{TARGET_LATENCY_S * 1000:.0f} ms after the marker",
                bool(latencies) and worst <= TARGET_LATENCY_S,
                f"worst {worst * 1000:+.0f} ms" if latencies else "no hits",
            ),
            (
                f"false positives < {TARGET_FALSE_POSITIVES_PER_20:g} per 20 casts",
                bool(self.casts) and self.false_positives_per_20 < TARGET_FALSE_POSITIVES_PER_20,
                f"{self.false_positives} in {len(self.casts)} casts "
                f"= {self.false_positives_per_20:.2f} per 20",
            ),
        ]

    @property
    def passed(self) -> bool:
        return all(passed for _name, passed, _measured in self.checks())


def arm_time(cast: CastSegment, timing: TimingConfig) -> float:
    """When the bot would arm the detector for this cast."""
    if cast.landed is not None:
        return cast.landed + ARM_AFTER_LANDED_S
    return cast.start + timing.land_delay_ms / 1000.0


def classify(bite: float | None, detection: float | None) -> str:
    if bite is None:
        return QUIET if detection is None else SPURIOUS
    if detection is None:
        return MISS
    if detection < bite - EARLY_TOLERANCE_S:
        return EARLY
    if detection > bite + LATE_TOLERANCE_S:
        return MISS  # far too late to count; the bot would have timed out or lost the fish
    return HIT


def score_clip(
    clip: Clip,
    config: AudioDetectorConfig | None = None,
    timing: TimingConfig | None = None,
) -> list[CastScore]:
    """Replay one clip's audio through a fresh detector, cast by cast."""
    config = config or AudioDetectorConfig()
    timing = timing or TimingConfig()
    casts = clip.casts()
    if not casts:
        return []

    detector = SplashDetector(config, clip.meta.audio.sample_rate)
    plans = [(cast, arm_time(cast, timing)) for cast in casts]
    detections: dict[int, float] = {}
    current = 0

    # One pass over the whole clip so the noise floor carries across casts, as it does live.
    for t, samples in clip.audio_chunks(config.chunk_ms):
        while current < len(plans) and t >= plans[current][0].end:
            current += 1
        if current >= len(plans):
            break
        cast, armed_at = plans[current]
        should_arm = t >= armed_at and cast.index not in detections
        if should_arm and not detector.armed:
            detector.arm()
        elif not should_arm and detector.armed:
            detector.disarm()

        event = detector.feed(AudioChunk(t=t, samples=samples))
        if event is not None and detector.armed and cast.contains(event.t):
            detections[cast.index] = event.t
            detector.disarm()  # the bot hooks here

    scores = []
    for cast, armed_at in plans:
        bite = cast.bites[0] if cast.bites else None
        detection = detections.get(cast.index)
        scores.append(
            CastScore(
                clip=clip.name,
                index=cast.index,
                outcome=classify(bite, detection),
                armed_at=armed_at,
                bite_marker=bite,
                detection=detection,
            )
        )
    return scores


def score_clips(
    clips: Sequence[Clip],
    config: AudioDetectorConfig | None = None,
    timing: TimingConfig | None = None,
) -> AudioReport:
    report = AudioReport()
    for clip in clips:
        if not clip.audio_path.is_file():
            continue
        report.casts.extend(score_clip(clip, config, timing))
    return report


def format_report(report: AudioReport, *, per_cast: bool = True) -> str:
    lines: list[str] = []
    if per_cast:
        lines.append(
            f"{'clip':<34} {'cast':>4}  {'outcome':<9} {'bite':>8} {'detect':>8} {'latency':>9}"
        )
        for cast in report.casts:
            bite = f"{cast.bite_marker:8.2f}" if cast.bite_marker is not None else "       -"
            found = f"{cast.detection:8.2f}" if cast.detection is not None else "       -"
            lat = f"{cast.latency * 1000:+7.0f}ms" if cast.latency is not None else "        -"
            lines.append(
                f"{cast.clip[:34]:<34} {cast.index:>4}  {cast.outcome:<9} {bite} {found} {lat}"
            )
        lines.append("")

    latencies = report.latencies
    if latencies:
        lat = np.array(latencies) * 1000
        lines.append(
            f"latency vs marker: median {np.median(lat):+.0f} ms, "
            f"p95 {np.percentile(lat, 95):+.0f} ms, range {lat.min():+.0f}..{lat.max():+.0f} ms"
        )
    outcomes = ", ".join(
        f"{name} {report.count(name)}" for name in (HIT, MISS, EARLY, SPURIOUS, QUIET)
    )
    lines.append(f"{len(report.casts)} casts: {outcomes}")
    lines.append("")
    for name, passed, measured in report.checks():
        lines.append(f"  [{'PASS' if passed else 'FAIL'}] {name:<42} {measured}")
    return "\n".join(lines)


def sweep(
    clips: Sequence[Clip],
    ratios: Sequence[float],
    base: AudioDetectorConfig | None = None,
) -> str:
    """Score several trigger ratios side by side, to pick one from data instead of taste."""
    base = base or AudioDetectorConfig()
    rows = [f"{'ratio':>6} {'dB':>6} {'detect':>8} {'FP/20':>7} {'worst lat':>10}  verdict"]
    for ratio in ratios:
        config = base.model_copy(update={"trigger_ratio": ratio})
        report = score_clips(clips, config)
        latencies = report.latencies
        worst = f"{max(latencies) * 1000:+.0f} ms" if latencies else "-"
        rows.append(
            f"{ratio:6.2f} {20 * np.log10(ratio):+6.1f} {report.detection_rate:8.1%} "
            f"{report.false_positives_per_20:7.2f} {worst:>10}  "
            f"{'PASS' if report.passed else 'fail'}"
        )
    return "\n".join(rows)
