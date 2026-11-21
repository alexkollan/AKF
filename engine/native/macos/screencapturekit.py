"""macOS: ScreenCaptureKit, for the game's audio alone and for fast screen capture.

**Audio.** An ``SCStream`` whose content filter includes only WoW's ``SCRunningApplication``
delivers WoW's audio and nothing else — the macOS counterpart of Windows process loopback,
so Discord or music can never fake a bite. Without a pid the filter takes every app instead
(the counterpart of endpoint loopback). Either way this process's own audio is excluded.
Buffers arrive as float32 ``CMSampleBuffer``s on ScreenCaptureKit's queue; the handler only
converts them to int16 :class:`~engine.audio.AudioChunk` objects and enqueues them.

**Timestamps.** A buffer's presentation time is on the host clock, ``mach_absolute_time``,
which is also what ``time.perf_counter()`` (``engine.clock.now``) reads on macOS. So
``CMTimeGetSeconds(pts) - start_time`` puts audio on the shared timeline directly, the way
``clock.from_qpc`` does for WASAPI. ``uv run python -m tools.spike --no-input --audio 10``
checks it against the live game.

**Video.** The same machinery over a display region, BGRA frames at native resolution.
ScreenCaptureKit only sends a frame when the screen changes; :meth:`ScreenCaptureFrames.grab`
waits briefly for a new one and otherwise returns the last, like dxcam's ``video_mode``.

Everything here needs the **Screen Recording** permission.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from collections.abc import Callable, Iterator
from types import TracebackType

import CoreMedia
import numpy as np
import objc
import Quartz
import ScreenCaptureKit as SCK
from Foundation import NSObject

from engine import clock
from engine.audio import DEFAULT_QUEUE_CHUNKS, SAMPLE_DTYPE, AudioChunk, AudioError
from engine.capture import CaptureError, MonitorInfo, Rect

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHANNELS = 2
#: How long to wait for ScreenCaptureKit's completion handlers.
CALL_TIMEOUT_S = 5.0
_FLAG_NON_INTERLEAVED = 1 << 5  # kAudioFormatFlagIsNonInterleaved
_FLAG_FLOAT = 1 << 0  # kAudioFormatFlagIsFloat
_PIXEL_FORMAT_BGRA = 0x42475241  # kCVPixelFormatType_32BGRA, 'BGRA'
_LOCK_READ_ONLY = 1  # kCVPixelBufferLock_ReadOnly


# --------------------------------------------------------------------------------------
# calling ScreenCaptureKit's async API from a plain thread
# --------------------------------------------------------------------------------------


def _wait(start: Callable[[Callable[..., None]], None], what: str) -> tuple:
    """Run an API that reports through a completion handler; return the handler's args."""
    done = threading.Event()
    result: list[tuple] = []

    def handler(*args: object) -> None:
        result.append(args)
        done.set()

    start(handler)
    if not done.wait(CALL_TIMEOUT_S):
        msg = f"ScreenCaptureKit did not answer ({what})"
        raise TimeoutError(msg)
    return result[0]


def _describe_error(error: object) -> str:
    if error is None:
        return "unknown error"
    try:
        return f"{error.localizedDescription()} (code {error.code()})"
    except AttributeError:
        return str(error)


def shareable_content() -> object:
    """What ScreenCaptureKit may capture right now; raises when Screen Recording is off."""
    content, error = _wait(
        lambda handler: (
            SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                True, False, handler
            )
        ),
        "shareable content",
    )
    if content is None:
        msg = (
            f"ScreenCaptureKit refused ({_describe_error(error)}): grant Screen Recording to "
            "the app running the engine, then restart it (`uv run python -m tools.permissions`)"
        )
        raise PermissionError(msg)
    return content


def _display(content: object, display_id: int | None) -> object:
    displays = list(content.displays() or ())
    if not displays:
        msg = "ScreenCaptureKit lists no displays"
        raise RuntimeError(msg)
    for display in displays:
        if display_id is not None and int(display.displayID()) == display_id:
            return display
    return displays[0]


