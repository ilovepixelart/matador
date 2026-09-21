"""The cadence of the live stream: every timing decision it makes, and nothing else.

A plain throttle fires on the first event of a window and discards the rest. For a
"something changed, go and look" signal that is wrong: the refresh the first event
triggered has already read the state, so a change that lands later in the window is
in nobody's repaint. A rate here emits at once when quiet, at most once per interval,
and once more when a window that saw a change reopens. It also emits when it has sent
nothing for a heartbeat - the first time as soon as it exists, because a stream that
has just subscribed has announced nothing yet.

The clock is an argument, never read here, so every rule is exact under test. The
stream only performs what `Cadence` decides.
"""

from __future__ import annotations

from collections.abc import Mapping


class Coalescer:
    """One refresh rate: a leading edge, a trailing edge, and a heartbeat."""

    def __init__(self, interval: float, heartbeat: float) -> None:
        if interval <= 0 or heartbeat <= 0:
            # at zero the next wake would not lie in the future: the stream would spin
            msg = "interval and heartbeat must be positive"
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

        Always later than the last poll, and a poll then always emits: a loop that
        sleeps until the earliest of these can neither spin nor wake for nothing.
        """
        return self._reopens_at if self._owed else max(self._reopens_at, self._beat_at)

    def hears(self, now: float) -> bool:
        """Whether a change arriving now would alter anything.

        It would not while the window is closed and an emit is already owed.
        """
        return now >= self._reopens_at or not self._owed


class Cadence:
    """Every rate of one stream, by event name."""

    def __init__(self, intervals: Mapping[str, float], heartbeat: float) -> None:
        self._rates = {name: Coalescer(interval, heartbeat) for name, interval in intervals.items()}

    def due(self, now: float, *, changed: bool) -> list[str]:
        """Advance every rate to `now` and name the ones to emit, in order."""
        return [name for name, rate in self._rates.items() if rate.poll(now, changed=changed)]

    def next_wake(self, now: float) -> tuple[float, bool]:
        """When to look again, and whether a change should wake the stream sooner.

        toro publishes one event per job. While every rate has a closed window and
        an emit already owed, another change alters nothing, so the stream waits on
        the clock: a storm wakes it per window, not per job.
        """
        rates = self._rates.values()
        return min(rate.wake_at for rate in rates), any(rate.hears(now) for rate in rates)
