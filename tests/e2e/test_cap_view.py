"""E2E: the global concurrency chip in a real browser, driven by a real workload.

A capped worker grinds through a backlog of short jobs, so the stream carries the
genuine `completed` events a capped queue produces. Nothing is published by hand:
the chip has to arrive through the strip's own live region, on the strip's own
cadence, and the panel has to survive the swap.
"""

import asyncio
import contextlib
import re

import pytest
from playwright.sync_api import Page, expect
from toro import Worker

from .conftest import PREFIX, QUEUE, URL, reset_queue

CAP = 2
JOBS = 60
JOB_SECONDS = 0.3  # 60 jobs, 2 at a time: about 9s of work, several strip refreshes
# \s+ where the template breaks a line: Playwright normalizes whitespace for
# string matches but not for regular expressions
FULL = re.compile(rf"at cap {CAP}/{CAP}\s+·\s+\d+ waiting")


@pytest.fixture
def capped_fleet(drive):
    """`start()` enqueues the backlog and runs a capped worker over it. Called after
    the page is open, so the chip has to ARRIVE live, not be there on first paint."""
    state: dict = {}

    async def _start():
        q = await reset_queue()

        async def work(job):
            await asyncio.sleep(JOB_SECONDS)

        for i in range(JOBS):
            await q.add("job", {"i": i})
        w = Worker(
            QUEUE,
            work,
            url=URL,
            prefix=PREFIX,
            concurrency=6,
            global_concurrency=CAP,
            stalled_interval=0,
        )
        state.update(q=q, w=w, task=asyncio.create_task(w.run()))

    async def _stop():
        if not state:
            return
        await state["w"].stop(grace_period=1)
        state["task"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state["task"]
        await state["q"].close()

    yield lambda: drive(_start())
    drive(_stop())


def test_cap_chip_goes_live(page: Page, base_url, drive, capped_fleet):
    drive(reset_queue())
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    chip = page.locator("[data-cap-state]")
    expect(chip).to_have_count(0)  # no capped worker yet: no chip

    capped_fleet()

    # no reload: it arrives with a strip refresh. The strip refreshes at most once
    # per 5s, so the timeout spans more than one window.
    full = page.locator('[data-cap-state="full"]')
    expect(full).to_have_count(1, timeout=20_000)
    expect(full).to_contain_text(FULL)
    # the failure this guards: a live region that re-roots to #queue-panel replaces
    # the whole panel, and the jobs table is gone
    expect(page.locator("#queue-panel .jobs-table")).to_be_visible()
    expect(page.locator("#metrics-live #metrics-strip")).to_have_count(1)


def test_cap_chip_is_accessible(page: Page, base_url, drive, capped_fleet):
    drive(reset_queue())
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    capped_fleet()
    chip = page.locator('[data-cap-state="full"]')
    expect(chip).to_have_count(1, timeout=20_000)

    # the state is in the visible words, not only in a color or a hover tip
    expect(chip).to_contain_text(FULL)

    # the explanation reaches a screen reader, from text that is really hidden
    tip_text = chip.get_attribute("data-tip")
    assert tip_text and "slot" in tip_text
    sr = chip.locator(".sr-only")
    expect(sr).to_have_text(tip_text)
    box = sr.bounding_box()
    assert box is not None and box["width"] <= 1 and box["height"] <= 1

    # not a tab stop: this region is swapped whole on refresh, which would drop focus
    assert chip.get_attribute("tabindex") is None
    page.locator("body").click(position={"x": 2, "y": 2})
    for _ in range(60):
        page.keyboard.press("Tab")
        assert not page.evaluate("document.activeElement?.hasAttribute('data-cap-state') ?? false")
