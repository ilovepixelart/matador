"""Integration: the SSE event_stream contract - a reconnect directive, then a
`changed` signal whenever a queue publishes a job event. Bounded by the 8s
heartbeat backstop so it can never hang (the HTTP stream itself is E2E territory).
"""

import asyncio
import re
from typing import cast

import pytest
from redis.asyncio.client import PubSub

from matador.service import STREAM_RATES as RATES
from matador.service import Service, _confirm_subscribed

from .conftest import PREFIX, QUEUE


async def test_event_stream_starts_then_signals_change(q):
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    agen = svc.event_stream()
    try:
        first = await asyncio.wait_for(agen.__anext__(), timeout=3)
        assert first.startswith("retry:")  # SSE auto-reconnect directive

        # a published job event opens every rate at once: no latency when quiet
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        frames = [await asyncio.wait_for(agen.__anext__(), timeout=3) for _ in RATES]
        assert frames == [f"event: {name}\ndata: 1\n\n" for name in RATES]
    finally:
        await agen.aclose()
        await svc.close()


async def test_event_stream_stops_when_client_disconnects(q):
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)

    async def disconnected() -> bool:
        return True  # client is already gone

    # the generator must terminate (not hang on the 8s heartbeat) once disconnected
    frames = [f async for f in svc.event_stream(disconnected)]
    assert frames == ["retry: 3000\n\n"]  # just the directive, then a clean stop
    await svc.close()


async def test_concurrent_streams_share_one_subscription(q):
    # N dashboard tabs must cost ONE pubsub connection, not N - otherwise open
    # tabs exhaust the pool and starve the action routes.
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    a, b = svc.event_stream(), svc.event_stream()
    try:
        assert (await asyncio.wait_for(a.__anext__(), timeout=3)).startswith("retry:")
        assert (await asyncio.wait_for(b.__anext__(), timeout=3)).startswith("retry:")

        subs = int((await q.redis.pubsub_numsub(q.keys.events))[0][1])
        assert subs == 1, f"{subs} subscriptions for 2 streams"

        # one published event reaches BOTH streams
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        assert "changed" in await asyncio.wait_for(a.__anext__(), timeout=10)
        assert "changed" in await asyncio.wait_for(b.__anext__(), timeout=10)
    finally:
        await a.aclose()
        await b.aclose()
        await svc.close()


async def test_a_started_stream_is_already_subscribed(q):
    """The stream's first frame means the subscription EXISTS. redis-py's subscribe()
    returns once the command is written, so without waiting for Redis to confirm it a
    job event published right after the stream starts reaches nobody. Repeated: the
    race is between two connections and is lost only some of the time."""
    for _ in range(20):
        svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
        agen = svc.event_stream()
        try:
            assert (await asyncio.wait_for(agen.__anext__(), timeout=3)).startswith("retry:")
            assert int((await q.redis.pubsub_numsub(q.keys.events))[0][1]) == 1
        finally:
            await agen.aclose()
            await svc.close()


class _Replies:
    """A subscription that hands out scripted replies, then stays silent."""

    def __init__(self, *kinds: str) -> None:
        self.left = [{"type": kind} for kind in kinds]

    async def get_message(self, timeout: float) -> dict | None:
        if self.left:
            return self.left.pop(0)
        await asyncio.sleep(timeout)
        return None


async def test_confirmation_counts_channels_not_job_events():
    # a job event on an already confirmed channel can arrive between two confirmations
    replies = _Replies("subscribe", "message", "subscribe", "message")
    await _confirm_subscribed(cast("PubSub", replies), 2)
    assert replies.left == [{"type": "message"}]  # and it stops at the last one


async def test_unconfirmed_subscription_times_out(monkeypatch):
    monkeypatch.setattr("matador.service.SUBSCRIBE_TIMEOUT", 0.05)
    with pytest.raises(TimeoutError):
        await _confirm_subscribed(cast("PubSub", _Replies("subscribe")), 2)


