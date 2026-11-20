"""Lure timers and the decision of when to (re)apply one.

Only one lure is on the pole at a time, so the scheduler tracks a single active lure: the
highest-priority enabled one. It is due when it was never applied or when less than
``refresh_margin_s`` remains — checked between casts, so a lure is never applied in the
middle of a channel. The timer starts when the application finishes (the lure's cast time
has passed), which is when the buff actually lands.
"""

from __future__ import annotations

from engine.config import Lure, LureConfig


class LureScheduler:
    def __init__(self, config: LureConfig) -> None:
        self.config = config
        self._expires_at: float | None = None
        self._applied_name: str | None = None
        self._forced = False

    @property
    def lure(self) -> Lure | None:
        """The lure this profile uses: enabled, highest priority, first on ties."""
        enabled = [lure for lure in self.config.lures if lure.enabled]
        if not enabled:
            return None
        return max(enabled, key=lambda lure: lure.priority)

    def due(self, now: float) -> Lure | None:
        """The lure to apply before the next cast, or ``None``."""
        lure = self.lure
        if lure is None:
            return None
        if self._forced:
            return lure
        if not self.config.auto_apply:
            return None
        if self._expires_at is None or self._applied_name != lure.name:
            return lure
        if self._expires_at - now <= self.config.refresh_margin_s:
            return lure
        return None

    def applied(self, lure: Lure, now: float) -> None:
        self._applied_name = lure.name
        self._expires_at = now + lure.duration_min * 60.0
        self._forced = False

    def apply_now(self) -> None:
        """The UI's "Apply now": apply before the next cast regardless of the timer."""
        self._forced = True

    def remaining(self, now: float) -> float | None:
        """Seconds left on the active lure, for the countdown; ``None`` if none is on."""
        if self._expires_at is None:
            return None
        return max(0.0, self._expires_at - now)
