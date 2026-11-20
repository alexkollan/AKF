"""The one clock everything in the engine is timestamped with.

On Windows, ``time.monotonic()`` is ``GetTickCount64`` and only moves in 15.6 ms steps —
half a frame at 30 fps, and a meaningful slice of the 300 ms bite-latency budget.
``time.perf_counter()`` is ``QueryPerformanceCounter`` with 100 ns resolution, and it is
also the clock WASAPI stamps audio packets with, so frames, audio and hotkey markers can
share a single timeline.

Never call ``time.monotonic()`` or ``time.time()`` for anything that gets compared against
a frame, an audio chunk or a marker. Use :func:`now`.
"""

from __future__ import annotations

import time

#: WASAPI reports QPC positions in 100-nanosecond units.
_QPC_UNIT_S = 1e-7


def now() -> float:
    """Seconds on the shared high-resolution clock (arbitrary origin)."""
    return time.perf_counter()


def from_qpc(qpc_100ns: int) -> float:
    """Convert a WASAPI ``u64QPCPosition`` into :func:`now` seconds."""
    return qpc_100ns * _QPC_UNIT_S


class SimClock:
    """A clock that only moves when told to: for tests and fast simulated sessions.

    ``now`` and ``sleep`` have the same shape as :func:`now` and ``time.sleep``, so a
    session loop can run on either without knowing which.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)
