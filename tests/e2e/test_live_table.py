"""E2E: the live job table. A job enqueued into an idle, empty tab must appear on its own
(no reload) - the end-to-end proof that enqueue now emits a `changed` signal the table
reacts to, across every state (not just active)."""

import asyncio

from playwright.sync_api import Page, expect
from toro import Queue, Worker

from .conftest import PREFIX, QUEUE, wait_for_live


def test_enqueued_job_appears_live_on_empty_tab(page: Page, base_url, drive):
    async def clear():
        q = Queue(QUEUE, prefix=PREFIX)
        keys = await q.redis.keys(q.keys.base + "*")
        if keys:
            await q.redis.delete(*keys)
        await q.close()

    drive(clear())
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs")).to_contain_text("No waiting jobs")
    wait_for_live(page)  # a change published before the stream subscribes reaches nobody

    async def enqueue():
        q = Queue(QUEUE, prefix=PREFIX)
        await q.add("liveappear", {"n": 1})
        await q.close()

    drive(enqueue())
    # no page.reload() - the table must refresh itself off the SSE `changed` signal.
    # timeout < the 8s heartbeat, so this proves the ENQUEUE event drove it, not the backstop.
    expect(page.locator("#jobs")).to_contain_text("liveappear", timeout=6000)


def test_live_refresh_keeps_rows_inside_the_grid(page: Page, base_url, seeded, drive):
    # idiomorph once relocated id-keyed rows OUTSIDE .jobs-table on refresh
    # (an intermediate wrapper confused it) - the grid stopped applying and
    # every column collapsed. Assert the structure survives a live morph.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    wait_for_live(page)  # a change published before the stream subscribes reaches nobody

    async def enqueue():
        q = Queue(QUEUE, prefix=PREFIX)
        await q.add("late-arrival", {"n": 1})
        await q.close()

    drive(enqueue())
    expect(page.locator("#jobs details")).to_have_count(4)  # refresh happened
    inside = page.locator("#jobs .jobs-table > details")
    expect(inside).to_have_count(4)  # ...and every row is still a grid row


def test_stale_jobs_fragment_cannot_eat_the_panel(page: Page, base_url, seeded):
    # Regression: a live-refresh response landing after the user switched views
    # used to morph the wrong view into #jobs - and recycled htmx closures could
    # then replace the ENTIRE #queue-panel with a bare jobs fragment. Inject a
    # stale fragment exactly the way a leftover listener would and assert both
    # defenses hold: the wrong view is dropped, the panel chrome survives.
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    page.wait_for_timeout(500)
    page.evaluate(
        f"htmx.ajax('GET', '/queues/{QUEUE}/jobs?state=wait&page=1',"
        " {target: '#jobs', swap: 'morph:innerHTML'})"
    )
    page.evaluate(
        f"htmx.ajax('GET', '/queues/{QUEUE}/jobs?state=wait&page=1',"
        " {target: '#queue-panel', swap: 'innerHTML'})"
    )
    page.wait_for_timeout(500)
    expect(page.locator("#queue-panel h1")).to_be_visible()  # chrome intact
    expect(page.locator("#jobs [data-view]")).to_have_attribute(
        "data-view",
        f"{QUEUE}:failed",  # the stale wait-view never rendered
    )


def test_a_held_job_appears_and_leaves(page: Page, base_url, drive):
    """HJ-005: the held tab end to end. A job waiting on a key shows there with the key
    it waits on, and leaves the moment the key frees."""

    async def clear():
        q = Queue(QUEUE, prefix=PREFIX)
        keys = await q.redis.keys(q.keys.base + "*")
        if keys:
            await q.redis.delete(*keys)
        await q.close()

    drive(clear())
    page.goto(f"{base_url}/queues/{QUEUE}?state=held")
    expect(page.locator("#jobs")).to_contain_text("No held jobs")
    wait_for_live(page)  # a change published before the stream subscribes reaches nobody

    async def enqueue():
        q = Queue(QUEUE, prefix=PREFIX)
        await q.add("holder", {}, concurrency_key="invoice-42")
        await q.add("behind", {}, concurrency_key="invoice-42")
        await q.close()

    drive(enqueue())
    expect(page.locator("#jobs")).to_contain_text("behind", timeout=6000)
    expect(page.locator("#jobs")).to_contain_text("invoice-42")  # why it is not running

    async def drain():
        # A worker finishing the holder is what frees the key, and the `completed`
        # events are what the page refreshes on. Removing the holder would free the
        # key too, but REMOVE_JOB publishes nothing, so the tab would not hear about
        # it until the heartbeat.
        async def proc(job):
            return job.name

        q = Queue(QUEUE, prefix=PREFIX)
        worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
        task = asyncio.create_task(worker.run())
        for _ in range(500):
            if (await q.counts())["completed"] >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the worker never finished both jobs")
        await worker.stop()
        task.cancel()
        await q.close()

    drive(drain())
    # under the 8s heartbeat, so this proves the finish events drove it, not the backstop
    expect(page.locator("#jobs")).to_contain_text("No held jobs", timeout=6000)


def test_a_cancelled_job_appears_on_its_own_tab(page: Page, base_url, drive):
    """The cancelled tab end to end: a job stopped on purpose shows there, and the
    failed tab stays empty because a cancellation is not a failure."""

    async def clear():
        q = Queue(QUEUE, prefix=PREFIX)
        keys = await q.redis.keys(q.keys.base + "*")
        if keys:
            await q.redis.delete(*keys)
        await q.close()

    drive(clear())
    page.goto(f"{base_url}/queues/{QUEUE}?state=cancelled")
    expect(page.locator("#jobs")).to_contain_text("No cancelled jobs")
    wait_for_live(page)  # a change published before the stream subscribes reaches nobody

    async def add_then_cancel():
        q = Queue(QUEUE, prefix=PREFIX)
        job = await q.add("stopped-on-purpose", {})
        assert await q.cancel_job(job.id) is True
        await q.close()

    drive(add_then_cancel())
    # under the 8s heartbeat, so the cancelled event drove it, not the backstop
    expect(page.locator("#jobs")).to_contain_text("stopped-on-purpose", timeout=6000)

    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    expect(page.locator("#jobs")).to_contain_text("No failed jobs")
