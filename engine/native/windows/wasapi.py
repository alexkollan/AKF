"""Windows: WASAPI endpoint loopback — hear exactly what an output device plays.

Loopback means the capture taps the render endpoint, not a microphone: no ambient noise,
no echo, and nothing to plug in. The cost is that it hears *everything* Windows plays, so
Discord and music have to be muted or routed elsewhere. That is why it is only the fallback
behind process loopback (``engine.native.windows.process_loopback``).

The PortAudio callback runs on its own high-priority thread. It must stay cheap: it drops
chunks into a queue and returns. Nothing in this module may be called from the asyncio loop.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Iterator
from types import TracebackType

import numpy as np
import pyaudiowpatch as pyaudio

from engine import clock
from engine.audio import (
    DEFAULT_QUEUE_CHUNKS,
    SAMPLE_DTYPE,
    AudioChunk,
    AudioDevice,
    AudioError,
    AudioSource,
    stamp_chunk,
)

logger = logging.getLogger(__name__)

SAMPLE_FORMAT = pyaudio.paInt16


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
        from engine.native.windows.process_loopback import ProcessLoopbackStream, supported

        if supported():
            return ProcessLoopbackStream(pid)
        message = "process loopback is not supported on this Windows build"
        if not allow_endpoint_fallback:
            raise AudioError(message)
        logger.warning("%s; falling back to endpoint loopback (hears every app)", message)

    return LoopbackStream(resolve_device(device_index), chunk_ms=chunk_ms)
