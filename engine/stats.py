"""Session statistics and the catch log.

Counts what the state machine did: casts, hooks ("catches"), timeouts ("misses") and lures.
With auto loot on, the bot cannot see what it caught — a catch here means "a bite was heard
and the bobber was hooked". The FishBridge addon (M7) can confirm loot later.

Time spent paused does not count towards the session, so catches per hour stay honest
across a coffee break.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class StatsSnapshot(BaseModel):
    """What the UI shows. Field names serialise to camelCase to match ``protocol.ts``."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    started_at: float | None
    elapsed_ms: int
    casts: int
    catches: int
    misses: int
    consecutive_misses: int
    lures_applied: int
    success_rate: float
    catches_per_hour: float


@dataclass(frozen=True, slots=True)
class CatchRecord:
    """One hook. ``reaction_s`` is bite-heard to hook-pressed, ``wait_s`` cast to bite."""

    t: float
    wait_s: float
    reaction_s: float


@dataclass
class SessionStats:
    started_at: float | None = None
    casts: int = 0
    catches: int = 0
    misses: int = 0
    consecutive_misses: int = 0
    lures_applied: int = 0
    log: list[CatchRecord] = field(default_factory=list)
    _active_s: float = 0.0
    _running_since: float | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def start(self, now: float) -> None:
        if self.started_at is None:
            self.started_at = now
        self.resume(now)

    def pause(self, now: float) -> None:
        if self._running_since is not None:
            self._active_s += now - self._running_since
            self._running_since = None

    def resume(self, now: float) -> None:
        if self._running_since is None:
            self._running_since = now

    def active_seconds(self, now: float) -> float:
        running = now - self._running_since if self._running_since is not None else 0.0
        return self._active_s + running

    # -- events ------------------------------------------------------------------------

    def record_cast(self) -> None:
        self.casts += 1

    def record_catch(self, record: CatchRecord) -> None:
        self.catches += 1
        self.consecutive_misses = 0
        self.log.append(record)

    def record_miss(self) -> None:
        self.misses += 1
        self.consecutive_misses += 1

    def record_lure(self) -> None:
        self.lures_applied += 1

    # -- views -------------------------------------------------------------------------

    def snapshot(self, now: float) -> StatsSnapshot:
        active = self.active_seconds(now)
        finished = self.catches + self.misses
        return StatsSnapshot(
            started_at=self.started_at,
            elapsed_ms=int(active * 1000),
            casts=self.casts,
            catches=self.catches,
            misses=self.misses,
            consecutive_misses=self.consecutive_misses,
            lures_applied=self.lures_applied,
            success_rate=self.catches / finished if finished else 0.0,
            catches_per_hour=self.catches * 3600 / active if active > 0 else 0.0,
        )
