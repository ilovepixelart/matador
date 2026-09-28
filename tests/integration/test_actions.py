"""Integration: action routes mutate queue state and return 200 + the panel."""

import json

import pytest

from .conftest import QUEUE, hx


async def test_clear_departed_workers(client, q):
    await q.redis.lpush(q.keys.departed, '{"id": "w1", "reason": "stopped"}')
    assert await q.redis.llen(q.keys.departed) == 1
    r = await client.post("/workers/departed/clear", headers=hx())
    assert r.status_code == 200
    assert await q.redis.llen(q.keys.departed) == 0  # history cleared


async def test_bulk_remove_caps_count(client, q, seeded):
    too_many = json.dumps([str(i) for i in range(1, 1002)])  # 1001 > MAX_BULK_REMOVE
    r = await client.post(
        f"/queues/{QUEUE}/jobs/bulk-remove?state=wait", data={"ids": too_many}, headers=hx()
    )
    assert r.status_code == 413  # rejected, not silently fanned out


async def test_pause_then_resume(client, q, seeded):
    assert (await client.post(f"/queues/{QUEUE}/pause", headers=hx())).status_code == 200
    assert await q.is_paused() is True
    assert (await client.post(f"/queues/{QUEUE}/resume", headers=hx())).status_code == 200
    assert await q.is_paused() is False


async def test_retry_moves_failed_to_wait(client, q, seeded):
    before = await q.counts()
    url = f"/queues/{QUEUE}/jobs/{seeded['failed']}/retry?state=failed"
    r = await client.post(url, headers=hx())
    assert r.status_code == 200
    after = await q.counts()
    assert after["failed"] == before["failed"] - 1
    assert after["wait"] == before["wait"] + 1


async def test_remove_deletes_the_job(client, q, seeded):
    jid = seeded["waits"][0]
    r = await client.request("DELETE", f"/queues/{QUEUE}/jobs/{jid}?state=wait", headers=hx())
    assert r.status_code == 200
    assert await q.get_job(jid) is None


async def test_promote_moves_delayed_to_wait(client, q, seeded):
    delayed = await q.get_jobs("delayed", 0, 10)
    assert delayed, "scheduler should have seeded a delayed occurrence"
    before = (await q.counts())["wait"]
    r = await client.post(f"/queues/{QUEUE}/jobs/{delayed[0].id}/promote", headers=hx())
    assert r.status_code == 200
    assert (await q.counts())["wait"] == before + 1


async def test_retry_all_clears_failed(client, q, seeded):
    assert (await client.post(f"/queues/{QUEUE}/retry-all", headers=hx())).status_code == 200
    assert (await q.counts())["failed"] == 0


async def test_clean_empties_a_state(client, q, seeded):
    assert (await client.post(f"/queues/{QUEUE}/clean?state=wait", headers=hx())).status_code == 200
    assert (await q.counts())["wait"] == 0


async def test_clean_rejects_non_cleanable_states(client, q, seeded):
    # a crafted clean of active/parked/garbage must NOT coerce to a destructive
    # default (the old _coerce_state turned these into "active" and deleted running
    # jobs); the route rejects them and leaves every state untouched.
    before = await q.counts()
    for state in ("active", "waiting-children", "nonsense"):
        r = await client.post(f"/queues/{QUEUE}/clean?state={state}", headers=hx())
        assert r.status_code == 400, state
    assert await q.counts() == before  # nothing was removed


async def test_trigger_scheduler_enqueues_one(client, q, seeded):
    sid = (await q.schedulers())[0]["id"]
    before = (await q.counts())["wait"]
    r = await client.post(f"/queues/{QUEUE}/schedulers/{sid}/trigger", headers=hx())
    assert r.status_code == 200
    assert (await q.counts())["wait"] == before + 1


async def test_remove_scheduler(client, q, seeded):
    sid = (await q.schedulers())[0]["id"]
    r = await client.request("DELETE", f"/queues/{QUEUE}/schedulers/{sid}", headers=hx())
    assert r.status_code == 200
    assert await q.schedulers() == []


