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