def _application(content: object, pid: int) -> object | None:
    for app in content.applications() or ():
        if int(app.processID()) == pid:
            return app
    return None


# --------------------------------------------------------------------------------------
# the stream output (one class, ScreenCaptureKit calls it for every buffer)
# --------------------------------------------------------------------------------------

SCStreamOutput = objc.protocolNamed("SCStreamOutput")


class _Output(NSObject, protocols=[SCStreamOutput]):
    """Hands every buffer to a Python callback. Lives as long as its stream."""

    def initWithHandler_(self, handler: Callable[[object, int], None]) -> _Output | None:
        self = objc.super(_Output, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def stream_didOutputSampleBuffer_ofType_(
        self, _stream: object, sample_buffer: object, output_type: int
    ) -> None:
        try:
            self._handler(sample_buffer, int(output_type))
        except Exception:  # an exception here would be lost inside ScreenCaptureKit
            logger.exception("ScreenCaptureKit buffer handler failed")


class _Stream:
    """An ``SCStream`` with one output, started and stopped synchronously."""

    def __init__(
        self,
        content_filter: object,
        config: object,
        handler: Callable[[object, int], None],
        output_types: tuple[int, ...],
    ) -> None:
        self._output = _Output.alloc().initWithHandler_(handler)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, None
        )
        for output_type in output_types:
            ok, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self._output, output_type, None, None
            )
            if not ok:
                msg = f"could not add a stream output: {_describe_error(error)}"
                raise RuntimeError(msg)
        self.running = False

    def start(self) -> None:
        (error,) = _wait(self._stream.startCaptureWithCompletionHandler_, "start capture")
        if error is not None:
            msg = f"ScreenCaptureKit would not start: {_describe_error(error)}"
            raise RuntimeError(msg)
        self.running = True

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            (error,) = _wait(self._stream.stopCaptureWithCompletionHandler_, "stop capture")
        except TimeoutError:
            logger.warning("ScreenCaptureKit did not confirm the stop")
            return
        if error is not None:
            logger.warning("stopping ScreenCaptureKit: %s", _describe_error(error))


def _base_config() -> object:
    config = SCK.SCStreamConfiguration.alloc().init()
    config.setShowsCursor_(False)
    return config


# --------------------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------------------


def _stream_format(asbd: object) -> tuple[int, int, int]:
    """``(channels, format flags, bits per channel)`` from an AudioStreamBasicDescription.

    pyobjc returns the struct as a plain tuple in field order — mSampleRate, mFormatID,
    mFormatFlags, mBytesPerPacket, mFramesPerPacket, mBytesPerFrame, mChannelsPerFrame,
    mBitsPerChannel, mReserved — or, in other versions, as a struct with named fields.
    """
    if isinstance(asbd, tuple | list):
        return int(asbd[6]) or 1, int(asbd[2]), int(asbd[7])
    return int(asbd.mChannelsPerFrame) or 1, int(asbd.mFormatFlags), int(asbd.mBitsPerChannel)


def audio_samples(sample_buffer: object) -> np.ndarray | None:
    """A ScreenCaptureKit audio buffer as ``(frames, channels)`` int16, or ``None``."""
    frames = int(CoreMedia.CMSampleBufferGetNumSamples(sample_buffer))
    if frames <= 0:
        return None
    description = CoreMedia.CMSampleBufferGetFormatDescription(sample_buffer)
    asbd = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(description)
    if asbd is None:
        return None
    channels, flags, bits = _stream_format(asbd)

    block = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
    if block is None:
        return None
    length = int(CoreMedia.CMBlockBufferGetDataLength(block))
    status, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
    if status != 0 or data is None:
        return None

    if flags & _FLAG_FLOAT and bits == 32:
        values = np.frombuffer(bytes(data), dtype=np.float32)
        as_int = np.clip(values * 32768.0, -32768, 32767).astype(SAMPLE_DTYPE)
    elif bits == 16:
        as_int = np.frombuffer(bytes(data), dtype=SAMPLE_DTYPE)
    else:
        msg = f"unexpected audio format: {bits}-bit, flags {flags:#x}"
        raise AudioError(msg)

    if as_int.size < frames * channels:
        return None
    as_int = as_int[: frames * channels]
    if flags & _FLAG_NON_INTERLEAVED:
        # One plane per channel, back to back in the block buffer.
        return np.ascontiguousarray(as_int.reshape(channels, frames).T)
    return as_int.reshape(frames, channels)


