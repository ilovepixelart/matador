"""Integration: what the sidebar's sparklines cost, and what they still show.

The sidebar refreshes up to 2.5 times a second for every viewer, and each refresh
read every queue's 60 minute buckets. A minute that has closed does not change.
"""

import asyncio
from itertools import pairwise

from toro import Queue, Worker

from matador.service import Service

from .conftest import PREFIX, QUEUE

URL = "redis://localhost:6379"


async def _completed_once(q: Queue):
    return (await q.counts())["completed"] == 1


async def test_a_refresh_rereads_only_the_open_minutes(q, monkeypatch):
    """The first refresh reads the whole window; the next one reads the two open
    minutes and keeps the closed ones, and hands back the same 60-point series."""
    svc = Service([QUEUE], url=URL, prefix=PREFIX, connection=q.redis)
    queue = svc.queues[QUEUE]
    asked: list[int] = []
    real_metrics = queue.metrics

    async def counting(*, minutes: int = 60):
        asked.append(minutes)
        return await real_metrics(minutes=minutes)

    monkeypatch.setattr(queue, "metrics", counting)
    first = (await svc.overview())[0]["spark"]
    second = (await svc.overview())[0]["spark"]

    assert asked == [60, 2]
    stamps = [p["timestamp"] for p in second]
    assert len(stamps) == 60
    assert all(b - a == 60_000 for a, b in pairwise(stamps))  # one point per minute
    assert stamps[-1] >= first[-1]["timestamp"]  # ends at the current minute


async def test_a_finish_in_the_open_minute_shows_on_the_next_refresh(q):
    """What is kept is only what cannot change: a job finishing now lands in the
    current minute, and the next refresh shows it."""
    svc = Service([QUEUE], url=URL, prefix=PREFIX, connection=q.redis)
    before = (await svc.overview())[0]["spark"]
    assert sum(p["completed"] for p in before) == 0

    await q.add("j", {})
    worker = Worker(QUEUE, _noop, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(500):  # up to 10 s
            if await _completed_once(q):
                break
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()
        task.cancel()
    assert await _completed_once(q)

    after = (await svc.overview())[0]["spark"]
    assert sum(p["completed"] for p in after) == 1


async def _noop(job):
    return None
