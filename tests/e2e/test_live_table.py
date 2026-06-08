"""E2E: the live job table. A job enqueued into an idle, empty tab must appear on its own
(no reload) — the end-to-end proof that enqueue now emits a `changed` signal the table
reacts to, across every state (not just active)."""

from playwright.sync_api import Page, expect
from toro import Queue

from .conftest import PREFIX, QUEUE


def test_enqueued_job_appears_live_on_empty_tab(page: Page, base_url, drive):
    async def clear():
        q = Queue(QUEUE, prefix=PREFIX)
        keys = await q.redis.keys(q.keys.base + "*")
        if keys:
            await q.redis.delete(*keys)
        await q.close()

    drive(clear())
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs")).to_contain_text("No wait jobs")
    page.wait_for_timeout(2000)  # let the SSE connection establish (no replay if we miss it)

    async def enqueue():
        q = Queue(QUEUE, prefix=PREFIX)
        await q.add("liveappear", {"n": 1})
        await q.close()

    drive(enqueue())
    # no page.reload() — the table must refresh itself off the SSE `changed` signal.
    # timeout < the 8s heartbeat, so this proves the ENQUEUE event drove it, not the backstop.
    expect(page.locator("#jobs")).to_contain_text("liveappear", timeout=6000)
