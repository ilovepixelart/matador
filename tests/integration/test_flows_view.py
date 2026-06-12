"""Integration: the flows tab and flow rendering - parked parents are visible,
the job detail shows the tree/progress/results, children link to their parent.
"""

import asyncio

from toro import FlowChild as c  # noqa: N813 - `c("fetch", ...)` keeps trees readable
from toro import Worker

from .conftest import PREFIX, QUEUE, hx


async def _flow(q, *, settle: int = 0):
    """Seed one flow (2 children); optionally let `settle` children complete."""
    parent = await q.add_flow(
        "report", {"id": 1}, children=[c("fetch", {"part": 1}), c("fetch", {"part": 2})]
    )
    if settle:
        done = 0

        async def proc(job):
            nonlocal done
            done += 1
            if done > settle:
                await asyncio.sleep(30)  # park the slot; stop() cancels us
            return job.data["part"]

        worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
        task = asyncio.create_task(worker.run())
        for _ in range(200):
            if (await q.counts())["completed"] >= settle:
                break
            await asyncio.sleep(0.02)
        await worker.stop(grace_period=0)
        task.cancel()
    return parent


async def test_flows_tab_lists_parked_parents(client, q):
    parent = await _flow(q)
    r = await client.get(f"/queues/{QUEUE}?state=waiting-children", headers=hx())
    assert r.status_code == 200
    assert "flows" in r.text  # the tab is labeled "flows" ...
    assert "state=waiting-children" in r.text  # ... over the raw state in the URL
    assert f"#{parent.id}" in r.text  # the parked parent is listed
    assert "report" in r.text


async def test_waiting_children_not_coerced_away(client, q):
    await _flow(q)
    # the state survives the query-string clamp (it used to coerce to "active")
    r = await client.get(f"/queues/{QUEUE}/jobs?state=waiting-children", headers=hx())
    assert r.status_code == 200
    assert "report" in r.text


async def test_parent_detail_shows_tree_progress_and_results(client, q):
    parent = await _flow(q, settle=1)
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert r.status_code == 200
    assert "1/2 children done" in r.text  # fan-in progress, completions only
    assert "children results" in r.text  # the settled child's value arrived
    assert r.text.count("fetch") >= 2  # both children render in the tree


async def test_failed_flow_progress_never_reads_as_success(client, q):
    parent = await q.add_flow("publish", {}, children=[c("ok", {}), c("bad", {})])

    async def proc(job):
        if job.name == "bad":
            raise RuntimeError("boom")
        return 1

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        j = await q.get_job(parent.id)
        if j and j.state == "failed" and (await q.counts())["completed"] >= 1:
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert "1/2 children done" in r.text  # the completed child
    assert "1 failed" in r.text  # the failure is named, in danger ink
    assert "100%" not in r.text  # a failed flow must never read as complete


async def test_child_detail_links_to_parent(client, q):
    parent = await _flow(q)
    child_id = (await q.get_flow(parent.id))["children"][0]["job"].id
    r = await client.get(f"/queues/{QUEUE}/jobs/{child_id}/detail")
    assert r.status_code == 200
    assert "part of" in r.text
    assert f"#{parent.id}" in r.text  # the chip points at the parent


async def test_flow_parent_row_warns_remove_takes_subtree(client, q):
    await _flow(q)
    r = await client.get(f"/queues/{QUEUE}/jobs?state=waiting-children", headers=hx())
    assert "whole subtree (2 direct children" in r.text  # the destructive confirm is honest


async def test_search_scopes_to_the_flows_tab(client, q):
    parent = await _flow(q)
    r = await client.get(f"/queues/{QUEUE}/jobs?state=waiting-children&query=report", headers=hx())
    assert f"#{parent.id}" in r.text  # found by name within the parked parents
    r = await client.get(f"/queues/{QUEUE}/jobs?state=waiting-children&query=nosuch", headers=hx())
    assert f"#{parent.id}" not in r.text


async def test_tab_counts_oob_includes_the_flows_tab(client, q):
    await _flow(q)
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())
    assert 'id="tabcount-waiting-children"' in r.text  # the badge refreshes too


async def test_service_flow_detail_counts_by_child_state(q):
    """The service's fan-in numbers: done counts completions only, failed counts
    failures, pending children count toward neither."""
    from matador.service import Service

    parent = await q.add_flow(
        "report",
        {},
        children=[
            c("done", {}),
            c("bad", {}, on_fail="continue"),
            c("pending", {}, delay=600_000),  # parked in delayed: settles nothing
        ],
    )

    async def proc(job):
        if job.name == "bad":
            raise RuntimeError("boom")
        return 1

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        cts = await q.counts()
        if cts["completed"] >= 1 and cts["failed"] >= 1:
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    try:
        detail = await svc.job(QUEUE, parent.id)
    finally:
        await svc.close()

    assert detail["children_total"] == 3
    assert detail["children_done"] == 1
    assert detail["children_failed"] == 1  # the tolerated failure
    assert detail["children_results"] and detail["children_failures"]
    assert len(detail["flow"]["children"]) == 3  # the tree carries all three


async def test_retry_flow_route_redrives_a_failed_flow(client, q):
    fail = {"on": True}
    parent = await q.add_flow("publish", {}, children=[c("a", {}), c("bad", {})])

    async def proc(job):
        if job.name == "bad" and fail["on"]:
            raise RuntimeError("boom")
        return "ok"

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        j = await q.get_job(parent.id)
        if j and j.state == "failed":
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    # the button is offered on a failed flow's detail
    detail = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert "retry flow" in detail.text

    fail["on"] = False
    r = await client.post(f"/queues/{QUEUE}/jobs/{parent.id}/retry-flow?state=failed", headers=hx())
    assert r.status_code == 200

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        j = await q.get_job(parent.id)
        if j and j.state == "completed":
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    assert (await q.get_job(parent.id)).state == "completed"  # the whole flow recovered


async def test_retry_node_route_retries_one_child_in_place(client, q):
    parent = await q.add_flow(
        "report", {}, children=[c("ok", {}), c("bad", {}, on_fail="continue")]
    )

    async def proc(job):
        if job.name == "bad":
            raise RuntimeError("boom")
        if job.name == "ok":
            return 1
        return await job.children_results()

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        j = await q.get_job(parent.id)
        if j and j.state == "completed":  # tolerated failure, parent still ran
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    bad_id = (await q.get_flow(parent.id))["children"][1]["job"].id
    assert (await q.get_job(bad_id)).state == "failed"

    # retry just that node; the response re-renders the parent's flow page
    r = await client.post(
        f"/queues/{QUEUE}/jobs/{bad_id}/retry-node?parent={parent.id}", headers=hx()
    )
    assert r.status_code == 200
    assert "report" in r.text  # stayed on the parent's flow, not the list
    assert "Retried job" in r.text  # the announcement
    assert (await q.get_job(bad_id)).state != "failed"  # the node left failed
