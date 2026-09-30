"""E2E: the live job list on queues whose names collide with the page's own paths
and markup. The session server watches one plainly named queue; these tests boot
their own server over a queue named `jobs` and one whose name carries `&`."""

from __future__ import annotations

import pytest
import uvicorn
from playwright.sync_api import Page, expect
from toro import Queue

from matador import create_app

from .conftest import URL, _free_port, _ThreadedUvicorn, wait_for_live

PREFIX = "matadore2enames"
QUEUES = ("jobs", "R&D")


async def _seed(n: int) -> None:
    for name in QUEUES:
        q = Queue(name, url=URL, prefix=PREFIX)
        keys = await q.redis.keys(q.keys.base + "*")
        if keys:
            await q.redis.delete(*keys)
        for i in range(n):  # no worker: they all stay in `wait`
            await q.add("plain", {"i": i})
        await q.close()


@pytest.fixture(scope="module")
def names_server():
    app = create_app(list(QUEUES), url=URL, prefix=PREFIX)
    config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
    server = _ThreadedUvicorn(config)
    with server.run_in_thread():
        yield f"http://127.0.0.1:{config.port}"


@pytest.fixture
def seeded_names(run_async):
    run_async(_seed(2))


def test_tabs_swap_the_list_of_a_queue_named_jobs(page: Page, names_server, seeded_names):
    page.goto(f"{names_server}/queues/jobs?state=wait")
    expect(page.locator("#jobs .jobs-table > details")).to_have_count(2)

    page.locator('a[hx-get="/queues/jobs?state=completed"]').click()

    expect(page.locator("#jobs [data-view]")).to_have_attribute("data-view", "jobs:completed")
    expect(page.locator("#jobs")).to_contain_text("No completed jobs")


def test_live_refresh_lands_on_a_queue_named_with_an_ampersand(
    page: Page, names_server, seeded_names, drive
):
    page.goto(f"{names_server}/queues/R%26D?state=wait")
    wait_for_live(page)
    rows = page.locator("#jobs .jobs-table > details")
    expect(rows).to_have_count(2)

    async def _enqueue():
        q = Queue("R&D", url=URL, prefix=PREFIX)
        await q.add("plain", {"i": 2})
        await q.close()

    drive(_enqueue())

    expect(rows).to_have_count(3)
