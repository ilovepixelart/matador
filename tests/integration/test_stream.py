"""Integration: the SSE event_stream contract — a reconnect directive, then a
`changed` signal whenever a queue publishes a job event. Bounded by the 8s
heartbeat backstop so it can never hang (the HTTP stream itself is E2E territory).
"""

import asyncio

from matador.service import Service

from .conftest import PREFIX, QUEUE


async def test_event_stream_starts_then_signals_change(q):
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    agen = svc.event_stream()
    try:
        first = await asyncio.wait_for(agen.__anext__(), timeout=3)
        assert first.startswith("retry:")  # SSE auto-reconnect directive

        # a published job event should produce a `changed` frame promptly
        await q.redis.publish(q.keys.events, '{"event":"completed"}')
        frame = await asyncio.wait_for(agen.__anext__(), timeout=10)
        assert "event: changed" in frame  # tells the client to refresh
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
    # N dashboard tabs must cost ONE pubsub connection, not N — otherwise open
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


async def test_stream_ends_cleanly_if_the_subscription_dies(q):
    # The events subscription dying mid-stream must END the stream (the browser
    # reconnects on the retry hint), never raise out of the response — and the
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
