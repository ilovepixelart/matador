"""E2E: the <details> accordion lazy-loads job detail over htmx on first open."""

from playwright.sync_api import Page, expect
from toro import Queue

from .conftest import PREFIX, QUEUE, URL


def test_accordion_lazy_loads_detail_on_open(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=completed")
    row = page.locator("#jobs details").first
    expect(row).to_contain_text("okjob")
    # the return-value section lives only in the lazily-loaded detail (the summary
    # shows a data preview, so we key off detail-only content instead)
    expect(row).not_to_contain_text("result")
    row.locator("summary").click()
    expect(row).to_contain_text("result")  # detail fetched on toggle
    expect(row).to_contain_text("done")  # the actual return value


def test_accordion_expands_scheduled_occurrence(page: Page, base_url, seeded, drive):
    """A repeatable job materializes a real delayed occurrence whose id contains
    colons (`repeat:<sid>:<when>`). Its row must still lazy-load detail on expand —
    colons in the id must not break the htmx target (they're a CSS pseudo-class)."""

    async def _add_scheduler():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.add_scheduler("nightly", every=3_600_000, name="rollup", data={"k": "v"})
        await q.close()

    drive(_add_scheduler())
    page.goto(f"{base_url}/queues/{QUEUE}?state=delayed")
    row = page.locator('#jobs details[id*="repeat:"]')  # the colon-id occurrence
    expect(row).to_contain_text("rollup")  # it's listed in the jobs table
    expect(row).to_contain_text("scheduled")  # badged as a scheduler occurrence
    expect(row).not_to_contain_text("opts")  # detail not loaded yet
    row.locator("summary").click()
    expect(row).to_contain_text("opts")  # detail lazy-loaded on expand (was broken)


def test_reopen_after_live_refresh_still_loads_the_detail(page: Page, base_url, seeded, drive):
    # `toggle once` + a live morph lost the reference: the morph emptied the
    # closed row's body but the preserved node remembered it already fetched —
    # reopening expanded nothing, forever.
    from toro import Queue

    from .conftest import PREFIX

    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.wait_for_timeout(2000)  # SSE connect
    row = page.locator("#jobs details").first
    row.locator("summary").click()
    expect(row).to_contain_text("opts")  # detail loaded
    row.locator("summary").click()  # close it

    async def background_noise():
        q = Queue(QUEUE, prefix=PREFIX)
        await q.add("background-noise", {})
        await q.close()

    drive(background_noise())  # changed event → the list morphs while closed
    expect(page.locator("#jobs")).to_contain_text("background-noise", timeout=6000)

    row.locator("summary").click()  # reopen — the detail must come back
    expect(row).to_contain_text("opts")