def presentation_seconds(sample_buffer: object) -> float | None:
    """The buffer's presentation time in host-clock seconds, or ``None`` when invalid."""
    pts = CoreMedia.CMSampleBufferGetPresentationTimeStamp(sample_buffer)
    seconds = float(CoreMedia.CMTimeGetSeconds(pts))
    return seconds if math.isfinite(seconds) and seconds > 0 else None


class ScreenCaptureAudioStream:
    """Capture the game's audio (``pid``) or every app's (``pid=None``).

    Same surface as the Windows streams: ``start``, ``read``, ``drain``, ``close``, chunks
    stamped in seconds since :attr:`start_time` on the shared clock.
    """

    def __init__(
        self,
        pid: int | None,
        *,
        max_queued_chunks: int = DEFAULT_QUEUE_CHUNKS,
    ) -> None:
        self.pid = pid
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        self.name = (
            f"ScreenCaptureKit app audio (pid {pid})"
            if pid is not None
            else "ScreenCaptureKit audio of every app"
        )
        self.dropped = 0
        #: Buffers whose presentation time was unusable and were stamped on arrival.
        self.unstamped = 0
        self._queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=max_queued_chunks)
        self._start_time = 0.0
        self._stream: _Stream | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        try:
            content = shareable_content()
            display = _display(content, None)
            if self.pid is not None:
                app = _application(content, self.pid)
                if app is None:
                    msg = f"ScreenCaptureKit does not list an app with pid {self.pid}"
                    raise AudioError(msg)
                content_filter = SCK.SCContentFilter.alloc()
                content_filter = (
                    content_filter.initWithDisplay_includingApplications_exceptingWindows_(
                        display, [app], []
                    )
                )
            else:
                content_filter = SCK.SCContentFilter.alloc()
                content_filter = (
                    content_filter.initWithDisplay_excludingApplications_exceptingWindows_(
                        display, [], []
                    )
                )
            config = _base_config()
            config.setCapturesAudio_(True)
            config.setSampleRate_(SAMPLE_RATE)
            config.setChannelCount_(CHANNELS)
            config.setExcludesCurrentProcessAudio_(True)
            # Video cannot be switched off; keep it as small and as rare as allowed.
            config.setWidth_(2)
            config.setHeight_(2)
            config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))
            config.setQueueDepth_(3)
            self._stream = _Stream(
                content_filter,
                config,
                self._on_buffer,
                (SCK.SCStreamOutputTypeScreen, SCK.SCStreamOutputTypeAudio),
            )
            self._start_time = clock.now()
            self._stream.start()
        except AudioError:
            raise
        except Exception as exc:  # anything from the framework is a device problem here
            raise AudioError(str(exc)) from exc

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream = None

    def __enter__(self) -> ScreenCaptureAudioStream:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def start_time(self) -> float:
        return self._start_time

    # -- data --------------------------------------------------------------------------

    def read(self, timeout: float | None = 0.5) -> AudioChunk | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> Iterator[AudioChunk]:
        while True:
            try:
                yield self._queue.get_nowait()
            except queue.Empty:
                return

    def _on_buffer(self, sample_buffer: object, output_type: int) -> None:
        """ScreenCaptureKit's queue. Keep it short: convert, enqueue, return."""
        if output_type != SCK.SCStreamOutputTypeAudio:
            return
        arrived = clock.now()
        samples = audio_samples(sample_buffer)
        if samples is None:
            return
        captured = presentation_seconds(sample_buffer)
        if captured is None or abs(captured - arrived) > 1.0:
            # Not on the host clock after all: stamp by arrival, minus the buffer's length.
            self.unstamped += 1
            captured = arrived - samples.shape[0] / self.sample_rate
        try:
            self._queue.put_nowait(AudioChunk(t=captured - self._start_time, samples=samples))
        except queue.Full:
            self.dropped += 1
            if self.dropped in (1, 10, 100, 1000):
                logger.warning("audio queue full; dropped %d chunks", self.dropped)


