"""A rate limit that never loses the last change.

A plain throttle fires on the first event of a window and discards the rest. For a
"something changed, go and look" signal that is wrong: the refresh the first event
triggered has already read the state, so a change that lands later in the window is
in nobody's repaint. This one has a trailing edge as well: a closed window remembers
that it was signalled, and emits once when it reopens.

The clock is an argument, never read here, so the behavior is exact under test.
"""

from __future__ import annotations


class Coalescer:
    """Emit at most once per `interval`, and once more after the last signal."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.reopens_at = float("-inf")  # the window is open while now >= this
        self._dirty = False  # signalled while closed: owes one emit on reopening

    def signal(self, now: float) -> bool:
        """Record a change. True means emit now (the window was open)."""
        if now >= self.reopens_at:
            self.reopens_at = now + self.interval
            self._dirty = False  # this emit covers anything that was pending
            return True
        self._dirty = True
        return False

    def due(self, now: float) -> bool:
        """Report, once, that a closed window reopened owing an emit."""
        if self._dirty and now >= self.reopens_at:
            self.reopens_at = now + self.interval
            self._dirty = False
            return True
        return False

    @property
    def deadline(self) -> float | None:
        """When the pending emit falls due, or None when nothing is owed."""
        return self.reopens_at if self._dirty else None
