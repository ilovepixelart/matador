"""E2E: SSE live updates - enqueue a job through the backend and the sidebar
count refreshes in the open page, no manual reload."""

import asyncio

from playwright.sync_api import Page, expect
from toro import Queue, Worker

from .conftest import PREFIX, QUEUE, URL, reset_queue


def test_sse_refreshes_sidebar_count_on_enqueue(page: Page, base_url, seeded, drive):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#sidebar")).to_contain_text("3 wait")

    async def _enqueue():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.add("zeta", {"n": "zeta"})
        # toro publishes on job lifecycle (complete/fail), not on enqueue - emit the
        # same event a worker would. Publish a few times over ~1.5s to bridge the SSE
        # connection-establishment window: pub/sub has no replay, so the first event
        # can land before the client has subscribed; a later one always lands after.
        for _ in range(6):
            await q.redis.publish(q.keys.events, '{"event":"completed"}')
            await asyncio.sleep(0.25)
        await q.close()

    drive(_enqueue())
    # the page is still open; SSE should push the new count without a reload
    expect(page.locator("#sidebar")).to_contain_text("4 wait")


def test_last_finish_of_a_burst_lands(page: Page, base_url, drive):
    """Two jobs finish 100 ms apart. The refresh the first finish triggers reads the
    state while the second job is still running, so the second finish has to be
    announced too, or that job stays listed as active until the 8 s heartbeat."""
    state: dict = {}

    async def _start():
        q = await reset_queue()
        gates = [asyncio.Event(), asyncio.Event()]

        async def proc(job):
            await gates[job.data["i"]].wait()

        for i in range(2):
            await q.add(f"batch-{i}", {"i": i})
        w = Worker(QUEUE, proc, url=URL, prefix=PREFIX, concurrency=2, stalled_interval=0)
        state.update(q=q, w=w, gates=gates, task=asyncio.create_task(w.run()))
        for _ in range(300):
            if (await q.counts())["active"] == 2:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("both jobs should be running")

    async def _finish_both():
        state["gates"][0].set()
        await asyncio.sleep(0.1)
        state["gates"][1].set()

    async def _stop():
        await state["w"].stop(grace_period=1)
        state["task"].cancel()
        await state["q"].close()

    drive(_start())
    try:
        # opened with both already running, so first paint shows them: this test is
        # about a FINISH being announced, not a claim (which toro does not publish)
        page.goto(f"{base_url}/queues/{QUEUE}?state=active")
        rows = page.locator("#jobs .jobs-table > details")
        expect(rows).to_have_count(2)

        drive(_finish_both())

        expect(rows).to_have_count(0, timeout=2000)  # neither is still listed as active
        expect(page.locator("#sidebar")).to_contain_text(
            "0 active", timeout=2000
        )  # a separate region
    finally:
        drive(_stop())
