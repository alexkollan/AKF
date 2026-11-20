"""Process loopback: capture the audio of one process (and its children) and nothing else.

Endpoint loopback (``engine.audio.LoopbackStream``) hears everything Windows plays: Discord,
a browser tab, music. Every one of those is a potential false bite. Windows 10 2004+ /
Windows 11 can instead tap a single process's audio before it is mixed, which is exactly
what a splash detector wants: WoW's sound effects and nothing else.

There is no Python binding for this, so it is driven through COM with ``comtypes``,
following Microsoft's *ApplicationLoopback* sample:

1. ``ActivateAudioInterfaceAsync`` on the virtual device ``VAD\\Process_Loopback`` with
   ``AUDIOCLIENT_ACTIVATION_PARAMS`` naming the target pid;
2. wait for the completion handler, take the ``IAudioClient``;
3. initialise it in shared loopback mode with a format we choose (the engine converts);
4. poll ``IAudioCaptureClient`` on a worker thread.

Each packet carries a QPC timestamp of when it was captured, which is the same clock as
``engine.clock.now()``. So chunk timestamps here are exact — no guessing across silence,
unlike the endpoint stream.

Everything COM happens on the stream's own MTA thread. Nothing here touches asyncio.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from collections.abc import Iterator
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_int,
    c_int64,
    c_long,
    c_ubyte,
    c_uint32,
    c_uint64,
    wintypes,
)
from types import TracebackType

import comtypes
import numpy as np
from comtypes import COMMETHOD, GUID, HRESULT, COMObject, IUnknown

from engine import clock
from engine.audio import DEFAULT_QUEUE_CHUNKS, SAMPLE_DTYPE, AudioChunk, AudioError

logger = logging.getLogger(__name__)

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

WAVE_FORMAT_PCM = 1
VT_BLOB = 65

#: Requested capture format. The audio engine converts WoW's mix to this for us.
SAMPLE_RATE = 48000
CHANNELS = 2
BITS = 16
BUFFER_HNS = 2_000_000  # 200 ms ring buffer, in 100 ns units
POLL_S = 0.005
ACTIVATE_TIMEOUT_S = 5.0

#: First Windows build with process loopback (Windows 10 2004).
MIN_BUILD = 19041


# --------------------------------------------------------------------------------------
# structures
# --------------------------------------------------------------------------------------


class _ProcessLoopbackParams(Structure):
    _fields_ = (
        ("TargetProcessId", wintypes.DWORD),
        ("ProcessLoopbackMode", c_int),
    )  # ctypes layout


class _ActivationParams(Structure):
    _fields_ = (("ActivationType", c_int), ("ProcessLoopbackParams", _ProcessLoopbackParams))


class _Blob(Structure):
    _fields_ = (("cbSize", wintypes.ULONG), ("pBlobData", POINTER(c_ubyte)))


class _PropVariant(Structure):
    """Just enough of PROPVARIANT to carry a VT_BLOB (24 bytes on x64)."""

    _fields_ = (
        ("vt", wintypes.USHORT),
        ("wReserved1", wintypes.USHORT),
        ("wReserved2", wintypes.USHORT),
        ("wReserved3", wintypes.USHORT),
        ("blob", _Blob),
    )


class _WaveFormatEx(Structure):
    _pack_ = 1
    _fields_ = (
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    )


# --------------------------------------------------------------------------------------
# interfaces
# --------------------------------------------------------------------------------------


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "GetActivateResult",
            (["out"], POINTER(c_long), "activateResult"),
            (["out"], POINTER(POINTER(IUnknown)), "activatedInterface"),
        ),
    )


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "ActivateCompleted",
            (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation"),
        ),
    )


class IAgileObject(IUnknown):
    """Marker interface: the handler may be called from any apartment."""

    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = ()


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "Initialize",
            (["in"], c_int, "ShareMode"),
            (["in"], wintypes.DWORD, "StreamFlags"),
            (["in"], c_int64, "hnsBufferDuration"),
            (["in"], c_int64, "hnsPeriodicity"),
            (["in"], POINTER(_WaveFormatEx), "pFormat"),
            (["in"], POINTER(GUID), "AudioSessionGuid"),
        ),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "frames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_int64), "latency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "padding")),
        COMMETHOD(
            [],
            HRESULT,
            "IsFormatSupported",
            (["in"], c_int, "ShareMode"),
            (["in"], POINTER(_WaveFormatEx), "pFormat"),
            (["out"], POINTER(POINTER(_WaveFormatEx)), "ppClosestMatch"),
        ),
        COMMETHOD(
            [], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(_WaveFormatEx)), "format")
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetDevicePeriod",
            (["out"], POINTER(c_int64), "default"),
            (["out"], POINTER(c_int64), "minimum"),
        ),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
        COMMETHOD(
            [],
            HRESULT,
            "GetService",
            (["in"], POINTER(GUID), "riid"),
            (["out"], POINTER(POINTER(IUnknown)), "service"),
        ),
    )


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "GetBuffer",
            (["out"], POINTER(POINTER(c_ubyte)), "data"),
            (["out"], POINTER(c_uint32), "frames"),
            (["out"], POINTER(wintypes.DWORD), "flags"),
            (["out"], POINTER(c_uint64), "devicePosition"),
            (["out"], POINTER(c_uint64), "qpcPosition"),
        ),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "frames")),
    )


class _CompletionHandler(COMObject):
    """Signals when activation finishes. Agile, because Windows calls it from its own MTA."""

    _com_interfaces_ = (IActivateAudioInterfaceCompletionHandler, IAgileObject)

    def __init__(self) -> None:
        super().__init__()
        self.done = threading.Event()

    def ActivateCompleted(self, this: object, operation: object) -> int:  # COM method name
        self.done.set()
        return 0


def _activate(pid: int) -> object:
    """Activate an IAudioClient that captures ``pid``'s process tree. MTA thread only."""
    params = _ActivationParams(
        ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        ProcessLoopbackParams=_ProcessLoopbackParams(
            TargetProcessId=pid,
            ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE,
        ),
    )
    variant = _PropVariant(vt=VT_BLOB)
    variant.blob.cbSize = ctypes.sizeof(params)
    variant.blob.pBlobData = ctypes.cast(ctypes.pointer(params), POINTER(c_ubyte))

    handler = _CompletionHandler()
    handler_ptr = handler.QueryInterface(IActivateAudioInterfaceCompletionHandler)
    operation = POINTER(IActivateAudioInterfaceAsyncOperation)()

    activate = ctypes.WinDLL("Mmdevapi.dll").ActivateAudioInterfaceAsync
    activate.argtypes = (
        wintypes.LPCWSTR,
        POINTER(GUID),
        POINTER(_PropVariant),
        POINTER(IActivateAudioInterfaceCompletionHandler),
        POINTER(POINTER(IActivateAudioInterfaceAsyncOperation)),
    )
    activate.restype = HRESULT

    iid = IAudioClient._iid_
    activate(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        byref(iid),
        byref(variant),
        handler_ptr,
        byref(operation),
    )
    if not handler.done.wait(ACTIVATE_TIMEOUT_S):
        msg = f"process loopback activation for pid {pid} timed out"
        raise AudioError(msg)

    hr, unknown = operation.GetActivateResult()
    if hr < 0:
        msg = f"process loopback activation for pid {pid} failed (HRESULT {hr & 0xFFFFFFFF:#010x})"
        raise AudioError(msg)
    return unknown.QueryInterface(IAudioClient)


