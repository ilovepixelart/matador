"""The cadence of the live stream: every timing decision it makes, and nothing else.

A plain throttle fires on the first event of a window and discards the rest. For a
"something changed, go and look" signal that is wrong: the refresh the first event
triggered has already read the state, so a change that lands later in the window is
in nobody's repaint. A rate here emits at once when quiet, at most once per interval,
and once more when a window that saw a change reopens. It also emits when it has sent
nothing for a heartbeat - the first time as soon as it exists, because a stream that
has just subscribed has announced nothing yet.

The clock is an argument, never read here, so every rule is exact under test. The
stream only reports facts (the clock's reading, whether a change arrived, whether its
last wait ran its course) and performs what `Cadence` decides.
"""

from __future__ import annotations

import math
from collections.abc import Mapping


class Rate:
    """One refresh rate: a leading edge, a trailing edge, and a heartbeat."""

    def __init__(self, interval: float, heartbeat: float) -> None:
        if not (0 < interval < math.inf and 0 < heartbeat < math.inf):
            # at zero the next wake would not lie in the future: the stream would spin
            msg = "interval and heartbeat must be positive and finite"
            raise ValueError(msg)
        self.interval = interval
        self.heartbeat = heartbeat
        self._reopens_at = float("-inf")  # the window is open while now >= this
        self._beat_at = float("-inf")  # nothing sent since before this: a beat is owed
        self._owed = False  # something to announce, held back by a closed window

    def poll(self, now: float, *, changed: bool) -> bool:
        """Advance to `now`. True means emit; `changed` reports a change since the last poll."""
        if changed or now >= self._beat_at:  # a heartbeat is a change nobody published
            self._owed = True
        if not self._owed or now < self._reopens_at:
            return False
        self._owed = False
        self._reopens_at = now + self.interval
        self._beat_at = now + self.heartbeat
        return True

    @property
    def wake_at(self) -> float:
        """The earliest time a poll could emit with no further change.

        Later than the last poll, and a poll at that time always emits: a loop that
        sleeps until the earliest of these neither spins nor wakes for nothing.
        """
        return self._reopens_at if self._owed else max(self._reopens_at, self._beat_at)

    @property
    def hears(self) -> bool:
        """Whether a change could alter anything before the next wake.

        Not once an emit is owed: after a poll, owed means the window is closed, and
        a change can only owe what is owed already.
        """
        return not self._owed


class Cadence:
    """Every rate of one stream, by event name."""

    def __init__(self, intervals: Mapping[str, float], heartbeat: float) -> None:
        if not intervals:
            msg = "a cadence needs at least one rate"
            raise ValueError(msg)
        self._rates = {name: Rate(interval, heartbeat) for name, interval in intervals.items()}
        self._wake_at = float("-inf")

    def due(self, now: float, *, changed: bool, timed_out: bool) -> list[str]:
        """Advance every rate and name the ones to emit, in the order they were given.

        `timed_out` says the wait asked for by `next_wake` ran its course. Then the
        wake time HAS come, whatever the clock reads: a coarse clock (uvloop's ticks
        in milliseconds) can read one float step short of a deadline its own timer
        just fired for, and a stream that believed it would find nothing due and turn
        at full speed until the next tick.
        """
        if timed_out:
            now = max(now, self._wake_at)
        return [name for name, rate in self._rates.items() if rate.poll(now, changed=changed)]

    def next_wake(self) -> tuple[float, bool]:
        """When to look again, and whether a change should wake the stream sooner.

        toro publishes one event per job. Once every rate owes an emit, another change
        alters nothing, so the stream waits on the clock: a storm wakes it per window,
        not per job.
        """
        rates = self._rates.values()
        self._wake_at = min(rate.wake_at for rate in rates)
        return self._wake_at, any(rate.hears for rate in rates)
