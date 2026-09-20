"""Integration: the global concurrency cap in the dashboard - what the service
reports about it and what the metrics strip and the workers list render.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from toro import Queue, Worker

from matador.service import Service

from .conftest import PREFIX, QUEUE


async def _noop(job):
    return None


@asynccontextmanager
async def live(q: Queue, *workers: Worker):
    """Run real workers until their presence records are up; stop them on exit."""
    tasks = [asyncio.create_task(w.run()) for w in workers]
    try:
        for _ in range(200):
            if len(await q.workers()) >= len(workers):
                break
            await asyncio.sleep(0.02)
        yield
    finally:
        for w in workers:
            await w.stop(grace_period=1)
        for t in tasks:
            t.cancel()


def worker(processor=_noop, **kw) -> Worker:
    return Worker(QUEUE, processor, prefix=PREFIX, stalled_interval=0, **kw)


@pytest.fixture
async def svc(q):
    return Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX, connection=q.redis)


async def test_service_reports_cap_states(q, svc):
    """The cap is a worker option, so the queue's cap is whatever its live workers
    agree on: nothing, one value, or a disagreement worth flagging."""
    assert (await svc.metrics(QUEUE))["cap"] is None  # no workers at all

    async with live(q, worker()):
        assert (await svc.metrics(QUEUE))["cap"] is None  # a live worker, but no cap

    async with live(q, worker(global_concurrency=3), worker(global_concurrency=3)):
        cap = (await svc.metrics(QUEUE))["cap"]
        assert (cap["limit"], cap["mixed"], cap["values"]) == (3, False, [3])

    async with live(q, worker(global_concurrency=3), worker(global_concurrency=5)):
        cap = (await svc.metrics(QUEUE))["cap"]
        assert (cap["limit"], cap["mixed"], cap["values"]) == (None, True, [3, 5])

    async with live(q, worker(global_concurrency=3), worker()):
        cap = (await svc.metrics(QUEUE))["cap"]
        assert (cap["limit"], cap["mixed"], cap["values"]) == (None, True, [0, 3])
