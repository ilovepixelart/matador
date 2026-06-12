"""E2E journeys: complete operator stories, stitched across features.

Each test walks a path a real operator walks, asserting the dashboard tells
the truth at every step - not that fragments render, but that the story holds:
incident -> triage -> fix -> green; flow enqueued -> watched live -> consumed.
"""

import asyncio

import pytest
from playwright.sync_api import Page, expect
from toro import FlowChild as c  # noqa: N813 - `c("fetch", ...)` keeps trees readable
from toro import Worker

from .conftest import PREFIX, QUEUE, URL, reset_queue, work_until


def _confirm(page: Page) -> None:
    page.locator("dialog[open] #confirm-ok").click()


# ---- journey: a bad deploy fails jobs; triage to green ---------------------------


def test_journey_incident_recovery_to_green(page: Page, base_url, drive):
    async def seed():
        q = await reset_queue()
        for i in range(3):
            await q.add("render-report", {"doc": i}, attempts=1)

        async def broken(job):
            raise RuntimeError("template not found: quarterly.tex")

        await work_until(broken, lambda q: _failed_is(q, 3))
        await q.close()

    async def _failed_is(q, n):
        return (await q.counts())["failed"] >= n

    drive(seed())

    # 1. landing with no tab in the URL puts the incident in front of you
    page.goto(f"{base_url}/queues/{QUEUE}")
    expect(page.locator("#jobs")).to_contain_text("template not found: quarterly.tex")
    # 2. the by-name table fingers the culprit
    expect(page.locator("body")).to_contain_text("render-report")
    # 3. open a row: the reason and the stack trace are right there
    row = page.locator("#jobs details").first
    row.locator("summary").click()
    expect(row).to_contain_text("RuntimeError")
    # 4. the fix is deployed; retry everything
    row.locator("summary").click()  # close the row so the live table isn't paused
    page.wait_for_timeout(2000)  # SSE connect (no replay if we miss it)
    page.locator('button:has-text("retry all")').click()
    _confirm(page)
    expect(page.locator("#tabcount-failed")).to_have_text("0")  # retry emptied failed
    # the swap replaced the live-refresh element; it re-wires its sse:changed
    # listener on init - give it a beat before background events start firing
    page.wait_for_timeout(1500)

    async def fixed():
        async def healthy(job):
            return {"rendered": job.data["doc"]}

        await work_until(healthy, lambda q: _completed_is(q, 3))

    async def _completed_is(q, n):
        return (await q.counts())["completed"] >= n

    drive(fixed())
    # 5. green: the badge fills in live as the retried jobs complete
    expect(page.locator("#tabcount-completed")).to_have_text("3", timeout=8000)
    expect(page.locator("#tabcount-failed")).to_have_text("0")


# ---- journey: a flow's whole life, watched live ----------------------------------


def test_journey_flow_lifecycle_to_results(page: Page, base_url, drive):
    ids = {}

    async def seed():
        q = await reset_queue()
        parent = await q.add_flow(
            "invoice-batch", {"month": "june"}, children=[c("collect", {"i": i}) for i in range(3)]
        )
        ids["parent"] = parent.id
        await q.close()

    drive(seed())

    # 1. the flow parks in the flows tab, honest about zero progress
    page.goto(f"{base_url}/queues/{QUEUE}?state=waiting-children")
    row = page.locator("#jobs details").first
    expect(row).to_contain_text("invoice-batch")
    row.locator("summary").click()
    expect(row).to_contain_text("0/3 children done")
    page.wait_for_timeout(2000)  # SSE connect (no replay if we miss it)
    row.locator("summary").click()  # close - live refresh resumes

    # 2. workers chew through it; the flows tab empties on its own
    async def run():
        async def proc(job):
            if job.name == "collect":
                return 100 + job.data["i"]
            return sorted((await job.children_results()).values())

        async def done(q):
            j = await q.get_job(ids["parent"])
            return j is not None and j.state == "completed"

        await work_until(proc, done)

    drive(run())
    expect(page.locator("#jobs details")).to_have_count(0, timeout=8000)

    # 3. the parent lands in completed with the aggregated result
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{ids['parent']}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("3/3 children done")
    expect(panel).to_contain_text("children results")
    expect(panel).to_contain_text("102")  # the aggregation consumed every child


# ---- journey: a flow fails, gets fixed, and recovers end to end ------------------


def test_journey_flow_failure_recovery_arc(page: Page, base_url, drive):
    behaviors = {"upload_ok": False}
    ids = {}

    async def seed():
        q = await reset_queue()
        parent = await q.add_flow("deploy-site", {}, children=[c("build", {}), c("upload", {})])
        ids["parent"] = parent.id

        async def proc(job):
            if job.name == "upload" and not behaviors["upload_ok"]:
                raise RuntimeError("S3 returned 503")
            return "ok"

        async def parent_failed(q):
            j = await q.get_job(ids["parent"])
            return j is not None and j.state == "failed"

        await work_until(proc, parent_failed)
        await q.close()

    drive(seed())

    # 1. the failed tab shows the parent naming the guilty child, plus the child
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    expect(page.locator("#jobs")).to_contain_text("S3 returned 503")
    expect(page.locator("#jobs")).to_contain_text("deploy-site")
    # 2. the parent's tree pins the failure to `upload` while `build` is green
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{ids['parent']}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text("1/2 children done")
    expect(panel).to_contain_text("1 failed")
    # 3. S3 recovered; retry-all re-arms the flow instead of running it broken
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    page.locator('button:has-text("retry all")').click()
    _confirm(page)
    expect(page.locator("#tabcount-waiting-children")).to_have_text("1", timeout=5000)

    async def recover():
        behaviors["upload_ok"] = True

        async def proc(job):
            if job.name == "deploy-site":
                return await job.children_results()
            return "ok"

        async def done(q):
            j = await q.get_job(ids["parent"])
            return j is not None and j.state == "completed"

        await work_until(proc, done)

    drive(recover())
    # 4. the whole flow completed; nothing left failed
    expect(page.locator("#tabcount-failed")).to_have_text("0", timeout=8000)
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{ids['parent']}")
    expect(page.locator("#queue-panel")).to_contain_text("2/2 children done")


