"""E2E: SSE live updates - enqueue a job through the backend and the sidebar
count refreshes in the open page, no manual reload."""

import asyncio

from playwright.sync_api import Page, expect
from toro import Queue

from .conftest import PREFIX, QUEUE, URL


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
