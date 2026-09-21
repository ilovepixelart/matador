"""Unit: the coalescer - a rate limit with a leading AND a trailing edge.

The clock is an argument, so every case here is exact and nothing sleeps.
"""

from matador.coalescer import Coalescer


def test_a_signal_in_an_open_window_emits_at_once():
    c = Coalescer(1.0)
    assert c.signal(10.0) is True  # the quiet case pays no latency


def test_signals_in_a_closed_window_emit_exactly_once_when_it_reopens():
    c = Coalescer(1.0)
    assert c.signal(10.0)
    assert not c.signal(10.1)
    assert not c.signal(10.5)
    assert not c.signal(10.9)  # however many: they are one pending emit
    assert c.deadline == 11.0

    assert not c.due(10.99)
    assert c.due(11.0)  # the last change of the burst lands here
    assert not c.due(11.0)  # once
    assert c.deadline is None


def test_a_window_left_clean_emits_nothing():
    c = Coalescer(1.0)
    assert c.signal(10.0)
    assert c.deadline is None
    assert not c.due(11.0)
    assert not c.due(99.0)


def test_the_trailing_emit_closes_the_window_again():
    c = Coalescer(1.0)
    c.signal(10.0)
    c.signal(10.5)
    assert c.due(11.0)
    assert not c.signal(11.2)  # closed until 12.0: the cap holds across the tail
    assert c.deadline == 12.0
    assert c.due(12.0)


def test_a_signal_after_a_missed_deadline_emits_once_not_twice():
    c = Coalescer(1.0)
    c.signal(10.0)
    c.signal(10.5)  # pending for 11.0, but nobody polls
    assert c.signal(11.5) is True  # the window is open: emit now
    assert c.deadline is None  # and that emit covered the pending one
    assert not c.due(11.6)


def test_storm_is_capped_and_lands_its_tail():
    """A signal every 10 ms for 3.5 s, polled every 10 ms: at most one emit per
    interval, and one last emit after the final signal."""
    c = Coalescer(1.0)
    emits: list[float] = []
    for tick in range(351):  # signals at 0.00 .. 3.50
        now = tick / 100
        if c.signal(now) or c.due(now):
            emits.append(now)
    for tick in range(351, 700):  # quiet afterwards: only the clock moves
        now = tick / 100
        if c.due(now):
            emits.append(now)

    assert emits == [0.0, 1.0, 2.0, 3.0, 4.0]  # 4.0: the change at 3.50, not dropped
