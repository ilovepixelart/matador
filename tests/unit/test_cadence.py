"""Unit: the stream's cadence - every timing decision `/stream` makes.

The clock is an argument, so every case is exact and nothing sleeps. The stream itself
only performs what `Cadence` decides, which is why its rules can all be checked here:
at once when quiet, at most once per interval, once more after the last change of a
burst, a heartbeat per rate, a first beat on subscription, and a loop that can neither
spin nor wake once per job event.
"""

import math
import random
from itertools import pairwise

import pytest

from matador.cadence import Cadence, Rate

EPS = 1e-9


def _opened(interval: float, heartbeat: float = 100.0, at: float = 0.0) -> Rate:
    """A rate whose opening beat has been sent at `at`."""
    c = Rate(interval, heartbeat)
    assert c.poll(at, changed=False) is True
    return c


# ---- one rate ---------------------------------------------------------------------


def test_a_new_rate_beats_at_once():
    # a stream that has just subscribed has announced nothing yet
    assert Rate(1.0, 8.0).poll(5.0, changed=False) is True


def test_a_change_in_an_open_window_emits_at_once():
    c = _opened(1.0)
    assert c.poll(10.0, changed=True) is True  # the quiet case pays no latency


def test_changes_in_a_closed_window_emit_exactly_once_when_it_reopens():
    c = _opened(1.0, at=10.0)
    assert not c.poll(10.1, changed=True)
    assert not c.poll(10.5, changed=True)
    assert not c.poll(10.9, changed=True)  # however many: they are one pending emit
    assert c.wake_at == 11.0

    assert not c.poll(10.99, changed=False)
    assert c.poll(11.0, changed=False)  # the last change of the burst lands here
    assert not c.poll(11.0, changed=False)  # once


def test_a_window_left_clean_emits_nothing_until_the_heartbeat():
    c = _opened(1.0, heartbeat=8.0, at=10.0)
    assert not c.poll(11.0, changed=False)
    assert not c.poll(17.99, changed=False)
    assert c.wake_at == 18.0
    assert c.poll(18.0, changed=False)


def test_the_trailing_emit_closes_the_window_again():
    c = _opened(1.0, at=10.0)
    c.poll(10.5, changed=True)
    assert c.poll(11.0, changed=False)
    assert not c.poll(11.2, changed=True)  # closed until 12.0: the cap holds across the tail
    assert c.wake_at == 12.0
    assert c.poll(12.0, changed=False)


def test_a_change_after_a_missed_deadline_emits_once_not_twice():
    c = _opened(1.0, at=10.0)
    c.poll(10.5, changed=True)  # pending for 11.0, but nobody polls then
    assert c.poll(11.5, changed=True) is True  # the window is open: emit now
    assert not c.poll(11.6, changed=False)  # and that emit covered the pending one


def test_storm_is_capped_and_lands_its_tail():
    """A change every 10 ms for 3.5 s, polled every 10 ms: at most one emit per
    interval, and one last emit after the final change."""
    c = Rate(1.0, 100.0)
    emits = [t / 100 for t in range(700) if c.poll(t / 100, changed=t <= 350)]
    assert emits == [0.0, 1.0, 2.0, 3.0, 4.0]  # 4.0: the change at 3.50, not dropped


def test_the_heartbeat_counts_from_the_rates_own_last_emit():
    c = _opened(0.4, heartbeat=8.0, at=0.0)
    c.poll(0.1, changed=True)
    assert c.poll(0.4, changed=False)  # its tail
    assert c.wake_at == pytest.approx(8.4)  # whatever any other rate does meanwhile


def test_a_beat_due_behind_a_closed_window_waits_for_it_to_reopen():
    """A heartbeat shorter than the interval falls due while the window is closed. It
    cannot be sent yet, and the next wake is the reopening - never a time already past."""
    c = _opened(1.0, heartbeat=0.5, at=0.0)
    assert not c.poll(0.5, changed=False)
    assert c.wake_at == 1.0
    assert c.poll(1.0, changed=False)


