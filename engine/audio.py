"""WASAPI loopback capture: hear exactly what the speakers play.

The bite is not visible to the game's API and the splash is the cleanest signal there is,
so audio is the engine's default detector. This module owns the *stream*; the splash
detector that consumes it arrives in M3 and follows the usual detector contract
(``feed(chunk) -> Event | None``), so it can be replayed over a recorded wav.

Loopback means the capture taps the render endpoint, not a microphone: no ambient noise,
no echo, and nothing to plug in. The cost is that it hears *everything* Windows plays, so
Discord and music have to be muted or routed elsewhere — the UI checks this and says so.

The PortAudio callback runs on its own high-priority thread. It must stay cheap: it drops
chunks into a queue and returns. Nothing in this module may be called from the asyncio loop.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import wave
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol

import numpy as np
import pyaudiowpatch as pyaudio

from engine import clock
from engine.config import AudioDetectorConfig

logger = logging.getLogger(__name__)

SAMPLE_FORMAT = pyaudio.paInt16
SAMPLE_DTYPE = np.int16
SAMPLE_WIDTH_BYTES = 2

#: Dropping a chunk is better than stalling PortAudio, but a full queue means the consumer
#: is too slow and the log should say so.
DEFAULT_QUEUE_CHUNKS = 256


class AudioError(RuntimeError):
    """No usable loopback device, or the stream refused to open."""


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    name: str
    channels: int
    sample_rate: int
    is_default: bool

    def __str__(self) -> str:
        mark = " (default)" if self.is_default else ""
        return f"[{self.index}] {self.name} {self.channels}ch @ {self.sample_rate} Hz{mark}"


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """A block of loopback samples.

    ``t`` is seconds since the stream started, on the shared clock (``engine.clock``), like the
    frame timestamps, which is what lets a recorded clip line audio up with video.
    ``samples`` is ``(frames, channels)`` int16.
    """

    t: float
    samples: np.ndarray

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    def mono(self) -> np.ndarray:
        """Channel average as float32 in -1..1, which is what the detectors work on."""
        as_float = self.samples.astype(np.float32) / 32768.0
        if as_float.ndim == 1:
            return as_float
        return as_float.mean(axis=1)


def _open_pyaudio() -> pyaudio.PyAudio:
    try:
        return pyaudio.PyAudio()
    except Exception as exc:  # PortAudio initialisation is all-or-nothing
        msg = f"could not initialise PortAudio: {exc}"
        raise AudioError(msg) from exc


def list_loopback_devices() -> list[AudioDevice]:
    """Every WASAPI loopback endpoint, with the one following the default output marked."""
    audio = _open_pyaudio()
    try:
        default_name = ""
        try:
            wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_name = str(
                audio.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
            )
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("no default WASAPI output: %s", exc)

        devices: list[AudioDevice] = []
        for info in audio.get_loopback_device_info_generator():
            name = str(info["name"])
            devices.append(
                AudioDevice(
                    index=int(info["index"]),
                    name=name,
                    channels=int(info["maxInputChannels"]),
                    sample_rate=int(info["defaultSampleRate"]),
                    # The loopback endpoint is named after the render device it taps.
                    is_default=bool(default_name) and default_name in name,
                )
            )
        return devices
    finally:
        audio.terminate()


def default_loopback_device() -> AudioDevice:
    """The loopback endpoint for whatever Windows is currently playing through."""
    devices = list_loopback_devices()
    if not devices:
        msg = (
            "no WASAPI loopback device found — check that an output device is enabled in "
            "Windows sound settings"
        )
        raise AudioError(msg)
    for device in devices:
        if device.is_default:
            return device
    logger.warning("no loopback device matches the default output; using %s", devices[0])
    return devices[0]


def resolve_device(index: int | None) -> AudioDevice:
    """Pick the configured device, falling back to the default when it is gone."""
    if index is None:
        return default_loopback_device()
    for device in list_loopback_devices():
        if device.index == index:
            return device
    logger.warning("loopback device %s is no longer present; using the default", index)
    return default_loopback_device()


class LoopbackStream:
    """An open WASAPI loopback capture, delivering chunks through a queue.

    Use it as a context manager::

        with LoopbackStream(chunk_ms=20) as stream:
            while running:
                chunk = stream.read(timeout=0.5)

    The PortAudio callback only enqueues, so a slow consumer costs dropped chunks (counted
    in :attr:`dropped`) rather than a stuttering capture.
    """

    def __init__(
        self,
        device: AudioDevice | None = None,
        *,
        chunk_ms: int = 20,
        max_queued_chunks: int = DEFAULT_QUEUE_CHUNKS,
    ) -> None:
        self.device = device or default_loopback_device()
        self.chunk_frames = max(16, int(self.device.sample_rate * chunk_ms / 1000))
        self.sample_rate = self.device.sample_rate
        self.channels = self.device.channels
        self.name = f"endpoint loopback {self.device.name}"
        self.dropped = 0

        self._queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=max_queued_chunks)
        self._audio = _open_pyaudio()
        self._start_time = 0.0
        self._frames_seen = 0
        self._lock = threading.Lock()

        try:
            self._stream = self._audio.open(
                format=SAMPLE_FORMAT,
                channels=self.device.channels,
                rate=self.device.sample_rate,
                frames_per_buffer=self.chunk_frames,
                input=True,
                input_device_index=self.device.index,
                stream_callback=self._on_chunk,
                start=False,
            )
        except Exception as exc:
            self._audio.terminate()
            msg = f"could not open loopback stream on {self.device}: {exc}"
            raise AudioError(msg) from exc

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._start_time = clock.now()
        self._stream.start_stream()

    def close(self) -> None:
        try:
            if self._stream.is_active():
                self._stream.stop_stream()
            self._stream.close()
        finally:
            self._audio.terminate()

    def __enter__(self) -> LoopbackStream:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # -- data --------------------------------------------------------------------------

    def read(self, timeout: float | None = 0.5) -> AudioChunk | None:
        """Next chunk, or ``None`` when nothing arrived within ``timeout``."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> Iterator[AudioChunk]:
        """Every chunk queued right now, without waiting for more."""
        while True:
            try:
                yield self._queue.get_nowait()
            except queue.Empty:
                return

    @property
    def start_time(self) -> float:
        """Reference point for chunk timestamps, shared with the frame clock."""
        return self._start_time

    def _on_chunk(
        self,
        in_data: bytes | None,
        frame_count: int,
        _time_info: dict[str, float],
        _status: int,
    ) -> tuple[bytes | None, int]:
        """PortAudio callback. Keep it short: enqueue and return."""
        if in_data:
            arrived = clock.now() - self._start_time
            with self._lock:
                t = stamp_chunk(self._frames_seen, frame_count, self.device.sample_rate, arrived)
                self._frames_seen = round(t * self.device.sample_rate) + frame_count
            samples = np.frombuffer(in_data, dtype=SAMPLE_DTYPE)
            if self.device.channels > 1:
                samples = samples.reshape(-1, self.device.channels)
            try:
                self._queue.put_nowait(AudioChunk(t=t, samples=samples))
            except queue.Full:
                self.dropped += 1
                if self.dropped in (1, 10, 100, 1000):
                    logger.warning("audio queue full; dropped %d chunks", self.dropped)
        return (None, pyaudio.paContinue)


