"""E2E: the <details> accordion lazy-loads job detail over htmx on first open."""

import re

from playwright.sync_api import Page, Route, expect
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
    colons (`repeat:<sid>:<when>`). Its row must still lazy-load detail on expand -
    colons in the id must not break the htmx target (they're a CSS pseudo-class)."""

    async def _add_scheduler():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.add_scheduler("nightly", every=3_600_000, name="rollup", data={"k": "v"})
        await q.close()

    drive(_add_scheduler())
    page.goto(f"{base_url}/queues/{QUEUE}?state=delayed")
    row = page.locator('#jobs details[id*="repeat:"]')  # the colon-id occurrence
    expect(row).to_contain_text("rollup")  # it's listed in the jobs table
    # badged as a scheduler occurrence (icon-only glyph; the words live in the tip)
    expect(row.locator('[data-tip^="Scheduled occurrence"]')).to_be_visible()
    expect(row).not_to_contain_text("opts")  # detail not loaded yet
    row.locator("summary").click()
    expect(row).to_contain_text("opts")  # detail lazy-loaded on expand (was broken)


def test_reopen_after_live_refresh_still_loads_the_detail(page: Page, base_url, seeded, drive):
    # `toggle once` + a live morph lost the reference: the morph emptied the
    # closed row's body but the preserved node remembered it already fetched -
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

    row.locator("summary").click()  # reopen - the detail must come back
    expect(row).to_contain_text("opts")


def test_paused_banner_shows_while_a_row_is_open(page: Page, base_url, seeded):
    # Regression: Tailwind 4 layers utilities after components, so a `hidden`
    # utility on the banner silently beat the :has() reveal rule - the banner
    # never showed again and no test noticed.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    banner = page.locator(".jobs-paused")
    expect(banner).to_be_hidden()
    page.locator("#jobs details.jobs-row summary").first.click()
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("paused")
    page.keyboard.press("Escape")  # close the row
    expect(banner).to_be_hidden()


def test_a_refresh_in_flight_does_not_close_a_row_just_opened(page: Page, base_url, seeded):
    """The live refresh is skipped while a row is open. One that was ALREADY in flight
    when the row opened must not land either: the server's HTML has every row closed,
    so the swap would shut the row under the reader. Held here so the order is exact:
    request out, row opened, response in."""
    held: list[Route] = []

    def hold_the_live_refresh(route: Route) -> None:
        if route.request.headers.get("hx-trigger", "").startswith("jl-"):
            held.append(route)
        else:
            route.continue_()

    page.route(re.compile(r"/jobs\?"), hold_the_live_refresh)
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    for _ in range(50):  # the stream beats as soon as it is subscribed
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the page never issued its live refresh"

    page.locator("#jobs details.jobs-row summary").first.click()
    expect(page.locator("#jobs details[open]")).to_have_count(1)

    with page.expect_response(re.compile(r"/jobs\?")) as landed:
        held[0].continue_()
    landed.value.finished()
    page.wait_for_timeout(300)  # a swap, had there been one, has settled by now
    assert page.locator("#jobs details[open]").count() == 1
