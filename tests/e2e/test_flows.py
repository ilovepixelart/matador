"""E2E: flows in a real browser - the flows tab, the tree in the detail, honest
progress, parent/child navigation, flow-aware retry/remove/clean, live unpark.
"""

import pytest
from playwright.sync_api import Page, expect
from toro import FlowChild as c  # noqa: N813 - `c("fetch", ...)` keeps trees readable
from toro import Queue

from .conftest import PREFIX, QUEUE, URL, reset_queue, work_until


async def _seed_flows() -> dict:
    """Two flows: A in flight (1/2 done, one child delayed so it stays pending),
    B failed (transcode raises under the fail_parent default, thumbnail completes).
    """
    q = await reset_queue()

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

    async def settled(q):
        b = await q.get_job(flow_b.id)
        return (await q.counts())["completed"] >= 2 and b is not None and b.state == "failed"

    await work_until(proc, settled)

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
    expect(dialog).to_contain_text("whole subtree")  # honest destructive copy
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
    page.wait_for_timeout(2000)  # SSE connect (no replay if we miss it)

    async def finish_the_flow():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.promote_job(flows["a_children"][1])  # the delayed shard runs now
        await q.close()

        async def proc(job):
            return {"ok": job.name}

        async def done(q):
            j = await q.get_job(flows["parent_a"])
            return j is not None and j.state == "completed"

        await work_until(proc, done)

    drive(finish_the_flow())
    # the SSE-driven refresh empties the flows tab without any user action
    expect(page.locator("#jobs details")).to_have_count(0, timeout=8000)


def test_retry_flow_button_recovers_the_flow(page: Page, base_url, flows, drive):
    # flows fixture: publish-video failed (transcode raised). Open the failed
    # parent's detail and use the one-click "retry flow".
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_b']}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("1 failed")
    page.get_by_role("button", name="retry flow").click()
    page.locator("dialog[open] #confirm-ok").click()
    # the flow re-parks: the parent leaves failed for waiting-children
    expect(page.locator("#tabcount-waiting-children")).to_have_text("2", timeout=5000)

    # let a worker drive the recovered flow to completion (transcode succeeds now)
    async def finish():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)

        async def proc(job):
            return {"ok": job.name} if job.name != "publish-video" else "done"

        async def done(qq):
            j = await qq.get_job(flows["parent_b"])
            return j is not None and j.state == "completed"

        await work_until(proc, done)
        await q.close()

    drive(finish())
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_b']}")
    expect(page.locator("#queue-panel")).to_contain_text("2/2 children done")


def test_flow_tree_updates_live_while_in_flight(page: Page, base_url, flows, drive):
    # parent_a is parked at 1/2 (one shard done, one delayed-pending)
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_a']}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("1/2 children done")
    page.wait_for_timeout(2000)  # SSE connect (no replay if we miss it)

    async def finish():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.promote_job(flows["a_children"][1])  # the delayed shard runs now
        await q.close()

        async def proc(job):
            return {"ok": job.name}

        async def done(qq):
            j = await qq.get_job(flows["parent_a"])
            return j is not None and j.state == "completed"

        await work_until(proc, done)

    drive(finish())
    # the OPEN detail re-rendered itself - no reopen, no manual refresh
    expect(panel).to_contain_text("2/2 children done", timeout=8000)


def test_live_update_touches_only_the_flow_section_not_the_chrome(
    page: Page, base_url, flows, drive
):
    # Regression for the bug you hit ("the top got eaten"): an earlier live render
    # re-fetched the WHOLE detail and morphed a parent, collapsing the page header
    # and the data/opts wells - yet it still showed "2/2", so the in-flight test
    # above stayed green through it. Guard the chrome explicitly: after the morph the
    # title, the parent's own data well, and a single #flow-section must all survive.
    parent = flows["parent_a"]
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{parent}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("1/2 children done")
    expect(page.locator("h1")).to_contain_text("nightly-report")  # the title (the "top")
    expect(panel).to_contain_text("period")  # the parent's data well, sibling of the flow
    expect(page.locator("#flow-section")).to_have_count(1)
    page.wait_for_timeout(2000)  # SSE connect (no replay if we miss it)

    async def finish():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.promote_job(flows["a_children"][1])  # the delayed shard runs now
        await q.close()

        async def proc(job):
            return {"ok": job.name}

        async def done(qq):
            j = await qq.get_job(parent)
            return j is not None and j.state == "completed"

        await work_until(proc, done)

    drive(finish())
    expect(panel).to_contain_text("2/2 children done", timeout=8000)  # the flow updated
    # ...and the morph touched ONLY the flow body - everything else is still here, once
    expect(page.locator("h1")).to_contain_text("nightly-report")
    expect(panel).to_contain_text("period")
    expect(page.locator("#flow-section")).to_have_count(1)


def test_flows_tab_row_shows_progress(page: Page, base_url, flows):
    # flows fixture: nightly-report is parked at 1/2 (one shard done, one pending)
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    row = page.locator("#jobs details", has_text="nightly-report")
    expect(row).to_contain_text("1/2")  # fan-in progress on the row, no need to open it