async def test_stream_gives_up_cleanly_when_redis_never_confirms(q, monkeypatch):
    async def never(pubsub: PubSub, channels: int) -> None:
        raise TimeoutError

    monkeypatch.setattr("matador.service._confirm_subscribed", never)
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    try:

        async def drain() -> list[str]:
            return [f async for f in svc.event_stream()]

        # the retry hint, then a clean end: the browser tries again in 3 s
        assert await asyncio.wait_for(drain(), timeout=5) == ["retry: 3000\n\n"]
        # and the half-made subscription was closed, not left holding a connection
        for _ in range(100):
            if int((await q.redis.pubsub_numsub(q.keys.events))[0][1]) == 0:
                break
            await asyncio.sleep(0.02)
        assert int((await q.redis.pubsub_numsub(q.keys.events))[0][1]) == 0
    finally:
        await svc.close()


async def test_stream_ends_cleanly_if_the_subscription_dies(q):
    # The events subscription dying mid-stream must END the stream (the browser
    # reconnects on the retry hint), never raise out of the response - and the
    # next stream must come back on a fresh subscription.
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    agen = svc.event_stream()
    assert (await asyncio.wait_for(agen.__anext__(), timeout=3)).startswith("retry:")

    # Inject a fault into the live subscription: the next read raises.
    async def boom(*a, **kw):
        raise ConnectionError("subscription died")

    svc._broadcast_pubsub.get_message = boom
    await q.redis.publish(q.keys.events, "{}")  # unblock the in-flight read

    async def drain() -> list[str]:
        return [f async for f in agen]  # must complete, not raise or hang

    await asyncio.wait_for(drain(), timeout=5)

    # A new stream heals: fresh subscription, signals flow again.
    agen2 = svc.event_stream()
    try:
        assert (await asyncio.wait_for(agen2.__anext__(), timeout=3)).startswith("retry:")
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        assert "changed" in await asyncio.wait_for(agen2.__anext__(), timeout=10)
    finally:
        await agen2.aclose()
        await svc.close()


async def test_second_event_in_a_window_is_announced(q):
    """Two job events 100 ms apart. The refresh the first one triggers has already
    read the state, so the second must be announced too, at each rate's own
    cadence. Left to the 8 s heartbeat it would land after this test has ended."""
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    loop = asyncio.get_running_loop()
    seen: dict[str, list[float]] = {name: [] for name in RATES}
    t0 = 0.0

    async def read() -> None:
        async for frame in svc.event_stream():
            m = re.match(r"event: (\S+)", frame)
            if m and m.group(1) in seen:
                seen[m.group(1)].append(loop.time() - t0)

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.4)  # subscribed
        t0 = loop.time()
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        await asyncio.sleep(0.1)
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        await asyncio.sleep(max(RATES.values()) + 0.6)
    finally:
        reader.cancel()
        await svc.close()

    for name, interval in RATES.items():
        times = seen[name]
        assert len(times) == 2, f"{name}: {times}"  # the first event, then the tail
        assert times[0] < 0.15, f"{name}: the first event must emit at once, got {times}"
        assert interval - 0.05 <= times[1] <= interval + 0.4, f"{name}: {times}"


async def test_quiet_stream_opens_every_rate_as_its_heartbeat(q, monkeypatch):
    """With nothing sent for HEARTBEAT seconds every rate emits: it keeps the
    connection alive through proxies and covers the transitions toro never
    publishes. Shortened here; nothing is published at all."""
    monkeypatch.setattr("matador.service.HEARTBEAT", 0.5)
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    loop = asyncio.get_running_loop()
    seen: list[tuple[str, float]] = []
    t0 = loop.time()

    async def read() -> None:
        async for frame in svc.event_stream():
            m = re.match(r"event: (\S+)", frame)
            if m:
                seen.append((m.group(1), loop.time() - t0))

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.9)
    finally:
        reader.cancel()
        await svc.close()

    assert [name for name, _ in seen] == list(RATES)  # one beat, every rate, in order
    assert all(0.45 <= at <= 0.8 for _, at in seen), seen


