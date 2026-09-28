"""Integration: what the event stream holds, and gives back.

A mounted sub-app's lifespan never runs, so `Service.close()` never runs either: a
dashboard mounted inside someone else's app would hold its shared subscription, and
the connection under it, for the life of the host process - for a page nobody has
open. The broadcaster starts with the first viewer, so it has to end with the last.
"""

import asyncio

from toro import Queue

from matador.service import Service

from .conftest import PREFIX, QUEUE

URL = "redis://localhost:6379"


async def _first_chunk(stream) -> str:
    return await asyncio.wait_for(anext(stream), timeout=5)


async def test_the_broadcaster_is_given_back_with_the_last_viewer(q: Queue):
    svc = Service([QUEUE], url=URL, prefix=PREFIX, connection=q.redis)
    stream = svc.event_stream()

    await _first_chunk(stream)  # subscribes and starts the shared listener
    assert svc._broadcast_task is not None
    assert await q.redis.pubsub_channels(q.keys.events) != []

    await stream.aclose()

    assert svc._broadcast_task is None, "the listener outlived every viewer"
    assert await q.redis.pubsub_channels(q.keys.events) == [], "the subscription is still open"


async def test_a_second_viewer_keeps_the_broadcaster(q: Queue):
    """One tab closing must not cut the stream under the other: the subscription is
    shared, which is the whole reason N tabs cost one connection."""
    svc = Service([QUEUE], url=URL, prefix=PREFIX, connection=q.redis)
    first, second = svc.event_stream(), svc.event_stream()
    await _first_chunk(first)
    await _first_chunk(second)

    await first.aclose()

    assert svc._broadcast_task is not None
    assert await q.redis.pubsub_channels(q.keys.events) != []
    await second.aclose()
    assert svc._broadcast_task is None


async def test_a_viewer_after_the_last_one_left_gets_a_stream(q: Queue):
    """Stopping is not a one-way door: the next viewer starts it again."""
    svc = Service([QUEUE], url=URL, prefix=PREFIX, connection=q.redis)
    first = svc.event_stream()
    await _first_chunk(first)
    await first.aclose()

    second = svc.event_stream()
    assert await _first_chunk(second) == "retry: 3000\n\n"
    assert svc._broadcast_task is not None
    await second.aclose()


async def test_a_viewer_arriving_as_the_last_one_leaves_keeps_its_stream(q: Queue, monkeypatch):
    """The last viewer's leave releases the broadcaster under the lock. A viewer arriving
    meanwhile saw the listener task still alive, skipped the lock, and registered on a
    broadcaster about to be released: its stream ended at once, and the page sat out
    the client's reconnect delay right after opening."""
    svc = Service([QUEUE], url=URL, prefix=PREFIX, connection=q.redis)
    releasing = asyncio.Event()
    proceed = asyncio.Event()
    real_release = svc._release_broadcaster

    async def held_release():
        releasing.set()
        await proceed.wait()  # the leave holds the lock here, mid-release
        await real_release()

    monkeypatch.setattr(svc, "_release_broadcaster", held_release)
    first = svc.event_stream()
    await _first_chunk(first)
    leaving = asyncio.create_task(first.aclose())
    await asyncio.wait_for(releasing.wait(), 5)

    second = svc.event_stream()
    arriving = asyncio.create_task(_first_chunk(second))
    await asyncio.sleep(0.1)  # an arrival that skips the lock is through by now
    proceed.set()
    await leaving
    assert await arriving == "retry: 3000\n\n"

    assert svc._broadcast_task is not None, "the newcomer is registered on no broadcaster"
    assert await q.redis.pubsub_channels(q.keys.events) != []
    await second.aclose()
    assert svc._broadcast_task is None
