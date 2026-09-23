"""E2E: SSE live updates - enqueue a job through the backend and the sidebar
count refreshes in the open page, no manual reload."""

import asyncio

from playwright.sync_api import Page, expect
from toro import Queue, Worker

from .conftest import PREFIX, QUEUE, URL, reset_queue, wait_for_live


def test_sse_refreshes_sidebar_count_on_enqueue(page: Page, base_url, seeded, drive):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    wait_for_live(page)
    expect(page.locator("#sidebar")).to_contain_text("3 wait")

    async def _enqueue():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        # toro announces the add itself. Pub/sub has no replay, so an add that lands
        # before the page's stream is subscribed is announced to nobody - and is still
        # shown, because a stream beats as soon as it is subscribed.
        await q.add("zeta", {"n": "zeta"})
        await q.close()

    drive(_enqueue())
    # the page is still open; SSE should push the new count without a reload
    expect(page.locator("#sidebar")).to_contain_text("4 wait")


def test_last_finish_of_a_burst_lands(page: Page, base_url, drive):
    """A job finishes just after the page repainted for the previous finish. That
    repaint read the state while this job was still running, and the window it opened
    is still closed, so the finish has to be announced when the window reopens, or
    the job stays listed as active until the 8 s heartbeat."""
    state: dict = {}

    async def _start():
        q = state["q"] = await reset_queue()
        gates = [asyncio.Event(), asyncio.Event()]

        async def proc(job):
            await gates[job.data["i"]].wait()

        for i in range(2):
            await q.add(f"batch-{i}", {"i": i})
        w = Worker(QUEUE, proc, url=URL, prefix=PREFIX, concurrency=2, stalled_interval=0)
        state.update(w=w, gates=gates, task=asyncio.create_task(w.run()))
        for _ in range(300):
            if (await q.counts())["active"] == 2:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("both jobs should be running")

    async def _finish(i: int):
        state["gates"][i].set()

    async def _stop():
        if "w" in state:
            await state["w"].stop(grace_period=1)
            state["task"].cancel()
        if "q" in state:
            await state["q"].close()

    try:
        drive(_start())
        # opened with both already running, so first paint shows them: this test is
        # about a FINISH being announced, not a claim (which toro does not publish)
        page.goto(f"{base_url}/queues/{QUEUE}?state=active")
        rows = page.locator("#jobs .jobs-table > details")
        expect(rows).to_have_count(2)

        drive(_finish(0))
        # the repaint for the first finish has landed, with the second job still in it:
        # from here a leading edge alone can never show the second finish
        expect(rows).to_have_count(1)
        drive(_finish(1))

        expect(rows).to_have_count(0, timeout=2000)  # not still listed as active
        expect(page.locator("#sidebar")).to_contain_text(
            "0 active", timeout=2000
        )  # a separate region
    finally:
        drive(_stop())


def test_the_page_says_whether_its_stream_is_connected(page: Page, base_url, seeded):
    """A dashboard that has stopped moving is either quiet or disconnected, and
    nothing on the page said which. It is also what a test waits for before
    publishing a change: one published before the stream subscribes reaches nobody.
    """
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")

    expect(page.locator("html")).to_have_attribute("data-sse", "open")