# ---- journey: an overdue delayed job needs to run NOW ----------------------------


def test_journey_promote_an_overdue_delayed_job(page: Page, base_url, drive):
    async def seed():
        q = await reset_queue()
        await q.add("warm-cache", {"region": "eu"}, delay=600_000)
        await q.close()

    drive(seed())

    page.goto(f"{base_url}/queues/{QUEUE}?state=delayed")
    row = page.locator("#jobs details").first
    expect(row).to_contain_text("warm-cache")
    # the operator can't wait 10 minutes: promote it
    page.get_by_role("button", name="Promote, run now").click()
    expect(page.locator("#tabcount-wait")).to_have_text("1")
    expect(page.locator("#tabcount-delayed")).to_have_text("0")
    page.wait_for_timeout(1500)  # SSE connect before the background worker fires

    async def drain():
        async def proc(job):
            return "warmed"

        async def done(q):
            return (await q.counts())["completed"] >= 1

        await work_until(proc, done)

    drive(drain())
    expect(page.locator("#tabcount-completed")).to_have_text("1", timeout=8000)


# ---- journey: support ticket says "what happened to order 4711?" -----------------


def test_journey_find_one_job_and_read_its_logs(page: Page, base_url, drive):
    async def seed():
        q = await reset_queue()
        await q.add("charge-card", {"order": "order-4711", "amount": 49})
        for i in range(4):
            await q.add("charge-card", {"order": f"order-{i}", "amount": 10})

        async def proc(job):
            await job.log(f"charged ${job.data['amount']} for {job.data['order']}")
            return {"charged": job.data["order"]}

        async def done(q):
            return (await q.counts())["completed"] >= 5

        await work_until(proc, done)
        await q.close()

    drive(seed())

    page.goto(f"{base_url}/queues/{QUEUE}?state=completed")
    expect(page.locator("#jobs details")).to_have_count(5)
    # narrow five charges to the one the ticket names
    page.fill('input[name="query"]', "4711")
    expect(page.locator("#jobs details")).to_have_count(1, timeout=4000)
    row = page.locator("#jobs details").first
    row.locator("summary").click()
    expect(row).to_contain_text("charged $49 for order-4711")  # the audit trail
    expect(row).to_contain_text("order-4711")


# ---- journey: history housekeeping ------------------------------------------------


def test_journey_prune_completed_history(page: Page, base_url, drive):
    async def seed():
        q = await reset_queue()
        for i in range(5):
            await q.add("rollup", {"i": i})

        async def proc(job):
            return job.data["i"]

        async def done(q):
            return (await q.counts())["completed"] >= 5

        await work_until(proc, done)
        await q.close()

    drive(seed())

    page.goto(f"{base_url}/queues/{QUEUE}?state=completed")
    rows = page.locator("#jobs details")
    expect(rows).to_have_count(5)
    # pick two specific rows; the bulk bar appears with the count
    page.locator(".jcheck").nth(0).check()
    page.locator(".jcheck").nth(1).check()
    expect(page.locator("#bulk-count")).to_have_text("2")
    page.locator("#bulk-delete").click()
    _confirm(page)
    expect(rows).to_have_count(3)
    # then sweep the rest
    page.locator('button:has-text("clean completed")').click()
    _confirm(page)
    expect(rows).to_have_count(0)
    expect(page.locator("#jobs")).to_contain_text("No completed jobs")


# ---- journey: watching the worker fleet -------------------------------------------

_FLEET: dict = {}


@pytest.fixture
def fleet(run_async):
    """Start a real worker the test can stop mid-journey; always cleaned up."""

    async def start():
        await reset_queue()
        worker = Worker(
            QUEUE, lambda j: None, url=URL, prefix=PREFIX, concurrency=2, stalled_interval=0
        )
        _FLEET["task"] = asyncio.create_task(worker.run())
        await asyncio.sleep(0.2)  # first heartbeat registers the presence record
        return worker

    worker = run_async(start())
    yield worker
    if not _FLEET["task"].done():  # the test usually stopped it; belt and braces

        async def cleanup():
            await worker.stop(grace_period=0)
            _FLEET["task"].cancel()

        run_async(cleanup())


def test_journey_worker_appears_then_departs_gracefully(page: Page, base_url, fleet, drive):
    # 1. the fleet page shows the live worker and its slots
    page.goto(f"{base_url}/workers")
    body = page.locator("body")
    expect(body).to_contain_text(QUEUE)
    expect(body).to_contain_text("×2")  # concurrency
    # 2. it shuts down gracefully...
    drive(fleet.stop(grace_period=0))
    # 3. ...and moves to the departure log instead of silently vanishing
    page.goto(f"{base_url}/workers")
    expect(body).to_contain_text("Recently stopped")
    expect(body).to_contain_text("stopped")