@pytest.mark.parametrize("seed", range(20))
def test_the_next_wake_is_always_in_the_future_and_never_wasted(seed):
    rng = random.Random(seed)  # noqa: S311 - reproducible traffic, not a secret
    c = Rate(rng.choice([0.1, 0.4, 1.0, 5.0]), rng.choice([0.3, 2.0, 8.0]))
    now = 0.0
    for _ in range(400):
        c.poll(now, changed=rng.random() < 0.5)
        assert c.wake_at > now  # a loop that sleeps until then cannot spin
        if rng.random() < 0.5:
            now = c.wake_at
            assert c.poll(now, changed=False)  # waking then always has something to send
        else:
            now += rng.random() * (c.wake_at - now)


# ---- the whole stream, simulated -----------------------------------------------------


class Ideal:
    """A clock that reads the real time, and a timer that fires exactly on its deadline."""

    def read(self, t: float) -> float:
        return t

    def fires(self, t: float, delay: float) -> float:
        return t + max(delay, 0.0)


class Millis:
    """uvloop, which `uvicorn.run` picks whenever it is installed: the clock reads whole
    milliseconds and a timer waits a whole number of them. `(k + 400) / 1000` can be
    one float step BELOW `k / 1000 + 0.4`, so the clock reads short of a deadline the
    timer has just fired for."""

    TURN = 40e-6  # a loop turn, when a timer rounds to zero milliseconds

    def __init__(self, uptime: float) -> None:
        self.uptime = uptime

    def read(self, t: float) -> float:
        return math.floor((self.uptime + t) * 1000) / 1000

    def fires(self, t: float, delay: float) -> float:
        ms = max(0, round(delay * 1000))
        if ms == 0:
            return t + self.TURN
        tick = math.floor((self.uptime + t) * 1000) + ms
        return tick / 1000 - self.uptime + 1e-7  # just inside that millisecond


def drive(cadence: Cadence, events: list[float], until: float, env=None) -> tuple[list, int]:
    """Be the stream, turn for turn: report the clock, whether a change arrived and
    whether the last wait ran its course; send what is due; wait as told. Returns the
    emits (by the clock's reading) and the number of looks."""
    env = env or Ideal()
    events = sorted(events)
    i, t, emits, looks, timed_out = 0, 0.0, [], 0, False
    while t <= until:
        changed = False
        while i < len(events) and events[i] <= t:
            changed, i = True, i + 1
        looks += 1
        assert looks < 100_000, "the stream spins"
        now = env.read(t)
        emits += [(now, name) for name in cadence.due(now, changed=changed, timed_out=timed_out)]
        wake_at, hears = cadence.next_wake()
        fires = env.fires(t, wake_at - now)
        timed_out = not (hears and i < len(events) and events[i] < fires)
        nxt = fires if timed_out else events[i]
        assert nxt > t, "the stream would spin"
        t = nxt
    return emits, looks


def _times(emits: list, name: str) -> list[float]:
    return [t for t, n in emits if n == name]


SHIPPED = {"changed-fast": 0.4, "changed": 1.0, "changed-slow": 5.0}


def test_two_changes_100ms_apart_with_the_shipped_rates():
    """The measured bug, and the one a review found after it: the second change is
    announced at each rate's reopening, and each rate then beats 8 s after ITS OWN
    last emit - the slow rate's tail at 5 s postpones nobody else's."""
    emits, _ = drive(Cadence(SHIPPED, 8.0), [0.0, 0.1], until=13.5)
    assert _times(emits, "changed-fast") == pytest.approx([0.0, 0.4, 8.4])
    assert _times(emits, "changed") == pytest.approx([0.0, 1.0, 9.0])
    assert _times(emits, "changed-slow") == pytest.approx([0.0, 5.0, 13.0])


def test_a_quiet_stream_opens_then_beats():
    emits, looks = drive(Cadence(SHIPPED, 8.0), [], until=16.5)
    assert [t for t, _ in emits] == pytest.approx([0.0] * 3 + [8.0] * 3 + [16.0] * 3)
    assert looks == 3  # nothing in between