async def test_each_rate_beats_on_its_own_clock(q, monkeypatch):
    """A rate's heartbeat counts from ITS last emit. A slow rate's trailing emit lands
    long after a fast rate went quiet, and must not push the fast rate's beat out:
    that beat is what bounds how stale a region on the fast rate can be."""
    monkeypatch.setattr("matador.service.STREAM_RATES", {"changed-fast": 0.1, "changed-slow": 1.2})
    monkeypatch.setattr("matador.service.HEARTBEAT", 2.0)
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    loop = asyncio.get_running_loop()
    fast: list[float] = []
    t0 = 0.0

    async def read() -> None:
        async for frame in svc.event_stream():
            if "changed-fast" in frame:
                fast.append(loop.time() - t0)  # noqa: PERF401 - cancelled mid-stream

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.4)  # subscribed
        t0 = loop.time()
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        await asyncio.sleep(0.05)
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        await asyncio.sleep(2.75)
    finally:
        reader.cancel()
        await svc.close()

    # at once, its tail at 0.1 s, then its beat 2 s after that. Counted from the slow
    # rate's tail (1.2 s) the beat would fall at 3.2 s, after this test has stopped.
    assert len(fast) == 3, fast
    assert 2.0 <= fast[2] <= 2.7, fast


async def test_a_beat_behind_a_closed_window_waits_for_it(q, monkeypatch):
    """A heartbeat shorter than a rate's interval falls due while that rate's window
    is still closed, and cannot be sent until it reopens. The stream has to wait for
    the reopening; woken by a deadline already in the past it would spin."""
    from matador import service

    monkeypatch.setattr(service, "HEARTBEAT", 0.5)  # under the 1 s and 5 s intervals
    waits = 0
    real_wait = service._wait_for_work

    async def counting(*args):
        nonlocal waits
        waits += 1
        await real_wait(*args)

    monkeypatch.setattr(service, "_wait_for_work", counting)
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    frames: list[str] = []

    async def read() -> None:
        async for frame in svc.event_stream():
            frames.append(frame)  # noqa: PERF401 - cancelled mid-stream

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(2.5)  # nothing is published: beats only
    finally:
        reader.cancel()
        await svc.close()

    assert waits < 40, f"the stream woke {waits} times in 2.5 quiet seconds"
    # and the beats still land: the fast rate every 0.5 s, the 1 s rate at its interval
    assert sum("changed-fast" in f for f in frames) >= 3
    assert sum(f.startswith("event: changed\n") for f in frames) >= 2


async def test_a_storm_does_not_wake_the_stream_per_event(q, monkeypatch):
    """300 job events back to back. While every window is closed an event can only
    mark one dirty, so the stream has nothing to do until the first reopens: it must
    wait on the clock, not wake once per event."""
    from matador import service

    waits = 0
    real_wait = service._wait_for_work

    async def counting(*args):
        nonlocal waits
        waits += 1
        await real_wait(*args)

    monkeypatch.setattr(service, "_wait_for_work", counting)
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    loop = asyncio.get_running_loop()
    frames: list[str] = []

    async def read() -> None:
        # appended one by one: this task is cancelled mid-stream, and a comprehension
        # that never completes would lose every frame it had collected
        async for frame in svc.event_stream():
            frames.append(frame)  # noqa: PERF401

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(0.4)  # subscribed
        waits = 0
        started = loop.time()
        for _ in range(300):
            await q.redis.publish(q.keys.events, '{"event":"completed"}')
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.2)
        elapsed = loop.time() - started
    finally:
        reader.cancel()
        await svc.close()

    # one wake per window that reopened, however long the runner took to publish
    allowed = 3 + elapsed * sum(1 / interval for interval in RATES.values())
    assert waits <= allowed, f"the stream woke {waits} times in {elapsed:.2f} s for 300 events"
    assert sum("changed-fast" in f for f in frames) >= 2  # and still announced them