def supported() -> bool:
    """Whether this Windows build has process loopback at all."""
    import sys

    return sys.getwindowsversion().build >= MIN_BUILD


# --------------------------------------------------------------------------------------
# stream
# --------------------------------------------------------------------------------------


class ProcessLoopbackStream:
    """Capture one process tree's audio. Same surface as ``engine.audio.LoopbackStream``.

    ``read``/``drain`` hand out :class:`~engine.audio.AudioChunk` objects whose ``t`` is
    seconds since :attr:`start_time` on the shared clock, taken from WASAPI's own capture
    timestamps.
    """

    def __init__(
        self,
        pid: int,
        *,
        max_queued_chunks: int = DEFAULT_QUEUE_CHUNKS,
    ) -> None:
        if not supported():
            msg = "process loopback needs Windows 10 2004 or later"
            raise AudioError(msg)
        self.pid = pid
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        self.name = f"process loopback (pid {pid})"
        self.dropped = 0
        self.discontinuities = 0

        self._queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=max_queued_chunks)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._start_time = 0.0
        self._thread = threading.Thread(target=self._run, name=f"proc-loopback-{pid}", daemon=True)

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._start_time = clock.now()
        self._thread.start()
        self._ready.wait(ACTIVATE_TIMEOUT_S + 1.0)
        if self._error is not None:
            raise AudioError(str(self._error)) from self._error
        if not self._ready.is_set():
            msg = "process loopback did not start"
            raise AudioError(msg)

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def __enter__(self) -> ProcessLoopbackStream:
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

    # -- worker ------------------------------------------------------------------------

    def _run(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        client = None
        capture = None
        try:
            client = _activate(self.pid)
            block_align = self.channels * BITS // 8
            fmt = _WaveFormatEx(
                wFormatTag=WAVE_FORMAT_PCM,
                nChannels=self.channels,
                nSamplesPerSec=self.sample_rate,
                nAvgBytesPerSec=self.sample_rate * block_align,
                nBlockAlign=block_align,
                wBitsPerSample=BITS,
                cbSize=0,
            )
            client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK
                | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                BUFFER_HNS,
                0,
                byref(fmt),
                None,
            )
            capture = client.GetService(byref(IAudioCaptureClient._iid_)).QueryInterface(
                IAudioCaptureClient
            )
            client.Start()
            self._ready.set()
            self._pump(capture, block_align)
        except (
            BaseException
        ) as exc:  # surfaced to start() or logged; the thread must not die silently
            self._error = exc
            if self._ready.is_set():
                logger.exception("process loopback capture stopped")
            self._ready.set()
        finally:
            if client is not None:
                try:
                    client.Stop()
                except comtypes.COMError:
                    pass
            del capture, client
            comtypes.CoUninitialize()

    def _pump(self, capture: object, block_align: int) -> None:
        while not self._stop.is_set():
            frames_next = capture.GetNextPacketSize()
            if frames_next == 0:
                time.sleep(POLL_S)
                continue
            data, frames, flags, _device_pos, qpc = capture.GetBuffer()
            try:
                if frames == 0:
                    continue
                if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                    samples = np.zeros((frames, self.channels), dtype=SAMPLE_DTYPE)
                else:
                    raw = ctypes.string_at(data, frames * block_align)
                    samples = np.frombuffer(raw, dtype=SAMPLE_DTYPE).reshape(-1, self.channels)
                if flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY:
                    self.discontinuities += 1
            finally:
                capture.ReleaseBuffer(frames)

            t = clock.from_qpc(qpc) - self._start_time
            try:
                self._queue.put_nowait(AudioChunk(t=t, samples=samples))
            except queue.Full:
                self.dropped += 1
                if self.dropped in (1, 10, 100, 1000):
                    logger.warning("audio queue full; dropped %d chunks", self.dropped)
