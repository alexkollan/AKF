"""Play a recorded clip back with its audio and markers in sync, and fix the markers.

    uv run python -m tools.replay                       # list the fixtures
    uv run python -m tools.replay tests/fixtures/<clip> # play it
    uv run python -m tools.replay <clip> --info         # just the numbers

The window shows the ROI on top and the loopback waveform underneath, with the markers as
ticks and a playhead that follows the audio. That is the point of the strip: if a bite
marker sits on the splash spike, video, audio and labels share one clock.

Playback is driven by the audio stream rather than the wall clock, so the frame on screen
is the frame that was on screen when those samples played.

**Fixing markers.** F9 is pressed by a human, so a ``bite`` marker lands a quarter of a
second or so after the splash. Pause on the spike and press ``3`` to put the marker where
it belongs, then ``s`` to save. Good fixtures are worth the minute it takes.

===========  ==================================================================
  space      play / pause
  , .        step one frame        [ ]   step one second
  n p        jump to next / previous marker
  1 2 3      place a cast / landed / bite marker here
  x          delete the marker nearest the playhead
  s          save markers.json
  q, Esc     quit
===========  ==================================================================

**Scoring the detector.** ``--metrics`` replays every fixture through the bite detector the
way the bot runs it and checks the M3 acceptance criteria; ``--sweep`` tries several trigger
ratios side by side. See ``tools.metrics`` for how each cast is judged.

    uv run python -m tools.replay --metrics              # all fixtures
    uv run python -m tools.replay <clip> --metrics       # one clip
    uv run python -m tools.replay --sweep 2.5 3 3.5 4 5  # pick a ratio from data
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import pyaudiowpatch as pyaudio

from engine.clips import (
    MARKER_BITE,
    MARKER_CAST,
    MARKER_LANDED,
    Clip,
    ClipError,
    FrameReader,
    Marker,
    list_clips,
    load_clip,
)
from engine.config import AudioDetectorConfig
from tools import metrics

DEFAULT_FIXTURES = Path("tests/fixtures")

WINDOW_TITLE = "AFK replay"
MAX_VIDEO_WIDTH = 1100
STRIP_HEIGHT = 150
WAVE_HEIGHT = 92
ENVELOPE_POINTS = 4000
FLASH_SECONDS = 0.25

# BGR, matching the UI's state colours closely enough to be familiar.
COLOR_BG = (24, 24, 24)
COLOR_WAVE = (120, 120, 120)
COLOR_PLAYHEAD = (255, 255, 255)
COLOR_TEXT = (220, 220, 220)
MARKER_COLORS: dict[str, tuple[int, int, int]] = {
    MARKER_CAST: (150, 150, 150),
    MARKER_LANDED: (220, 190, 60),
    MARKER_BITE: (70, 70, 240),
}

logger = logging.getLogger("replay")


# --------------------------------------------------------------------------------------
# clocks
# --------------------------------------------------------------------------------------


class AudioClock:
    """Plays the clip's wav and reports how far through it the speakers are.

    The position comes from the number of samples handed to PortAudio minus the output
    latency, which is as close to "what you are hearing right now" as this gets.
    """

    def __init__(self, samples: np.ndarray, rate: int, channels: int, offset: float) -> None:
        self._samples = np.ascontiguousarray(samples, dtype=np.int16)
        self._rate = rate
        self._channels = channels
        self._offset = offset
        self._cursor = 0
        self._playing = False
        self._latency = 0.0

        self._audio = pyaudio.PyAudio()
        try:
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                output=True,
                stream_callback=self._on_request,
                start=False,
            )
        except Exception as exc:
            self._audio.terminate()
            msg = f"could not open an output stream: {exc}"
            raise ClipError(msg) from exc

    @property
    def duration(self) -> float:
        return self._offset + len(self._samples) / self._rate

    @property
    def playing(self) -> bool:
        return self._playing

    def position(self) -> float:
        heard = self._cursor / self._rate - (self._latency if self._playing else 0.0)
        return self._offset + max(0.0, heard)

    def play(self, t: float | None = None) -> None:
        if t is not None:
            self.seek(t)
        if not self._playing:
            self._stream.start_stream()
            self._latency = float(self._stream.get_output_latency() or 0.0)
            self._playing = True

    def pause(self) -> None:
        if self._playing:
            self._stream.stop_stream()
            self._playing = False

    def seek(self, t: float) -> None:
        target = int(max(0.0, t - self._offset) * self._rate)
        self._cursor = min(max(0, target), len(self._samples))

    def close(self) -> None:
        try:
            self.pause()
            self._stream.close()
        finally:
            self._audio.terminate()

    def _on_request(
        self,
        _in_data: bytes | None,
        frame_count: int,
        _time_info: dict[str, float],
        _status: int,
    ) -> tuple[bytes, int]:
        start = self._cursor
        end = min(start + frame_count, len(self._samples))
        block = self._samples[start:end]
        self._cursor = end

        if len(block) < frame_count:
            shape = (
                (frame_count - len(block), self._channels)
                if block.ndim > 1
                else (frame_count - len(block),)
            )
            block = np.concatenate([block, np.zeros(shape, dtype=np.int16)])
        return block.tobytes(), pyaudio.paContinue


class SilentClock:
    """Stand-in for clips recorded without audio: advances in frame-sized steps."""

    def __init__(self, duration: float, fps: float) -> None:
        self.duration = duration
        self._step = 1.0 / max(fps, 1.0)
        self._position = 0.0
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing

    def position(self) -> float:
        if self._playing:
            self._position = min(self.duration, self._position + self._step)
        return self._position

    def play(self, t: float | None = None) -> None:
        if t is not None:
            self._position = t
        self._playing = True

    def pause(self) -> None:
        self._playing = False

    def seek(self, t: float) -> None:
        self._position = max(0.0, min(self.duration, t))

    def close(self) -> None:
        return


Clock = AudioClock | SilentClock


# --------------------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------------------


def audio_envelope(clip: Clip, points: int = ENVELOPE_POINTS) -> np.ndarray:
    """Peak amplitude per time bin, normalised to 0..1, for the waveform strip."""
    try:
        samples, _rate = clip.read_audio()
    except ClipError:
        return np.zeros(points, dtype=np.float32)

    mono = samples.astype(np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    if mono.size == 0:
        return np.zeros(points, dtype=np.float32)

    usable = (mono.size // points) * points
    if usable < points:
        return np.abs(mono) / 32768.0
    binned = np.abs(mono[:usable]).reshape(points, -1).max(axis=1)
    peak = float(binned.max()) or 1.0
    return (binned / peak).astype(np.float32)


class Renderer:
    """Builds the single canvas shown each tick: video on top, timeline underneath."""

    def __init__(self, clip: Clip, envelope: np.ndarray) -> None:
        self.clip = clip
        self.envelope = envelope
        self.duration = max(clip.meta.duration, 1e-6)
        self.scale = min(1.0, MAX_VIDEO_WIDTH / max(1, clip.meta.video.width))
        self.width = max(480, int(clip.meta.video.width * self.scale))

    def draw(self, frame: np.ndarray, t: float, index: int, status: str) -> np.ndarray:
        video = (
            cv2.resize(frame, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
            if self.scale < 1.0
            else frame
        )
        if video.shape[1] != self.width:
            video = cv2.resize(
                video, (self.width, int(video.shape[0] * self.width / video.shape[1]))
            )

        canvas = np.full((video.shape[0] + STRIP_HEIGHT, self.width, 3), COLOR_BG, np.uint8)
        canvas[: video.shape[0], : video.shape[1]] = video

        self._draw_flash(canvas, t)
        self._draw_strip(canvas, video.shape[0], t)
        self._draw_text(canvas, t, index, status)
        return canvas

    def _draw_flash(self, canvas: np.ndarray, t: float) -> None:
        """Name any marker the playhead is sitting on, over the video itself."""
        for marker in self.clip.markers:
            if abs(marker.t - t) > FLASH_SECONDS:
                continue
            color = MARKER_COLORS.get(marker.kind, COLOR_TEXT)
            cv2.rectangle(canvas, (0, 0), (self.width - 1, 46), color, 3)
            cv2.putText(
                canvas,
                marker.kind.upper(),
                (16, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                color,
                2,
                cv2.LINE_AA,
            )
            break

    def _draw_strip(self, canvas: np.ndarray, top: int, t: float) -> None:
        wave_top = top + 16
        baseline = wave_top + WAVE_HEIGHT // 2

        for x in range(self.width):
            value = self.envelope[int(x / self.width * len(self.envelope))]
            half = int(value * WAVE_HEIGHT / 2)
            cv2.line(canvas, (x, baseline - half), (x, baseline + half), COLOR_WAVE, 1)

        for marker in self.clip.markers:
            x = self._x_of(marker.t)
            color = MARKER_COLORS.get(marker.kind, COLOR_TEXT)
            cv2.line(canvas, (x, wave_top), (x, wave_top + WAVE_HEIGHT), color, 1)
            cv2.circle(canvas, (x, wave_top), 4, color, -1)

        playhead = self._x_of(t)
        cv2.line(
            canvas,
            (playhead, wave_top - 8),
            (playhead, wave_top + WAVE_HEIGHT + 4),
            COLOR_PLAYHEAD,
            1,
        )

    def _draw_text(self, canvas: np.ndarray, t: float, index: int, status: str) -> None:
        line = (
            f"{t:6.2f}s / {self.duration:.2f}s   frame {index}/{len(self.clip.frame_times) - 1}"
            f"   {status}"
        )
        cv2.putText(
            canvas,
            line,
            (10, canvas.shape[0] - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            COLOR_TEXT,
            1,
            cv2.LINE_AA,
        )

    def _x_of(self, t: float) -> int:
        return int(min(max(t / self.duration, 0.0), 1.0) * (self.width - 1))


# --------------------------------------------------------------------------------------
# player
# --------------------------------------------------------------------------------------


class Player:
    """The interactive loop: show a frame, read a key, repeat."""

    def __init__(self, clip: Clip, *, with_audio: bool = True) -> None:
        self.clip = clip
        self.reader = FrameReader(clip)
        self.renderer = Renderer(clip, audio_envelope(clip))
        self.dirty = False
        self.clock = self._make_clock(with_audio)

    def _make_clock(self, with_audio: bool) -> Clock:
        fps = len(self.clip.frame_times) / max(self.clip.meta.duration, 1e-6)
        if not with_audio or not self.clip.audio_path.is_file():
            return SilentClock(self.clip.meta.duration, fps)
        try:
            samples, rate = self.clip.read_audio()
            channels = self.clip.meta.audio.channels
            return AudioClock(samples, rate, channels, self.clip.meta.audio.start_offset)
        except ClipError as exc:
            logger.warning("playing without sound: %s", exc)
            return SilentClock(self.clip.meta.duration, fps)

    def run(self) -> int:
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_AUTOSIZE)
        self.clock.play(0.0)
        try:
            while True:
                t = min(self.clock.position(), self.clip.meta.duration)
                index = self.clip.frame_index_at(t)
                status = ("playing" if self.clock.playing else "paused") + (
                    "  *unsaved*" if self.dirty else ""
                )
                canvas = self.renderer.draw(self.reader.read(index), t, index, status)
                cv2.imshow(WINDOW_TITLE, canvas)

                if t >= self.clip.meta.duration:
                    self.clock.pause()

                key = cv2.waitKey(16) & 0xFF
                if key != 255 and not self._handle(key, t):
                    break
                if cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            self.clock.close()
            self.reader.close()
            cv2.destroyAllWindows()

        if self.dirty:
            print("markers were changed but not saved (press s next time)")
        return 0

    def _handle(self, key: int, t: float) -> bool:
        """Act on one keypress. Returns False to quit."""
        frame_step = 1.0 / max(len(self.clip.frame_times) / max(self.clip.meta.duration, 1e-6), 1.0)

        if key in (ord("q"), 27):
            return False
        if key == ord(" "):
            self.clock.pause() if self.clock.playing else self.clock.play()
        elif key == ord(","):
            self._scrub(t - frame_step)
        elif key == ord("."):
            self._scrub(t + frame_step)
        elif key == ord("["):
            self._scrub(t - 1.0)
        elif key == ord("]"):
            self._scrub(t + 1.0)
        elif key == ord("n"):
            self._jump(t, forward=True)
        elif key == ord("p"):
            self._jump(t, forward=False)
        elif key in (ord("1"), ord("2"), ord("3")):
            kind = {ord("1"): MARKER_CAST, ord("2"): MARKER_LANDED, ord("3"): MARKER_BITE}[key]
            self._place(kind, t)
        elif key == ord("x"):
            self._delete(t)
        elif key == ord("s"):
            self.clip.save_markers()
            self.dirty = False
            print(f"saved {len(self.clip.markers)} markers to {self.clip.path / 'markers.json'}")
        elif key == ord("h"):
            print(__doc__)
        return True

    def _scrub(self, t: float) -> None:
        self.clock.pause()
        self.clock.seek(max(0.0, min(t, self.clip.meta.duration)))

    def _jump(self, t: float, *, forward: bool) -> None:
        times = sorted(marker.t for marker in self.clip.markers)
        if forward:
            nxt = next((value for value in times if value > t + 1e-3), None)
        else:
            nxt = next((value for value in reversed(times) if value < t - 1e-3), None)
        if nxt is not None:
            self._scrub(nxt)

    def _place(self, kind: str, t: float) -> None:
        """Move an existing marker of this kind onto the playhead, or add a new one."""
        existing = next(
            (m for m in self.clip.markers if m.kind == kind and abs(m.t - t) <= 2.0), None
        )
        if existing is not None:
            self.clip.markers.remove(existing)
            print(f"moved {kind} {existing.t:.2f}s -> {t:.2f}s")
        else:
            print(f"added {kind} at {t:.2f}s")
        self.clip.markers.append(
            existing.model_copy(update={"t": t}) if existing else Marker(t=t, kind=kind)
        )
        self.dirty = True

    def _delete(self, t: float) -> None:
        marker = self.clip.nearest_marker(t)
        if marker is None:
            print("no marker within 0.5s of the playhead")
            return
        self.clip.markers.remove(marker)
        self.dirty = True
        print(f"deleted {marker.kind} at {marker.t:.2f}s")


# --------------------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------------------


def describe(clip: Clip) -> None:
    casts = clip.casts()
    bites = len(clip.markers_of(MARKER_BITE))
    landed = len(clip.markers_of(MARKER_LANDED))
    size_mb = sum(f.stat().st_size for f in clip.path.rglob("*") if f.is_file()) / 1024 / 1024
    fps = len(clip.frame_times) / max(clip.meta.duration, 1e-6)

    print(f"{clip.path.name}")
    tags = ", ".join(clip.meta.tags) or "-"
    print(f"  recorded : {clip.meta.created_at:%Y-%m-%d %H:%M}  tags: {tags}")
    print(
        f"  video    : {clip.meta.video.width}x{clip.meta.video.height} "
        f"{clip.meta.video.codec} {len(clip.frame_times)} frames @ {fps:.1f} fps"
    )
    print(
        f"  audio    : {clip.meta.audio.sample_rate} Hz x{clip.meta.audio.channels} "
        f"offset {clip.meta.audio.start_offset * 1000:+.0f} ms"
    )
    print(f"  duration : {clip.meta.duration:.1f}s   size: {size_mb:.1f} MiB")
    print(f"  markers  : {len(casts)} casts, {landed} landed, {bites} bites")
    if clip.meta.notes:
        print(f"  notes    : {clip.meta.notes}")

    for cast in casts:
        landed_text = f"landed +{cast.landed - cast.start:.1f}s" if cast.landed else "no landing"
        bite_text = ", ".join(f"+{bite - cast.start:.1f}s" for bite in cast.bites) or "no bite"
        print(
            f"    cast {cast.index:>2}  {cast.start:7.2f}s  {cast.duration:5.1f}s  "
            f"{landed_text:<16} bite {bite_text}"
        )


def summarise_all(root: Path) -> int:
    paths = list_clips(root)
    if not paths:
        print(f"no clips in {root} — record one with `uv run python -m tools.record_clip`")
        return 1

    total_casts = 0
    total_bites = 0
    print(f"{len(paths)} clip(s) in {root}:\n")
    for path in paths:
        clip = load_clip(path)
        casts = len(clip.casts())
        bites = len(clip.markers_of(MARKER_BITE))
        total_casts += casts
        total_bites += bites
        tags = ",".join(clip.meta.tags) or "-"
        print(
            f"  {path.name:<34} {clip.meta.duration:6.1f}s  {casts:>3} casts  "
            f"{bites:>3} bites  [{tags}]"
        )

    print(f"\n  total: {total_casts} casts, {total_bites} bites")
    if total_casts < 20:
        print(f"  M3 wants at least 20 casts across 2-3 spots; {20 - total_casts} to go")
    return 0


def run_metrics(args: argparse.Namespace) -> int:
    paths = [args.clip] if args.clip else list_clips(args.fixtures)
    try:
        clips = [load_clip(path) for path in paths]
    except ClipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not clips:
        print(
            f"no clips in {args.fixtures} — record some with `uv run python -m tools.record_clip`"
        )
        return 1

    overrides: dict[str, float] = {}
    if args.ratio is not None:
        overrides["trigger_ratio"] = args.ratio
    if args.floor_min is not None:
        overrides["floor_min_dbfs"] = args.floor_min
    config = AudioDetectorConfig(**overrides)

    if args.sweep:
        print(f"{len(clips)} clip(s), sweeping trigger ratio:\n")
        print(metrics.sweep(clips, args.sweep, config))
        return 0

    report = metrics.score_clips(clips, config)
    if not report.casts:
        print("no scorable casts — clips need audio and cast or landed markers")
        return 1
    print(
        f"{len(clips)} clip(s), trigger ratio {config.trigger_ratio}, "
        f"floor >= {config.floor_min_dbfs} dBFS\n"
    )
    print(metrics.format_report(report))
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="tools.replay", description=__doc__)
    parser.add_argument("clip", nargs="?", type=Path, help="clip directory to play")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--info", action="store_true", help="print the clip summary and exit")
    parser.add_argument("--no-audio", action="store_true", help="play without sound")
    parser.add_argument("--metrics", action="store_true", help="score the bite detector")
    parser.add_argument(
        "--sweep", type=float, nargs="+", metavar="RATIO", help="score several trigger ratios"
    )
    parser.add_argument("--ratio", type=float, default=None, help="trigger ratio override")
    parser.add_argument("--floor-min", type=float, default=None, help="floor clamp in dBFS")
    args = parser.parse_args(argv)

    if args.metrics or args.sweep:
        return run_metrics(args)
    if args.clip is None:
        return summarise_all(args.fixtures)

    try:
        clip = load_clip(args.clip)
    except ClipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    describe(clip)
    if args.info:
        return 0
    if not clip.frame_times:
        print("clip has no frames to play", file=sys.stderr)
        return 3

    print(
        "\nspace play/pause   , . step   [ ] second   n p marker"
        "   1 2 3 mark   x delete   s save   q quit"
    )
    return Player(clip, with_audio=not args.no_audio).run()


if __name__ == "__main__":
    raise SystemExit(main())
