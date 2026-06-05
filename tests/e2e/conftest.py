"""E2E fixtures: a real (threaded) uvicorn server wired to a seeded toro queue on
the dev Redis — isolated by prefix — driven by a real browser via pytest-playwright.

The server is session-scoped (booting it is the expensive part); the queue state is
reset + reseeded per test so action tests stay isolated and deterministic.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time

import pytest
import uvicorn
from toro import Queue, Worker

from matador import create_app

PREFIX = "matadore2e"
QUEUE = "e2eq"
URL = "redis://localhost:6379"


class _ThreadedUvicorn(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # signals only work on the main thread
        pass

    @contextlib.contextmanager
    def run_in_thread(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        try:
            while not self.started:  # readiness wait — no fixed sleep
                time.sleep(1e-3)
            yield
        finally:
            self.should_exit = True
            thread.join()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def run_async():
    """Sync→async bridge: submit coroutines to a persistent loop on a background
    thread. Unlike asyncio.run(), this never touches asyncio's global/main-thread
    state, so e2e (sync) and integration (pytest-asyncio) coexist in one process."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


@pytest.fixture(scope="session")
def live_server():
    app = create_app([QUEUE], url=URL, prefix=PREFIX)
    config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
    server = _ThreadedUvicorn(config)
    with server.run_in_thread():
        yield f"http://127.0.0.1:{config.port}"


@pytest.fixture(scope="session")
def base_url(live_server):
    # session-scoped to satisfy pytest-base-url (bundled with pytest-playwright)
    return live_server


async def _seed() -> dict:
    q = Queue(QUEUE, url=URL, prefix=PREFIX)
    keys = await q.redis.keys(q.keys.base + "*")
    if keys:
        await q.redis.delete(*keys)
    done = await q.add("okjob", {"recipient": "ada@example.com"})
    await q.add("badjob", {"why": "bad"}, attempts=1)

    async def proc(job):
        if job.name == "badjob":
            raise RuntimeError("boom")
        return {"done": True}

    worker = Worker(QUEUE, proc, url=URL, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        c = await q.counts()
        if c["completed"] >= 1 and c["failed"] >= 1:
            break
        await asyncio.sleep(0.02)
    await worker.stop()
    task.cancel()
    waits = {name: (await q.add(name, {"n": name})).id for name in ("alpha", "beta", "gamma")}
    await q.close()
    return {"completed": done.id, "waits": waits}


@pytest.fixture
def seeded(run_async):
    """Reset + reseed the queue before the test; returns ids that matter."""
    return run_async(_seed())


async def _seed_many(n: int) -> None:
    q = Queue(QUEUE, url=URL, prefix=PREFIX)
    keys = await q.redis.keys(q.keys.base + "*")
    if keys:
        await q.redis.delete(*keys)
    for i in range(n):  # no worker here, so they all stay in `wait`
        await q.add("bulkjob", {"i": i})
    await q.close()


@pytest.fixture
def seeded_many(run_async):
    """Seed >1 page of waiting jobs (25) — for pagination + bulk-select tests."""
    run_async(_seed_many(25))


@pytest.fixture
def drive(run_async):
    """Run a coroutine against the same Redis the server reads (for live-update tests)."""

    def _run(coro):
        return run_async(coro)

    return _run