#: How far the wall clock may run ahead of the sample count before it counts as a gap.
#: Delivery jitter is about one buffer; a real silence gap is hundreds of milliseconds.
GAP_TOLERANCE_S = 0.08


def stamp_chunk(frames_seen: int, frame_count: int, rate: int, arrived: float) -> float:
    """Timestamp for a chunk, in seconds since the stream started.

    WASAPI loopback delivers **nothing** while nothing is playing: no callbacks, no zero
    buffers. Counting samples alone would then stamp a splash heard after ten seconds of
    silence as if it came straight after the previous sound. So:

    * while audio flows, the sample count is the clock — it is exact and jitter-free;
    * when the wall clock has run ahead by more than :data:`GAP_TOLERANCE_S`, there was a
      silent gap, and the chunk is re-anchored to when it arrived (minus its own length,
      since it was captured *before* the callback fired).
    """
    by_samples = frames_seen / rate
    by_clock = arrived - frame_count / rate
    if by_clock - by_samples > GAP_TOLERANCE_S:
        return by_clock
    return by_samples


# --------------------------------------------------------------------------------------
# wav helpers, shared by the recorder and the fixture loader
# --------------------------------------------------------------------------------------


class WavWriter:
    """Append-only int16 wav writer."""

    def __init__(self, path: Path, *, channels: int, sample_rate: int) -> None:
        self.path = path
        self.channels = channels
        self.sample_rate = sample_rate
        self.frames_written = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(channels)
        self._wave.setsampwidth(SAMPLE_WIDTH_BYTES)
        self._wave.setframerate(sample_rate)

    def append(self, samples: np.ndarray) -> None:
        block = np.ascontiguousarray(samples, dtype=SAMPLE_DTYPE)
        self._wave.writeframes(block.tobytes())
        self.frames_written += int(block.shape[0])

    def close(self) -> None:
        self._wave.close()

    def __enter__(self) -> WavWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Load a wav as ``((frames, channels) int16, sample_rate)``."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        if width != SAMPLE_WIDTH_BYTES:
            msg = f"{path} is {width * 8}-bit; the clip format is 16-bit"
            raise AudioError(msg)
        raw = handle.readframes(handle.getnframes())
    samples = np.frombuffer(raw, dtype=SAMPLE_DTYPE)
    if channels > 1:
        samples = samples.reshape(-1, channels)
    return samples, rate


