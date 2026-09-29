"""Integration: a job's contents are attacker-influenced, and the page renders them.

A dashboard shows whatever a web app's users put into a job: the payload, the name,
the exception message, the stack trace, the log lines. None of that is trusted, and
two properties have to hold whatever it contains: it cannot execute, and it cannot
make the page too big to use.
"""

import asyncio

from toro import FlowChild, Worker

from .conftest import PREFIX, QUEUE, hx

BIG = 200_000  # one payload, far past anything a person reads


async def test_a_fat_payload_does_not_fill_the_listing(client, q):
    """The listing renders every row's data, and the live refresh re-fetches that
    fragment on every change event, about once a second per open tab. One job with a
    megabyte in it made the dashboard unusable and saturated the host app with it."""
    for _ in range(5):
        await q.add("fat", {"blob": "x" * BIG})

    page = await client.get(f"/queues/{QUEUE}?state=wait", headers=hx())
    fragment = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())

    assert page.status_code == 200
    assert len(page.content) < 5 * BIG, f"{len(page.content):,} bytes for five rows"
    assert len(fragment.content) < 5 * BIG, f"{len(fragment.content):,} bytes for five rows"


async def _drain(q, proc, until) -> None:
    """Run one worker until `until(q)` holds. matador's integration layer has no
    worker fixture: the dashboard is the thing under test, and a worker is scenery."""
    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    worker.on("failed", lambda *a, **k: None)
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(1500):
            if await until():
                break
            await asyncio.sleep(0.02)
        assert await until(), "the scenery never settled"
    finally:
        await worker.stop(grace_period=0)
        task.cancel()


async def test_a_fat_failure_does_not_fill_the_detail(client, q):
    """The same for what a failure carries: a message and a trace, both of which are
    whatever the exception said, rendered twice per row in the listing."""

    async def proc(job):
        raise RuntimeError("boom " + "y" * BIG)

    job = await q.add("breaks", {}, attempts=1)
    await _drain(q, proc, _failed(q))

    listing = await client.get(f"/queues/{QUEUE}?state=failed", headers=hx())
    detail = await client.get(f"/queues/{QUEUE}/jobs/{job.id}/detail", headers=hx())

    assert len(listing.content) < 2 * BIG, f"{len(listing.content):,} bytes"
    assert len(detail.content) < 2 * BIG, f"{len(detail.content):,} bytes"
    assert "boom" in detail.text, "the truncation hid what the failure was"


async def test_a_flood_of_log_lines_does_not_fill_the_detail(client, q):
    """A processor in a loop writes as many log lines as it likes."""

    async def proc(job):
        for i in range(3000):
            await job.log(f"line {i} " + "z" * 200)
        return 1

    job = await q.add("chatty", {}, remove_on_complete=False)
    await _drain(q, proc, _completed(q))

    detail = await client.get(f"/queues/{QUEUE}/jobs/{job.id}/detail", headers=hx())

    assert len(detail.content) < 500_000, f"{len(detail.content):,} bytes for 3000 lines"
    assert "line 2999" in detail.text, "the newest lines are the ones worth keeping"


def _failed(q):
    async def check() -> bool:
        return (await q.counts())["failed"] >= 1

    return check


def _completed(q):
    async def check() -> bool:
        return (await q.counts())["completed"] >= 1

    return check


MARK = "<xsSmark onx=1>"


async def test_nothing_a_job_carries_can_become_markup(client, q):
    """Autoescape is on, and this is the test that keeps it on. Everything here is
    written by whoever enqueued the job, and it is rendered in a title attribute, a
    tooltip, a confirm message, a JSON block and a log line."""
    await q.add(MARK, {"deep": {"value": MARK}}, job_id=f"id{MARK}", concurrency_key=MARK)

    async def proc(job):
        await job.log(MARK)
        raise RuntimeError(MARK)

    await _drain(q, proc, _failed(q))

    pages = [
        f"/queues/{QUEUE}?state=failed",
        f"/queues/{QUEUE}/jobs?state=failed",
        f"/queues/{QUEUE}?state=failed&query=xsS",
        "/workers",
        "/sidebar",
    ]
    for path in pages:
        r = await client.get(path, headers=hx())
        assert r.status_code == 200, path
        assert "<xsSmark" not in r.text, f"{path} rendered it as markup"
        assert "&lt;xsSmark" in r.text or "xsS" not in r.text, path


async def test_a_fat_child_failure_does_not_fill_the_flow_detail(client, q):
    """A flow parent lists its children's failure reasons twice: in the failures
    list under `continue`, and on each node of the tree. Both are whatever the child's
    exception said."""

    async def proc(job):
        if job.name == "bad":
            raise RuntimeError("boom " + "y" * BIG)
        return 1

    parent = await q.add_flow("report", {}, children=[FlowChild("bad", {}, on_fail="continue")])

    async def parent_done() -> bool:
        return (await q.counts())["completed"] >= 1

    await _drain(q, proc, parent_done)

    detail = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/detail")
    flow = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/flow")

    assert detail.status_code == flow.status_code == 200
    assert len(detail.content) < BIG, f"{len(detail.content):,} bytes for one flow"
    assert len(flow.content) < BIG, f"{len(flow.content):,} bytes for one flow"


async def test_a_malformed_origin_is_refused_not_a_crash(client, q):
    """The origin check split the Origin header with urlsplit, which raises on a
    bracket that opens no IPv6 literal: a sender's own bad header answered 500."""
    r = await client.post(f"/queues/{QUEUE}/pause", headers={"origin": "http://["})
    assert r.status_code == 403


async def test_a_malformed_current_url_falls_back_to_the_queue(client, q, seeded):
    """The job page's back link came from HX-Current-URL through the same urlsplit."""
    r = await client.get(
        f"/queues/{QUEUE}/jobs/{seeded['completed']}", headers=hx(**{"HX-Current-URL": "http://["})
    )
    assert r.status_code == 200
    assert f"/queues/{QUEUE}" in r.text


async def test_a_search_under_an_unknown_state_renders_the_coerced_state(client, q, seeded):
    """The search branch scanned the coerced state and rendered the raw one: the label
    read "scanned the most recent 500 bogus jobs", the results drew bulk checkboxes
    the active tab never allows, and their delete posted state=bogus."""
    r = await client.get(f"/queues/{QUEUE}/jobs?state=bogus&query=alph", headers=hx())
    assert r.status_code == 200
    assert "bogus" not in r.text
