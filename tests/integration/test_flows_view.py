"""Integration: flow rendering in the root-first model - a flow root shows in its
state tab (parked roots under active), children are hidden from the lists, and the
job detail shows the tree/progress/results with children linking to their parent.
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


async def test_parked_flow_root_shows_under_active(client, q):
    parent = await _flow(q)
    # no flows tab: a parked flow parent (waiting-children) folds into `active`
    r = await client.get(f"/queues/{QUEUE}?state=active", headers=hx())
    assert r.status_code == 200
    assert f"#{parent.id}" in r.text  # the parked parent is listed under active
    assert "report" in r.text
    assert "tabcount-waiting-children" not in r.text  # the flows tab is gone


async def test_old_flows_url_coerces_to_active(client, q):
    await _flow(q)
    # the retired ?state=waiting-children URL clamps to active, where parked flows live
    r = await client.get(f"/queues/{QUEUE}/jobs?state=waiting-children", headers=hx())
    assert r.status_code == 200
    assert "report" in r.text  # the parent still shows (via the active union)


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
    r = await client.get(f"/queues/{QUEUE}/jobs?state=active", headers=hx())
    assert "whole subtree (2 direct children" in r.text  # the destructive confirm is honest


async def test_search_active_finds_parked_flow(client, q):
    parent = await _flow(q)
    # the active tab searches active + parked roots, matching its listing
    r = await client.get(f"/queues/{QUEUE}/jobs?state=active&query=report", headers=hx())
    assert f"#{parent.id}" in r.text  # found by name among active + parked roots
    assert "0/2" in r.text  # search rows carry fan-in progress too (not just the listing)
    r = await client.get(f"/queues/{QUEUE}/jobs?state=active&query=nosuch", headers=hx())
    assert f"#{parent.id}" not in r.text


async def test_tab_counts_oob_has_no_flows_tab(client, q):
    await _flow(q)
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())
    assert 'id="tabcount-waiting-children"' not in r.text  # the flows tab is retired
    assert 'id="tabcount-active"' in r.text  # parked flows fold into the active badge


async def test_clean_flows_cancels_parked_roots_and_subtrees(client, q):
    await _flow(q)  # one parked flow, two children
    # the active tab offers the bulk cancel while parked flows exist
    r = await client.get(f"/queues/{QUEUE}/jobs?state=active", headers=hx())
    assert "cancel parked flows" in r.text
    # the action cancels the parked root AND its subtree (children included)
    r = await client.post(f"/queues/{QUEUE}/flows/clean", headers=hx())
    assert r.status_code == 200
    cts = await q.counts()
    assert cts["waiting-children"] == 0  # the parked root is gone
    assert cts["wait"] == 0  # its children went with it
    # with nothing parked, the button is no longer offered
    r = await client.get(f"/queues/{QUEUE}/jobs?state=active", headers=hx())
    assert "cancel parked flows" not in r.text


async def test_flow_fragment_survives_a_vanished_parent(client, q):
    # The live #flow-section polls .../flow on each event. If the parent is removed
    # (or cancelled) between events, svc.job -> None and the body must render empty,
    # not 500 on `children_done * 100 // 0`.
    parent = await _flow(q)
    assert await q.remove_job(parent.id)  # parent + subtree gone
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/flow?title=1")
    assert r.status_code == 200  # no ZeroDivisionError / UndefinedError


async def test_flow_fragment_carries_title_oob_only_when_asked(client, q):
    parent = await _flow(q)
    # the standalone page asks ?title=1, so the live fragment also OOB-updates the
    # page title pill; the accordion (no ?title) gets just the body
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/flow?title=1")
    assert 'id="job-state-pill"' in r.text and "hx-swap-oob" in r.text
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/flow")
    assert 'id="job-state-pill"' not in r.text


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


async def test_parked_flow_row_shows_fanin_progress(client, q):
    parent = await q.add_flow(
        "report",
        {},
        children=[c("good", {}), c("bad", {}, on_fail="continue"), c("later", {}, delay=600_000)],
    )

    async def proc(job):
        if job.name == "bad":
            raise RuntimeError("boom")
        return 1

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        if (await q.flow_progress([parent.id]))[parent.id] == (1, 1):
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    r = await client.get(f"/queues/{QUEUE}/jobs?state=active", headers=hx())
    assert r.status_code == 200
    assert "1/3" in r.text  # 1 of 3 children done, on the parked row under active


async def test_live_refresher_self_stops_when_flow_is_terminal(client, q):
    # parked flow (a delayed child keeps it in flight) -> detail carries the refresher
    parked = await q.add_flow("parked", {}, children=[c("later", {}, delay=600_000)])
    r = await client.get(f"/queues/{QUEUE}/jobs/{parked.id}/detail")
    assert f"/jobs/{parked.id}/flow" in r.text  # self-updating flow fragment is live

    # a completed flow -> NO refresher (else it would poll the server forever)
    done = await q.add_flow("done", {}, children=[c("leaf", {})])

    async def proc(job):
        return "ok" if job.name == "leaf" else (await job.children_results())

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        j = await q.get_job(done.id)
        if j and j.state == "completed":
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()

    r = await client.get(f"/queues/{QUEUE}/jobs/{done.id}/detail")
    assert "3/3" not in r.text  # (sanity: it's a 1-child flow)
    assert f"/jobs/{done.id}/flow" not in r.text  # not live -> no self-refresh


async def test_per_node_retry_button_only_on_failed_children_not_the_root(client, q):
    parent = await q.add_flow("publish", {}, children=[c("good", {}), c("bad", {})])

    async def proc(job):
        if job.name == "bad":
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

    tree = await q.get_flow(parent.id)
    good_id = tree["children"][0]["job"].id
    bad_id = tree["children"][1]["job"].id

    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert f"/jobs/{bad_id}/retry-node" in r.text  # failed child: per-node retry
    assert f"/jobs/{good_id}/retry-node" not in r.text  # completed child: no retry
    assert f"/jobs/{parent.id}/retry-node" not in r.text  # root: NO per-node retry ...
    assert f"/jobs/{parent.id}/retry-flow" in r.text  # ... it has retry flow instead


async def test_failed_flow_with_a_retried_child_stays_live_then_stops(client, q):
    """The bug: a failed flow's detail had no live refresher, so a per-node retry
    running under it didn't push the child's state flip - you had to reload. It
    must be live while the retried child runs, then self-stop."""
    parent = await q.add_flow("publish", {}, children=[c("good", {}), c("bad", {})])
    fail = {"on": True}

    async def proc(job):
        if job.name == "bad" and fail["on"]:
            raise RuntimeError("boom")
        return "ok"

    async def _run_until(pred):
        worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
        task = asyncio.create_task(worker.run())
        for _ in range(200):
            if await pred():
                break
            await asyncio.sleep(0.02)
        await worker.stop(grace_period=0)
        task.cancel()

    await _run_until(lambda: _is_state(q, parent.id, "failed"))

    sel = f"/jobs/{parent.id}/flow"  # the self-updating flow fragment's hx-get
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert sel not in r.text  # a settled failed flow is NOT live

    bad_id = (await q.get_flow(parent.id))["children"][1]["job"].id
    fail["on"] = False
    await q.retry_job(bad_id)  # per-node retry: the child is now in `wait`
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert sel in r.text  # in-flight again -> live, even though the parent is `failed`

    await _run_until(lambda: _is_state(q, bad_id, "completed"))
    r = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    assert sel not in r.text  # everything terminal again -> self-stopped


async def _is_state(q, jid, state):
    j = await q.get_job(jid)
    return j is not None and j.state == state