# --------------------------------------------------------------------------------------
# choosing a source
# --------------------------------------------------------------------------------------


class AudioSource(Protocol):
    """What the recorder, the listener and the engine need from any audio capture."""

    name: str
    sample_rate: int
    channels: int
    dropped: int

    @property
    def start_time(self) -> float: ...

    def start(self) -> None: ...

    def close(self) -> None: ...

    def read(self, timeout: float | None = 0.5) -> AudioChunk | None: ...

    def drain(self) -> Iterator[AudioChunk]: ...


def open_audio_source(
    pid: int | None = None,
    *,
    device_index: int | None = None,
    chunk_ms: int = 20,
    allow_endpoint_fallback: bool = True,
) -> AudioSource:
    """The best available capture for the game.

    With a ``pid``, try process loopback first: it hears only WoW, so Discord and music
    cannot fake a bite. Without one, or when it is unavailable, fall back to endpoint
    loopback on the configured device and say so — the fallback works, it is just noisier.
    The returned source is not started yet.
    """
    if pid is not None:
        from engine.audio_process import ProcessLoopbackStream, supported

        if supported():
            return ProcessLoopbackStream(pid)
        message = "process loopback is not supported on this Windows build"
        if not allow_endpoint_fallback:
            raise AudioError(message)
        logger.warning("%s; falling back to endpoint loopback (hears every app)", message)

    return LoopbackStream(resolve_device(device_index), chunk_ms=chunk_ms)


# --------------------------------------------------------------------------------------
# splash detector
# --------------------------------------------------------------------------------------

#: Windows the floor must have seen before a bite may fire, so the very first sound after
#: start-up is not judged against an empty history.
WARMUP_WINDOWS = 10


def to_dbfs(value: float) -> float:
    """Linear RMS (full scale = 1.0) to dBFS, clamped at -120."""
    return 20.0 * math.log10(max(value, 1e-6))


def from_dbfs(value: float) -> float:
    return 10.0 ** (value / 20.0)


@dataclass(frozen=True, slots=True)
class BiteEvent:
    """The detector's verdict. ``t`` is when it *could* have known: the end of the window."""

    t: float
    level_db: float
    floor_db: float
    ratio: float
    source: str = "audio"


@dataclass(frozen=True, slots=True)
class DetectorLevel:
    """One analysed window, for the live chart and the replay plots."""

    t: float
    level_db: float
    floor_db: float
    threshold_db: float
    armed: bool