def test_failed_flow_updates_live_during_per_node_retry(page: Page, base_url, flows, drive):
    # the bug you hit: per-node retry on a FAILED flow didn't push the child's
    # state flip - you had to reload. With flow_live, the failed flow's detail
    # stays live while the retried child runs.
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_b']}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("1/2 children done")  # transcode failed, thumbnail done
    page.wait_for_timeout(2000)  # body SSE connect

    # per-node retry the failed child (the only retry-node button in the tree)
    page.locator('button[hx-post*="retry-node"]').first.click()
    # the swapped-in detail is now live (the child is in `wait`); let its refresher
    # wire its SSE listener before any worker event fires (no replay if missed)
    page.wait_for_timeout(2000)

    async def finish():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)

        async def proc(job):
            return {"ok": job.name}

        async def done(qq):
            return (await qq.flow_progress([flows["parent_b"]]))[flows["parent_b"]] == (2, 0)

        await work_until(proc, done)
        await q.close()

    drive(finish())
    # no reload: the open detail re-rendered itself to 2/2 as the retry completed
    expect(panel).to_contain_text("2/2 children done", timeout=8000)


def test_flow_action_buttons_appear_only_where_they_apply(page: Page, base_url, flows):
    # retry-flow recovers a FAILED parent; per-node retry recovers a FAILED child.
    # An in-flight flow offers neither. A failed flow offers retry-flow plus exactly
    # one per-node retry: on the failed child, not the completed one and not the root
    # (the root carries retry-flow instead). The single count on parent_b's
    # failed+completed+root tree is what encodes "only the failed child". The same
    # selector finds and clicks the button in the live test, so the 0-counts here
    # are a real absence, not a typo'd locator.
    panel = page.locator("#queue-panel")

    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_a']}")  # in flight, 1/2
    expect(panel).to_contain_text("1/2 children done")
    expect(panel.get_by_role("button", name="retry flow")).to_have_count(0)
    expect(panel.locator('button[hx-post*="retry-node"]')).to_have_count(0)

    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{flows['parent_b']}")  # failed
    expect(panel).to_contain_text("1 failed")
    expect(panel.get_by_role("button", name="retry flow")).to_have_count(1)
    expect(panel.locator('button[hx-post*="retry-node"]')).to_have_count(1)


def test_deep_nested_tree_renders_every_level(page: Page, base_url, run_async):
    # flow_node recurses; the shared seed is only 2 levels deep, so render a 4-deep
    # chain and confirm every level shows up, properly nested (one <ul> per non-leaf).
    async def seed():
        q = await reset_queue()
        parent = await q.add_flow(
            "deploy",
            {},
            children=[c("build", {}, children=[c("compile", {}, children=[c("lint", {})])])],
        )
        await q.close()
        return parent.id

    pid = run_async(seed())
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{pid}")
    tree = page.locator("ul.ftree")
    for name in ("deploy", "build", "compile", "lint"):
        expect(tree).to_contain_text(name)
    expect(tree.locator("ul")).to_have_count(3)  # build's, compile's, lint's containers


def test_flows_tab_shows_the_flow_throughput_strip(page: Page, base_url, flows):
    # the seed leaves publish-video FAILED - one WHOLE flow failed (recorded at the
    # root, not per child). The flows-tab throughput strip reports it, separate from
    # the per-job health strip. Nothing completed yet, so "0 flows done".
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    strip = page.locator("#flow-metrics")
    expect(strip).to_be_visible()
    expect(strip).to_contain_text("0 flows done")
    expect(strip).to_contain_text("1 failed")  # the eagerly-failed flow, counted once


def test_flow_throughput_strip_is_flows_tab_only(page: Page, base_url, flows):
    # the strip is scoped to the flows tab; other tabs keep only the job health strip
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    expect(page.locator("#metrics-strip")).to_be_visible()  # job health strip
    expect(page.locator("#flow-metrics")).to_have_count(0)  # no flow strip here


def test_flow_throughput_climbs_live_when_a_flow_completes(page: Page, base_url, flows, drive):
    # parent_a is parked at 1/2; drive it to completion and the throughput strip
    # should tick 0 -> 1 flows done on its own (self-refresh, no reload).
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    strip = page.locator("#flow-metrics")
    expect(strip).to_contain_text("0 flows done")
    page.wait_for_timeout(2000)  # SSE connect (no replay if we miss it)

    async def finish():
        q = Queue(QUEUE, url=URL, prefix=PREFIX)
        await q.promote_job(flows["a_children"][1])  # the delayed shard runs now
        await q.close()

        async def proc(job):
            return {"ok": job.name}

        async def done(qq):
            j = await qq.get_job(flows["parent_a"])
            return j is not None and j.state == "completed"

        await work_until(proc, done)

    drive(finish())
    expect(strip).to_contain_text("1 flows done", timeout=8000)
