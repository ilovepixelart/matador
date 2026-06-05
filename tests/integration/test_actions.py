"""Integration: action routes mutate queue state and return 200 + the panel."""

from .conftest import QUEUE, hx


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
        data={"ids": f"{a},{b}"},  # the comma-joined client selection
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
