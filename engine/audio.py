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
import queue
import threading
import time
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import numpy as np
import pyaudiowpatch as pyaudio

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

    ``t`` is seconds since the stream started, taken from the same monotonic clock as the
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
        self.dropped = 0

        self._queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=max_queued_chunks)
        self._audio = _open_pyaudio()
        self._start_monotonic = 0.0
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
        self._start_monotonic = time.monotonic()
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
    def start_monotonic(self) -> float:
        """Reference point for chunk timestamps, shared with the frame clock."""
        return self._start_monotonic

    def _on_chunk(
        self,
        in_data: bytes | None,
        frame_count: int,
        _time_info: dict[str, float],
        _status: int,
    ) -> tuple[bytes | None, int]:
        """PortAudio callback. Keep it short: enqueue and return."""
        if in_data:
            arrived = time.monotonic() - self._start_monotonic
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