# --------------------------------------------------------------------------------------
# video
# --------------------------------------------------------------------------------------


def _frame_complete(sample_buffer: object) -> bool:
    attachments = CoreMedia.CMSampleBufferGetSampleAttachmentsArray(sample_buffer, False)
    if not attachments:
        return False
    status = attachments[0].get(SCK.SCStreamFrameInfoStatus)
    return status is not None and int(status) == SCK.SCFrameStatusComplete


def frame_pixels(sample_buffer: object) -> np.ndarray | None:
    """A BGRA video buffer as a BGR ``(height, width, 3)`` array, copied out."""
    pixel_buffer = CoreMedia.CMSampleBufferGetImageBuffer(sample_buffer)
    if pixel_buffer is None:
        return None
    Quartz.CVPixelBufferLockBaseAddress(pixel_buffer, _LOCK_READ_ONLY)
    try:
        width = int(Quartz.CVPixelBufferGetWidth(pixel_buffer))
        height = int(Quartz.CVPixelBufferGetHeight(pixel_buffer))
        stride = int(Quartz.CVPixelBufferGetBytesPerRow(pixel_buffer))
        base = Quartz.CVPixelBufferGetBaseAddress(pixel_buffer)
        raw = np.frombuffer(base.as_buffer(stride * height), dtype=np.uint8)
        image = raw.reshape(height, stride)[:, : width * 4].reshape(height, width, 4)
        return np.ascontiguousarray(image[:, :, :3])
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(pixel_buffer, _LOCK_READ_ONLY)


class ScreenCaptureFrames:
    """ScreenCaptureKit over one region of one display. The fast path on macOS."""

    backend_name = "screencapturekit"

    def __init__(self, region: Rect, monitor: MonitorInfo, target_fps: int) -> None:
        self.region = region
        self._interval = 1.0 / max(1, target_fps)
        self._lock = threading.Lock()
        self._fresh = threading.Event()
        self._latest: np.ndarray | None = None

        local = region.translated(-monitor.rect.left, -monitor.rect.top)
        local = local.clamped_to(Rect(0, 0, monitor.rect.width, monitor.rect.height))
        try:
            content = shareable_content()
            display = _display(content, monitor.handle)
            content_filter = SCK.SCContentFilter.alloc()
            content_filter = content_filter.initWithDisplay_excludingApplications_exceptingWindows_(
                display, [], []
            )
            config = _base_config()
            config.setSourceRect_(
                Quartz.CGRectMake(local.left, local.top, local.width, local.height)
            )
            config.setWidth_(monitor.to_pixels(local.width))
            config.setHeight_(monitor.to_pixels(local.height))
            config.setPixelFormat_(_PIXEL_FORMAT_BGRA)
            config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, max(1, target_fps)))
            config.setQueueDepth_(3)
            self._stream = _Stream(
                content_filter, config, self._on_buffer, (SCK.SCStreamOutputTypeScreen,)
            )
            self._stream.start()
        except Exception as exc:
            msg = f"ScreenCaptureKit capture failed: {exc}"
            raise CaptureError(msg) from exc

    def _on_buffer(self, sample_buffer: object, output_type: int) -> None:
        if output_type != SCK.SCStreamOutputTypeScreen or not _frame_complete(sample_buffer):
            return
        image = frame_pixels(sample_buffer)
        if image is None:
            return
        with self._lock:
            self._latest = image
        self._fresh.set()

    def grab(self) -> np.ndarray | None:
        """A new frame if one arrives within two frame intervals, else the last one."""
        first = self._latest is None
        if self._fresh.wait(1.0 if first else 2 * self._interval):
            self._fresh.clear()
        with self._lock:
            return self._latest

    def close(self) -> None:
        self._stream.stop()
