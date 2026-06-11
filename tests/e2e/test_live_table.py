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


def test_live_refresh_keeps_rows_inside_the_grid(page: Page, base_url, seeded, drive):
    # idiomorph once relocated id-keyed rows OUTSIDE .jobs-table on refresh
    # (an intermediate wrapper confused it) — the grid stopped applying and
    # every column collapsed. Assert the structure survives a live morph.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.wait_for_timeout(2000)  # let the SSE connection establish

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
    # used to morph the wrong view into #jobs — and recycled htmx closures could
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
        "data-view", f"{QUEUE}:failed"  # the stale wait-view never rendered
    )