class SplashDetector:
    """Find the bite splash in a stream of audio chunks.

    Pure and replayable: no capture, no clock — timestamps arrive with the chunks — so the
    same object runs live and over a recorded wav.

    Per analysis window it measures RMS inside ``band_hz`` (Hann window + FFT), compares it
    with a rolling **median** of recent windows, and fires on the *rising edge* — the first
    window over ``trigger_ratio`` after one that was under it. A splash is an onset; a level
    that stays up (rain starting, the tail of the landing plop still ringing when the
    detector is armed) must not read as a string of bites.

    Every window goes into the history: the median already ignores a splash that fills a
    tenth of it, and it still follows the ambience when that genuinely gets louder (rain, a
    noisier spot). Excluding "loud" windows instead would leave the floor unlearnable
    whenever the session starts above ``floor_min_dbfs`` — a dead detector.
    Because levels are relative, Windows and in-game volume do not move the threshold —
    except at the bottom, where ``floor_min_dbfs`` stops digital silence from making every
    sound look infinitely loud.

    Firing is gated by :meth:`arm`/:meth:`disarm`. The state machine arms it once the
    bobber has landed (the landing plop is a splash too) and disarms it after the hook.
    The detector keeps measuring while disarmed so the floor is always current.

    Chunks may be any size; they are re-cut into fixed windows internally. A jump in chunk
    timestamps (dropped audio) restarts the window rather than splicing across the hole.
    """

    def __init__(self, config: AudioDetectorConfig, sample_rate: int) -> None:
        if config.use_template:
            msg = "template matching is not implemented yet; it needs recorded fixtures first"
            raise ValueError(msg)
        self.config = config
        self.sample_rate = sample_rate
        self.window = max(32, int(sample_rate * config.chunk_ms / 1000))
        self._taper = np.hanning(self.window).astype(np.float32)
        # Hann loses energy; scale so a full-scale in-band sine still reads ~0.707 RMS.
        self._taper_gain = float(np.sqrt(np.mean(self._taper**2)))
        freqs = np.fft.rfftfreq(self.window, d=1.0 / sample_rate)
        low, high = config.band_hz
        self._band = (freqs >= low) & (freqs <= high)

        history = max(WARMUP_WINDOWS, int(config.noise_window_s * 1000 / config.chunk_ms))
        self._history: deque[float] = deque(maxlen=history)
        self._min_floor = from_dbfs(config.floor_min_dbfs)
        self._min_gap = config.min_gap_ms / 1000.0

        self._buffer = np.zeros(0, dtype=np.float32)
        self._buffer_t = 0.0
        self._armed = False
        self._last_fire = -math.inf
        self._was_loud = False
        self.last: DetectorLevel | None = None

    # -- gating ------------------------------------------------------------------------

    @property
    def armed(self) -> bool:
        return self._armed

    def arm(self) -> None:
        self._armed = True

    def disarm(self) -> None:
        self._armed = False

    def reset(self) -> None:
        """Forget everything, including the noise floor. For a new session, not a new cast."""
        self._history.clear()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_fire = -math.inf
        self._was_loud = False
        self.last = None

    # -- data --------------------------------------------------------------------------

    def feed(self, chunk: AudioChunk) -> BiteEvent | None:
        """Consume a chunk; return the first bite it completes, if any."""
        mono = chunk.mono()
        if mono.size == 0:
            return None

        expected = self._buffer_t + self._buffer.size / self.sample_rate
        if self._buffer.size == 0 or abs(chunk.t - expected) > self.window / self.sample_rate:
            # First chunk, or audio went missing: start a fresh window at this chunk.
            self._buffer = mono
            self._buffer_t = chunk.t
        else:
            self._buffer = np.concatenate([self._buffer, mono])

        event: BiteEvent | None = None
        while self._buffer.size >= self.window:
            block = self._buffer[: self.window]
            self._buffer = self._buffer[self.window :]
            end_t = self._buffer_t + self.window / self.sample_rate
            self._buffer_t = end_t
            fired = self._analyse(block, end_t)
            if event is None:
                event = fired
        return event

    def _analyse(self, block: np.ndarray, end_t: float) -> BiteEvent | None:
        spectrum = np.fft.rfft(block * self._taper)
        # Parseval: band energy back to an RMS in the same units as the samples.
        power = float(np.sum(np.abs(spectrum[self._band]) ** 2)) * 2.0 / (self.window**2)
        level = math.sqrt(power) / self._taper_gain

        warm = len(self._history) >= WARMUP_WINDOWS
        floor = float(np.median(self._history)) if self._history else self._min_floor
        floor = max(floor, self._min_floor)
        ratio = level / floor
        loud = ratio >= self.config.trigger_ratio

        self.last = DetectorLevel(
            t=end_t,
            level_db=to_dbfs(level),
            floor_db=to_dbfs(floor),
            threshold_db=to_dbfs(floor * self.config.trigger_ratio),
            armed=self._armed,
        )

        # Judge against the floor *before* this window, then let it inform the next one.
        self._history.append(level)
        onset = loud and not self._was_loud
        self._was_loud = loud

        if not onset:
            return None
        if not (self._armed and warm and end_t - self._last_fire >= self._min_gap):
            return None

        self._last_fire = end_t
        return BiteEvent(t=end_t, level_db=to_dbfs(level), floor_db=to_dbfs(floor), ratio=ratio)
