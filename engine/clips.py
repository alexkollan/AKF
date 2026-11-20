"""The recorded-clip format: write it once, replay it forever.

A clip is a directory holding one stretch of real fishing — the ROI as video, the loopback
audio as wav, and hand-placed markers saying when the bobber landed and when the fish bit.
Everything the detectors are judged on comes from these, which is why the format is
deliberately boring and inspectable:

```
tests/fixtures/20261120-204500-stv-night/
  meta.json      geometry, codecs, counts, what the client looked like
  frames.json    one timestamp per frame, seconds from the start of the clip
  markers.json   [{"t": 12.4, "kind": "bite", "note": ""}] - editable by hand
  video.mkv      FFV1, lossless (or frames/000000.png ... when recorded as PNG)
  audio.wav      int16, the loopback device's own rate and channel count
```

Two rules make the fixtures trustworthy:

* **Video is lossless.** The bobber is found by HSV thresholds on a small red feather and
  the pixel bite detector works on frame differences; both are exactly what a lossy codec
  destroys. FFV1 costs little on screen content and keeps the pixels honest.
* **Timestamps are recorded, not assumed.** Capture does not deliver a perfectly even 30
  fps, so each frame carries the time it was grabbed. Audio shares the same clock, so a
  marker placed during recording still points at the right frame and the right samples.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal

import cv2
import numpy as np
from pydantic import Field

from engine.audio import WavWriter, read_wav
from engine.config import Model, RoiFraction

META_FILE = "meta.json"
FRAMES_FILE = "frames.json"
MARKERS_FILE = "markers.json"
VIDEO_FILE = "video.mkv"
FRAMES_DIR = "frames"
AUDIO_FILE = "audio.wav"

FFV1_FOURCC = "FFV1"

VideoCodec = Literal["ffv1", "png"]

#: Marker kinds. ``cast`` segments the clip; ``landed`` and ``bite`` are the ground truth
#: the M3 metrics are scored against.
MARKER_CAST = "cast"
MARKER_LANDED = "landed"
MARKER_BITE = "bite"
MARKER_NOTE = "note"


class ClipError(RuntimeError):
    """A clip is missing, malformed or disagrees with its own metadata."""


# --------------------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------------------


class Marker(Model):
    """A hand-placed label. ``t`` is seconds from the start of the clip."""

    t: float
    kind: str = MARKER_NOTE
    note: str = ""


class VideoMeta(Model):
    codec: VideoCodec = "ffv1"
    width: int = 0
    height: int = 0
    fps_target: int = 30
    frame_count: int = 0


class AudioMeta(Model):
    sample_rate: int = 48000
    channels: int = 2
    frame_count: int = 0
    device_name: str = ""
    #: Seconds between the clip's t=0 and the first recorded sample. Usually tiny, but it
    #: is the difference between a bite marker landing on the splash or just after it.
    start_offset: float = 0.0


class ClipGeometry(Model):
    """Where the ROI came from, so a fixture can be matched against a client setup."""

    client_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    roi_screen: tuple[int, int, int, int] = (0, 0, 0, 0)
    roi_fraction: RoiFraction = Field(default_factory=RoiFraction)
    monitor_device: str = ""
    process_name: str = ""
    window_title: str = ""


class ClipMeta(Model):
    name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str = ""
    #: Free-form tags for the fixture matrix: day/night, sea/lake, which spot.
    tags: list[str] = Field(default_factory=list)
    video: VideoMeta = Field(default_factory=VideoMeta)
    audio: AudioMeta = Field(default_factory=AudioMeta)
    geometry: ClipGeometry = Field(default_factory=ClipGeometry)
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class CastSegment:
    """One cast: from its ``cast`` marker to the next one (or the end of the clip)."""

    index: int
    start: float
    end: float
    landed: float | None
    bites: tuple[float, ...]

    @property
    def duration(self) -> float:
        return self.end - self.start

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


# --------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------


class Clip:
    """A recorded clip on disk. Frames and audio are read lazily."""

    def __init__(
        self, path: Path, meta: ClipMeta, frame_times: list[float], markers: list[Marker]
    ) -> None:
        self.path = path
        self.meta = meta
        self.frame_times = frame_times
        self.markers = markers

    # -- basics ------------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<Clip {self.path.name} {len(self.frame_times)} frames "
            f"{self.meta.duration:.1f}s {len(self.markers)} markers>"
        )

    @property
    def name(self) -> str:
        return self.meta.name or self.path.name

    @property
    def video_path(self) -> Path:
        return self.path / VIDEO_FILE

    @property
    def frames_dir(self) -> Path:
        return self.path / FRAMES_DIR

    @property
    def audio_path(self) -> Path:
        return self.path / AUDIO_FILE

    # -- markers -----------------------------------------------------------------------

    def markers_of(self, kind: str) -> list[Marker]:
        return [marker for marker in self.markers if marker.kind == kind]

    def casts(self) -> list[CastSegment]:
        """Split the clip into casts using the ``cast`` markers.

        A clip recorded without explicit cast markers still yields one segment per
        ``landed`` marker, so older fixtures stay usable.
        """
        starts = [marker.t for marker in self.markers_of(MARKER_CAST)]
        if not starts:
            starts = [marker.t for marker in self.markers_of(MARKER_LANDED)]
        if not starts:
            return []

        starts.sort()
        bounds = [
            *starts,
            self.meta.duration or (self.frame_times[-1] if self.frame_times else 0.0),
        ]
        segments: list[CastSegment] = []
        for index, start in enumerate(starts):
            end = bounds[index + 1]
            landed = next((m.t for m in self.markers_of(MARKER_LANDED) if start <= m.t < end), None)
            bites = tuple(m.t for m in self.markers_of(MARKER_BITE) if start <= m.t < end)
            segments.append(CastSegment(index, start, end, landed, bites))
        return segments

    def save_markers(self) -> None:
        """Write ``markers.json`` back out, sorted.

        Markers are the clip's ground truth and the only part meant to be corrected after
        recording, so editing them is a first-class operation rather than a rewrite.
        """
        self.markers.sort(key=lambda marker: marker.t)
        _write_json(
            self.path / MARKERS_FILE,
            [marker.model_dump(mode="json") for marker in self.markers],
        )

    def nearest_marker(self, t: float, within: float = 0.5) -> Marker | None:
        candidates = [m for m in self.markers if abs(m.t - t) <= within]
        return min(candidates, key=lambda m: abs(m.t - t)) if candidates else None

    def frame_index_at(self, t: float) -> int:
        """Index of the last frame captured at or before ``t``."""
        if not self.frame_times:
            msg = f"{self.path.name} has no frames"
            raise ClipError(msg)
        return max(0, int(np.searchsorted(self.frame_times, t, side="right")) - 1)

    # -- frames ------------------------------------------------------------------------

    def frames(
        self, start_index: int = 0, stop_index: int | None = None
    ) -> Iterator[tuple[float, np.ndarray]]:
        """Yield ``(timestamp, BGR frame)`` in order, without loading the clip into RAM."""
        stop = len(self.frame_times) if stop_index is None else stop_index
        if self.meta.video.codec == "png":
            yield from self._png_frames(start_index, stop)
        else:
            yield from self._video_frames(start_index, stop)

    def _png_frames(self, start_index: int, stop_index: int) -> Iterator[tuple[float, np.ndarray]]:
        for index in range(start_index, stop_index):
            path = self.frames_dir / f"{index:06d}.png"
            frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if frame is None:
                msg = f"missing frame {path}"
                raise ClipError(msg)
            yield self.frame_times[index], frame

    def _video_frames(
        self, start_index: int, stop_index: int
    ) -> Iterator[tuple[float, np.ndarray]]:
        capture = cv2.VideoCapture(str(self.video_path))
        if not capture.isOpened():
            msg = f"could not open {self.video_path}"
            raise ClipError(msg)
        try:
            if start_index:
                capture.set(cv2.CAP_PROP_POS_FRAMES, start_index)
            for index in range(start_index, stop_index):
                ok, frame = capture.read()
                if not ok:
                    return
                yield self.frame_times[index], frame
        finally:
            capture.release()

    def frame_at(self, t: float) -> tuple[float, np.ndarray]:
        """Single frame nearest to ``t``. Convenient, but slow in a loop."""
        index = self.frame_index_at(t)
        for timestamp, frame in self.frames(index, index + 1):
            return timestamp, frame
        msg = f"no frame at t={t}"
        raise ClipError(msg)

    # -- audio -------------------------------------------------------------------------

    def read_audio(self) -> tuple[np.ndarray, int]:
        """Whole wav as ``((frames, channels) int16, sample_rate)``."""
        if not self.audio_path.is_file():
            msg = f"{self.path.name} has no audio"
            raise ClipError(msg)
        return read_wav(self.audio_path)

    def audio_chunks(self, chunk_ms: int = 20) -> Iterator[tuple[float, np.ndarray]]:
        """Yield ``(timestamp, chunk)`` the same size the live stream would deliver.

        This is what makes a recorded clip a drop-in substitute for the live loopback
        stream when a detector is replayed.
        """
        samples, rate = self.read_audio()
        size = max(1, int(rate * chunk_ms / 1000))
        offset = self.meta.audio.start_offset
        for start in range(0, len(samples) - size + 1, size):
            yield offset + start / rate, samples[start : start + size]


class FrameReader:
    """Random access to a clip's frames, optimised for playing forward.

    Scrubbing a clip means jumping around, but replaying a detector means walking straight
    through it. Reading the next frame is therefore free, and only a jump pays for a seek.
    """

    def __init__(self, clip: Clip) -> None:
        self.clip = clip
        self._capture: cv2.VideoCapture | None = None
        self._next_index = 0

    def read(self, index: int) -> np.ndarray:
        if not 0 <= index < len(self.clip.frame_times):
            msg = f"frame {index} is outside {self.clip.name} (0..{len(self.clip.frame_times) - 1})"
            raise ClipError(msg)

        if self.clip.meta.video.codec == "png":
            path = self.clip.frames_dir / f"{index:06d}.png"
            frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if frame is None:
                msg = f"missing frame {path}"
                raise ClipError(msg)
            return frame

        capture = self._open()
        if index != self._next_index:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            msg = f"could not decode frame {index} of {self.clip.name}"
            raise ClipError(msg)
        self._next_index = index + 1
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> FrameReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _open(self) -> cv2.VideoCapture:
        if self._capture is None:
            capture = cv2.VideoCapture(str(self.clip.video_path))
            if not capture.isOpened():
                msg = f"could not open {self.clip.video_path}"
                raise ClipError(msg)
            self._capture = capture
            self._next_index = 0
        return self._capture


def load_clip(path: Path | str) -> Clip:
    """Read a clip directory, failing loudly on anything inconsistent."""
    directory = Path(path)
    meta_path = directory / META_FILE
    if not meta_path.is_file():
        msg = f"{directory} is not a clip (no {META_FILE})"
        raise ClipError(msg)

    meta = ClipMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
    frame_times = _read_json_list(directory / FRAMES_FILE)
    markers = [Marker.model_validate(item) for item in _read_json_list(directory / MARKERS_FILE)]

    if meta.video.frame_count and meta.video.frame_count != len(frame_times):
        msg = (
            f"{directory.name}: meta says {meta.video.frame_count} frames but "
            f"{FRAMES_FILE} has {len(frame_times)}"
        )
        raise ClipError(msg)

    return Clip(directory, meta, [float(t) for t in frame_times], markers)


def list_clips(root: Path | str) -> list[Path]:
    """Clip directories under ``root``, oldest first."""
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(child for child in base.iterdir() if (child / META_FILE).is_file())


def _read_json_list(path: Path) -> list:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = f"{path} should hold a JSON array"
        raise ClipError(msg)
    return payload


# --------------------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------------------


class ClipWriter:
    """Builds a clip directory while recording.

    Frames, audio and markers arrive from different threads' data but are written from the
    recorder's own loop, so this class is not thread-safe by design: hand it data, do not
    share it.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        codec: VideoCodec = "ffv1",
        fps_target: int = 30,
        audio_rate: int = 48000,
        audio_channels: int = 2,
        audio_device_name: str = "",
        with_audio: bool = False,
        geometry: ClipGeometry | None = None,
        name: str = "",
        tags: Sequence[str] = (),
        notes: str = "",
    ) -> None:
        self.path = Path(path)
        self.with_audio = with_audio
        if self.path.exists() and any(self.path.iterdir()):
            msg = f"{self.path} already exists and is not empty"
            raise ClipError(msg)
        self.path.mkdir(parents=True, exist_ok=True)

        self.meta = ClipMeta(
            name=name or self.path.name,
            notes=notes,
            tags=list(tags),
            video=VideoMeta(codec=codec, fps_target=fps_target),
            audio=AudioMeta(
                sample_rate=audio_rate, channels=audio_channels, device_name=audio_device_name
            ),
            geometry=geometry or ClipGeometry(),
        )
        self.frame_times: list[float] = []
        self.markers: list[Marker] = []

        self._writer: cv2.VideoWriter | None = None
        self._wav: WavWriter | None = None
        if codec == "png":
            (self.path / FRAMES_DIR).mkdir(exist_ok=True)

    # -- data in -----------------------------------------------------------------------

    def add_frame(self, t: float, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        if not self.frame_times:
            self.meta.video.width = width
            self.meta.video.height = height
        elif (width, height) != (self.meta.video.width, self.meta.video.height):
            msg = (
                f"frame size changed mid-clip: {width}x{height} after "
                f"{self.meta.video.width}x{self.meta.video.height}"
            )
            raise ClipError(msg)

        if self.meta.video.codec == "png":
            target = self.path / FRAMES_DIR / f"{len(self.frame_times):06d}.png"
            if not cv2.imwrite(str(target), frame):
                msg = f"could not write {target}"
                raise ClipError(msg)
        else:
            self._video_writer(width, height).write(frame)

        self.frame_times.append(float(t))

    def add_audio(self, samples: np.ndarray, at: float | None = None) -> None:
        """Append audio; with ``at`` (seconds since the stream started), place it there.

        Placing by timestamp is what keeps the wav aligned with the video: loopback goes
        quiet during silence and the live queue can drop chunks under load, and both would
        otherwise shift every later sample earlier. The hole is filled with silence.
        """
        wav = self._audio_writer()
        if at is not None:
            missing = round(at * self.meta.audio.sample_rate) - wav.frames_written
            if missing > 0:
                wav.append(self._silence(missing))
        wav.append(samples)

    def add_marker(self, t: float, kind: str, note: str = "") -> Marker:
        marker = Marker(t=float(t), kind=kind, note=note)
        self.markers.append(marker)
        return marker

    def set_audio_start(self, offset: float) -> None:
        self.meta.audio.start_offset = float(offset)

    # -- finish ------------------------------------------------------------------------

    def close(self) -> Clip:
        """Flush everything and write the sidecar JSON. Returns the finished clip."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None

        self.meta.video.frame_count = len(self.frame_times)
        self.meta.duration = self.frame_times[-1] if self.frame_times else 0.0

        if self.with_audio:
            # A quiet ending produces no loopback data at all; pad so the wav covers the
            # whole clip and replay never runs out of sound before it runs out of video.
            wav = self._audio_writer()
            end = self.meta.duration - self.meta.audio.start_offset
            missing = round(end * self.meta.audio.sample_rate) - wav.frames_written
            if missing > 0:
                wav.append(self._silence(missing))
        if self._wav is not None:
            self.meta.audio.frame_count = self._wav.frames_written
            self._wav.close()
            self._wav = None

        _write_json(self.path / META_FILE, self.meta.model_dump(mode="json"))
        _write_json(self.path / FRAMES_FILE, [round(t, 6) for t in self.frame_times])
        _write_json(
            self.path / MARKERS_FILE,
            [marker.model_dump(mode="json") for marker in sorted(self.markers, key=lambda m: m.t)],
        )
        return load_clip(self.path)

    def discard(self) -> None:
        """Abandon the recording and remove the directory."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> ClipWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.discard()
        else:
            self.close()

    # -- internals ---------------------------------------------------------------------

    def _audio_writer(self) -> WavWriter:
        if self._wav is None:
            self._wav = WavWriter(
                self.path / AUDIO_FILE,
                channels=self.meta.audio.channels,
                sample_rate=self.meta.audio.sample_rate,
            )
        return self._wav

    def _silence(self, frames: int) -> np.ndarray:
        channels = self.meta.audio.channels
        shape = (frames, channels) if channels > 1 else (frames,)
        return np.zeros(shape, dtype=np.int16)

    def _video_writer(self, width: int, height: int) -> cv2.VideoWriter:
        if self._writer is None:
            fourcc = cv2.VideoWriter_fourcc(*FFV1_FOURCC)
            writer = cv2.VideoWriter(
                str(self.path / VIDEO_FILE), fourcc, self.meta.video.fps_target, (width, height)
            )
            if not writer.isOpened():
                msg = (
                    "could not open an FFV1 writer; record with --format png instead "
                    "(lossy codecs are not an option for detector fixtures)"
                )
                raise ClipError(msg)
            self._writer = writer
        return self._writer


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
