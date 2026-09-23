"""Integration: a job's contents are attacker-influenced, and the page renders them.

A dashboard shows whatever a web app's users put into a job: the payload, the name,
the exception message, the stack trace, the log lines. None of that is trusted, and
two properties have to hold whatever it contains: it cannot execute, and it cannot
make the page too big to use.
"""

import asyncio

from toro import Worker

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
