"""E2E: flows in a real browser - the flows tab, the tree in the detail, honest
progress, parent/child navigation, flow-aware retry/remove/clean, live unpark.
"""

import asyncio

import pytest
from playwright.sync_api import Page, expect
from toro import FlowChild as c  # noqa: N813 - `c("fetch", ...)` keeps trees readable
from toro import Queue, Worker

from .conftest import PREFIX, QUEUE, URL


async def _seed_flows() -> dict:
    """Two flows: A in flight (1/2 done, one child delayed so it stays pending),
    B failed (transcode raises under the fail_parent default, thumbnail completes).
    """
    q = Queue(QUEUE, url=URL, prefix=PREFIX)
    keys = await q.redis.keys(q.keys.base + "*")
    if keys:
        await q.redis.delete(*keys)

    flow_a = await q.add_flow(
        "nightly-report",
        {"period": "today"},
        children=[c("done-shard", {"i": 1}), c("pending-shard", {"i": 2}, delay=600_000)],
    )
    flow_b = await q.add_flow(
        "publish-video",
        {"video": "launch.mp4"},
        children=[c("transcode", {}), c("thumbnail", {})],
    )

    async def proc(job):
        if job.name == "transcode":
            raise RuntimeError("ffmpeg exited 137")
        return {"ok": job.name}

    worker = Worker(QUEUE, proc, url=URL, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        b = await q.get_job(flow_b.id)
        if (await q.counts())["completed"] >= 2 and b and b.state == "failed":
            break
        await asyncio.sleep(0.02)
    await worker.stop()
    task.cancel()

    a_kids = [n["job"].id for n in (await q.get_flow(flow_a.id))["children"]]
    await q.close()
    return {"parent_a": flow_a.id, "a_children": a_kids, "parent_b": flow_b.id}


@pytest.fixture
def flows(run_async):
    """Reset + seed the two flows; returns the ids that matter."""
    return run_async(_seed_flows())


def test_flows_tab_lists_parents_with_child_counts(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    expect(page.locator("a", has_text="flows").first).to_be_visible()  # the tab label
    row = page.locator("#jobs details").first
    expect(row).to_contain_text("nightly-report")
    expect(row).to_contain_text("2")  # the branch badge: 2 children
    expect(page.locator("#jobs details")).to_have_count(1)  # B failed - not parked


def test_parent_detail_renders_tree_and_honest_progress(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    row = page.locator("#jobs details").first
    row.locator("summary").click()
    expect(row).to_contain_text("1/2 children done")
    expect(row).to_contain_text("50%")  # the pending child is not progress
    expect(row).to_contain_text("done-shard")
    expect(row).to_contain_text("pending-shard")
    expect(row).to_contain_text("children results")  # the done shard's value arrived


def test_tree_chip_navigates_to_the_child_job_page(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    row = page.locator("#jobs details").first
    row.locator("summary").click()
    cid = flows["a_children"][0]
    row.locator(f'a:has-text("#{cid}")').first.click()
    expect(page).to_have_url(f"{base_url}/queues/{QUEUE}/jobs/{cid}")
    expect(page.locator("#queue-panel")).to_contain_text("part of")  # the child links up


def test_child_page_links_back_to_its_parent(page: Page, base_url, flows):
    cid = flows["a_children"][0]
    parent = flows["parent_a"]
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{cid}")
    page.locator(f'a:has-text("#{parent}")').first.click()
    expect(page).to_have_url(f"{base_url}/queues/{QUEUE}/jobs/{parent}")
    expect(page.locator("#queue-panel")).to_contain_text("children done")  # parent view


def test_failed_flow_never_reads_as_complete(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_b']}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("1/2 children done")
    expect(panel).to_contain_text("1 failed")
    expect(panel).to_contain_text("ffmpeg exited 137")  # the reason, in the tree
    expect(panel).not_to_contain_text("100%")


def test_retry_all_reparks_the_failed_parent_into_flows(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    page.locator('button:has-text("retry all")').click()
    page.locator("dialog[open] #confirm-ok").click()
    # the parent re-arms its barrier instead of running with partial results
    expect(page.locator("#tabcount-waiting-children")).to_have_text("2", timeout=5000)
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    expect(page.locator("#jobs")).to_contain_text("publish-video")


def test_remove_parent_confirm_names_the_subtree(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    page.get_by_role("button", name="Remove this job").first.click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_contain_text("AND its 2 child jobs")  # honest destructive copy
    dialog.locator("#confirm-ok").click()
    expect(page.locator("#jobs details")).to_have_count(0)
    expect(page.locator("#jobs")).to_contain_text("No flows in flight")  # empty state


def test_clean_flows_cancels_every_parked_flow(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    page.locator('button:has-text("clean flows")').click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_contain_text("whole subtree")  # warns about the cascade
    dialog.locator("#confirm-ok").click()
    expect(page.locator("#jobs details")).to_have_count(0)
    # the cascade took the pending child with it - nothing left anywhere
    expect(page.locator("#tabcount-delayed")).to_have_text("0")


def test_keyboard_cursor_works_on_the_flows_tab(page: Page, base_url, flows):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    page.keyboard.press("j")  # enter the row cursor at the top
    expect(page.locator("#jobs details > summary").first).to_be_focused()
    page.keyboard.press("o")  # open the focused row
    expect(page.locator("#jobs details").first).to_contain_text("children done")


def test_live_refresh_unparks_a_completed_flow(page: Page, base_url, flows, drive):
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    expect(page.locator("#jobs details")).to_have_count(1)
    page.wait_for_timeout(1500)  # SSE connect

    async def finish_the_flow():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.promote_job(flows["a_children"][1])  # the delayed shard runs now

        async def proc(job):
            return {"ok": job.name}

        worker = Worker(QUEUE, proc, url=URL, prefix=PREFIX, stalled_interval=0)
        task = asyncio.create_task(worker.run())
        for _ in range(200):
            j = await q.get_job(flows["parent_a"])
            if j and j.state == "completed":
                break
            await asyncio.sleep(0.02)
        await worker.stop()
        task.cancel()
        await q.close()

    drive(finish_the_flow())
    # the SSE-driven refresh empties the flows tab without any user action
    expect(page.locator("#jobs details")).to_have_count(0, timeout=8000)
