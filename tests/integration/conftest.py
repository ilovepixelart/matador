"""Integration fixtures: a matador app wired to a seeded toro queue on real Redis,
driven through an in-process ASGI client (httpx). A fresh, isolated queue per test.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from toro import Queue, Worker

from matador import create_app

PREFIX = "matadortest"
QUEUE = "testq"


async def _clear(q: Queue) -> None:
    keys = await q.redis.keys(q.keys.base + "*")
    if keys:
        await q.redis.delete(*keys)


@pytest.fixture
async def q():
    """A clean, isolated toro queue used to seed + inspect what the app serves."""
    queue = Queue(QUEUE, prefix=PREFIX)
    await _clear(queue)
    yield queue
    await _clear(queue)
    await queue.close()


@pytest.fixture
async def seeded(q):
    """A known dataset: 1 completed, 1 failed, 3 waiting (alpha/beta/gamma),
    1 scheduler (→ 1 delayed occurrence). Returns the ids that matter."""
    done = await q.add("okjob", {"recipient": "ada@example.com"})
    bad = await q.add("badjob", {"why": "bad"}, attempts=1)

    async def proc(job):
        if job.name == "badjob":
            raise RuntimeError("boom")
        return {"done": True}

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        c = await q.counts()
        if c["completed"] >= 1 and c["failed"] >= 1:
            break
        await asyncio.sleep(0.02)
    await worker.stop()
    task.cancel()

    waits = [(await q.add(name, {"n": name})).id for name in ("alpha", "beta", "gamma")]
    await q.add_scheduler("nightly", cron="0 0 * * *", name="rollup")
    return {"completed": done.id, "failed": bad.id, "waits": waits}


@pytest.fixture
async def client(q):
    """An httpx client bound to the matador app (same Redis/prefix as `q`)."""
    app = create_app([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def hx(**extra) -> dict:
    """Headers that make a request look like an htmx fragment swap."""
    return {"HX-Request": "true", **extra}