async def test_bulk_remove_deletes_only_the_selected_ids(client, q, seeded):
    a, b, c = seeded["waits"]  # three waiting jobs
    before = (await q.counts())["wait"]
    r = await client.post(
        f"/queues/{QUEUE}/jobs/bulk-remove?state=wait",
        data={"ids": json.dumps([a, b])},  # the client selection, a JSON array
        headers=hx(),
    )
    assert r.status_code == 200
    assert (await q.counts())["wait"] == before - 2
    assert await q.get_job(a) is None
    assert await q.get_job(b) is None
    assert await q.get_job(c) is not None  # the un-selected one stays


async def test_bulk_remove_with_no_ids_is_a_noop(client, q, seeded):
    before = (await q.counts())["wait"]
    r = await client.post(
        f"/queues/{QUEUE}/jobs/bulk-remove?state=wait", data={"ids": ""}, headers=hx()
    )
    assert r.status_code == 200
    assert (await q.counts())["wait"] == before


async def test_acting_on_a_missing_job_returns_a_toast(client, q):
    # response-targets routes this 4xx into #toast instead of failing silently.
    r = await client.request("DELETE", f"/queues/{QUEUE}/jobs/ghost-999", headers=hx())
    assert r.status_code == 404
    assert "no longer here" in r.text
    assert 'role="alert"' in r.text


async def test_cancelling_a_running_job_from_the_dashboard(client, q):
    """The active tab is the only place a running job can be acted on, and stopping one
    is the action people reach for. Removing it leaves the processor running."""
    job = await q.add("long", {})
    await q.redis.zrem(q.keys.prioritized, job.id)  # as a worker's claim would
    await q.redis.rpush(q.keys.active, job.id)
    await q.redis.hset(q.keys.job(job.id), "state", "active")

    r = await client.post(f"/queues/{QUEUE}/jobs/{job.id}/cancel", headers=hx())

    assert r.status_code == 200
    assert await q.redis.hget(q.keys.job(job.id), "cancel") == "1"


async def test_cancelling_a_job_that_is_gone_says_so(client, q):
    # response-targets routes this 4xx into #toast instead of failing silently
    r = await client.post(f"/queues/{QUEUE}/jobs/nope/cancel", headers=hx())
    assert r.status_code == 404
    assert "no longer here" in r.text
    assert 'role="alert"' in r.text


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("retry", "Couldn&#39;t retry"),
        ("retry-node?parent=p1", "Couldn&#39;t retry"),
        ("promote", "Couldn&#39;t promote"),
    ],
)
async def test_retrying_or_promoting_a_job_that_is_gone_says_so(client, q, path, title):
    # the job vanished between render and click: a toast, not a 500 or a blank swap
    r = await client.post(f"/queues/{QUEUE}/jobs/nope/{path}", headers=hx())
    assert r.status_code == 404
    assert title in r.text
    assert "Job #nope is no longer here." in r.text
    assert 'role="alert"' in r.text


@pytest.mark.parametrize(
    "ids",
    ["a,b", '{"a": 1}', "[1, 2]", "[unclosed", pytest.param("[" * 100_000, id="nested-100k-deep")],
)
async def test_bulk_remove_refuses_a_selection_it_cannot_read(client, q, seeded, ids):
    """Anything but a JSON array of ids is refused rather than guessed at: a guess is
    what turned one selected `a,b` into two deleted jobs."""
    before = (await q.counts())["wait"]
    r = await client.post(
        f"/queues/{QUEUE}/jobs/bulk-remove?state=wait", data={"ids": ids}, headers=hx()
    )
    assert r.status_code == 400
    assert (await q.counts())["wait"] == before


async def test_a_scheduler_id_with_a_slash_is_shown_triggered_and_removed(client, q, seeded):
    """toro accepts "/" in a scheduler id. The routes took the id as one path segment,
    so building its buttons raised inside url_for and the whole queue page was a 500,
    and nothing could trigger or remove the scheduler from the dashboard."""
    await q.add_scheduler("reports/daily", cron="0 0 * * *")

    r = await client.get(f"/queues/{QUEUE}", headers=hx())
    assert r.status_code == 200
    assert "reports/daily" in r.text

    before = (await q.counts())["wait"]
    r = await client.post(f"/queues/{QUEUE}/schedulers/reports/daily/trigger", headers=hx())
    assert r.status_code == 200
    assert (await q.counts())["wait"] == before + 1

    r = await client.request("DELETE", f"/queues/{QUEUE}/schedulers/reports/daily", headers=hx())
    assert r.status_code == 200
    assert [s["id"] for s in await q.schedulers()] == ["nightly"]
