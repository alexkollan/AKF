"""Lure timers and the decision of when to (re)apply one.

Only one lure is on the pole at a time, so the scheduler tracks a single active lure: the
highest-priority enabled one. It is due when it was never applied or when less than
``refresh_margin_s`` remains — checked between casts, so a lure is never applied in the
middle of a channel. The timer starts when the application finishes (the lure's cast time
has passed), which is when the buff actually lands.
"""

from __future__ import annotations

from engine.config import Lure, LureConfig

#: After applying a lure, ignore "no lure" from FishBridge this long: the addon reads the
#: game a few times a second, and a stale "off" right after we put one on would apply a
#: second lure at the next cast.
OBSERVE_GRACE_S = 5.0


class LureScheduler:
    def __init__(self, config: LureConfig) -> None:
        self.config = config
        self._expires_at: float | None = None
        self._applied_name: str | None = None
        self._applied_at = float("-inf")
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
        self._applied_at = now
        self._expires_at = now + lure.duration_min * 60.0
        self._forced = False

    def observe(self, active: bool, remaining_s: float | None, now: float) -> None:
        """What the game says about the lure (FishBridge), which beats our own timer.

        A lure that dropped off early, or one put on by hand, is picked up here: the timer
        follows the game instead of assuming every application lasted its full duration.
        """
        lure = self.lure
        if not active:
            if now - self._applied_at >= OBSERVE_GRACE_S:
                self._expires_at = None
            return
        if lure is not None and remaining_s is not None:
            self._applied_name = lure.name
            self._expires_at = now + remaining_s

    def apply_now(self) -> None:
        """The UI's "Apply now": apply before the next cast regardless of the timer."""
        self._forced = True

    def remaining(self, now: float) -> float | None:
        """Seconds left on the active lure, for the countdown; ``None`` if none is on."""
        if self._expires_at is None:
            return None
        return max(0.0, self._expires_at - now)