def test_a_heartbeat_under_two_of_the_intervals_cannot_spin():
    emits, looks = drive(Cadence(SHIPPED, 0.5), [], until=2.5)
    assert _times(emits, "changed-fast") == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
    assert _times(emits, "changed") == pytest.approx([0.0, 1.0, 2.0])  # at its interval
    assert looks <= 2 * len(emits)


def test_a_storm_wakes_the_stream_per_window_not_per_event():
    storm = [i / 1000 for i in range(10_000)]  # 10 s at a thousand changes a second
    emits, looks = drive(Cadence(SHIPPED, 8.0), storm, until=16.0)
    assert len(_times(emits, "changed-fast")) == pytest.approx(26, abs=1)
    assert looks <= 2 * len(emits) + 2, f"{looks} looks for {len(emits)} emits"


@pytest.mark.parametrize("seed", range(30))
def test_invariants_hold_for_any_traffic(seed):
    rng = random.Random(seed)  # noqa: S311 - reproducible traffic, not a secret
    heartbeat = rng.choice([0.3, 2.0, 8.0])
    events = sorted(rng.uniform(0, 20) for _ in range(rng.choice([0, 3, 40, 2000])))
    emits, looks = drive(Cadence(SHIPPED, heartbeat), events, until=30.0)

    for name, interval in SHIPPED.items():
        times = _times(emits, name)
        gaps = [b - a for a, b in pairwise(times)]
        assert all(g >= interval - EPS for g in gaps), f"{name}: two emits inside one interval"
        assert all(g <= max(heartbeat, interval) + EPS for g in gaps), f"{name}: went silent"
        for e in events:  # every change is announced, within the rate's own interval
            assert any(e - EPS <= t <= e + interval + EPS for t in times), f"{name}: lost {e}"
    assert looks <= 2 * len(emits) + 2  # never once per event


# ---- clocks that are not ideal --------------------------------------------------------


@pytest.mark.parametrize("uptime", [60.0, 3600.0, 21_600.0, 86_400.0, 864_000.0])
def test_a_millisecond_clock_cannot_make_the_stream_spin(uptime):
    """At some uptimes (an hour, six hours, ten days) the clock reads one float step
    short of the deadline its timer fired for. A stream that believed the clock over
    its own timer would find nothing due and turn at CPU speed until the next tick."""
    storm = [i / 500 for i in range(5000)]  # 10 s, a change every 2 ms
    emits, looks = drive(Cadence(SHIPPED, 8.0), storm, until=12.0, env=Millis(uptime))
    assert looks <= 2 * len(emits) + 2, f"{looks} looks for {len(emits)} emits"
    fast = _times(emits, "changed-fast")
    assert all(b - a >= 0.4 - 0.0015 for a, b in pairwise(fast)), "the cap, to the millisecond"


def test_a_wait_that_ran_its_course_is_at_its_deadline_whatever_the_clock_reads():
    c = Cadence({"changed": 1.0}, 8.0)
    assert c.due(100.0, changed=True, timed_out=False) == ["changed"]
    c.due(100.5, changed=True, timed_out=False)  # owed for 101.0
    assert c.next_wake() == (101.0, False)
    short = math.nextafter(101.0, 0.0)  # the clock, one float step shy
    assert c.due(short, changed=False, timed_out=False) == []  # woken early by something else
    assert c.due(short, changed=False, timed_out=True) == ["changed"]


def test_due_names_the_rates_in_their_given_order():
    assert Cadence(SHIPPED, 8.0).due(0.0, changed=False, timed_out=False) == list(SHIPPED)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
def test_times_must_be_positive_and_finite(bad):
    with pytest.raises(ValueError, match="positive"):
        Rate(bad, 8.0)
    with pytest.raises(ValueError, match="positive"):
        Rate(1.0, bad)


def test_a_cadence_needs_a_rate():
    with pytest.raises(ValueError, match="at least one"):
        Cadence({}, 8.0)
