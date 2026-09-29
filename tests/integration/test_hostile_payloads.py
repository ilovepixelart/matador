"""Integration: a job's contents are attacker-influenced, and the page renders them.

A dashboard shows whatever a web app's users put into a job: the payload, the name,
the exception message, the stack trace, the log lines. None of that is trusted, and
two properties have to hold whatever it contains: it cannot execute, and it cannot
make the page too big to use.
"""

import asyncio

from toro import FlowChild, Queue, Worker

from matador.service import MAX_LOG_LINES

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


async def test_a_flood_of_log_lines_is_read_bounded(client, q, monkeypatch):
    """The detail read every log line (LRANGE 0 -1) and cut the tail afterwards: a job
    that logs in a loop made every render, and every event-driven refresh of an open
    flow page, read the whole list. The read is bounded to the lines shown, and the
    count of earlier lines comes from the list's length."""
    asked: list[tuple[int, int]] = []
    real = Queue.get_logs

    async def spy(self, job_id, start=0, end=-1):
        asked.append((start, end))
        return await real(self, job_id, start, end)

    monkeypatch.setattr(Queue, "get_logs", spy)

    async def proc(job):
        for i in range(MAX_LOG_LINES + 100):
            await job.log(f"line {i}")
        return 1

    job = await q.add("chatty", {}, remove_on_complete=False)
    await _drain(q, proc, _completed(q))

    detail = await client.get(f"/queues/{QUEUE}/jobs/{job.id}/detail", headers=hx())
    assert asked == [(-MAX_LOG_LINES, -1)], asked
    assert "100 earlier lines not shown" in detail.text
    assert f"line {MAX_LOG_LINES + 99}" in detail.text
