"""Triage info on job rows: the failed list leads with the failure reason, and
every row carries a relative timestamp (with the absolute time on hover).
"""

from .conftest import QUEUE, hx


async def test_failed_rows_lead_with_the_reason(client, seeded):
    r = await client.get(f"/queues/{QUEUE}?state=failed", headers=hx())
    assert r.status_code == 200
    # The seeded failure raises RuntimeError("boom") - the reason must be
    # visible in the LIST, not only after expanding the row.
    assert "boom" in r.text


async def test_rows_carry_a_relative_timestamp(client, seeded):
    for state in ("completed", "failed", "wait"):
        r = await client.get(f"/queues/{QUEUE}?state={state}", headers=hx())
        assert r.status_code == 200
        assert "ago</span>" in r.text, f"no relative time on {state} rows"
    # Hover reveals the absolute moment (title attribute from the `at` filter).
    assert 'title="' in r.text


async def test_delayed_rows_show_when_they_are_due(client, seeded):
    r = await client.get(f"/queues/{QUEUE}?state=delayed", headers=hx())
    assert r.status_code == 200
    assert "due in" in r.text or "due now" in r.text


async def test_job_names_expose_their_full_text_on_hover(client, seeded):
    # The name column truncates; the untruncated name must be one hover away.
    r = await client.get(f"/queues/{QUEUE}?state=wait", headers=hx())
    assert 'title="alpha"' in r.text


async def test_queue_without_explicit_state_lands_on_the_signal_tab(client, seeded):
    # Seeded queue: 0 active, 1 failed - a bare /queues/{name} should land on
    # failed (the tab with signal), not an empty active list.
    r = await client.get(f"/queues/{QUEUE}", headers=hx())
    assert "badjob" in r.text

    # An explicit state is always respected.
    r = await client.get(f"/queues/{QUEUE}?state=wait", headers=hx())
    assert "alpha" in r.text


async def test_detail_reads_chronologically_logs_then_error(client, q, seeded):
    # the story of a failure in order: what it logged, what it raised, the trace
    await q.redis.rpush(q.keys.logs("2"), "attempt 1 crashed")  # as the worker would
    r = await client.get(f"/queues/{QUEUE}/jobs/2/detail", headers=hx())
    assert r.status_code == 200
    html = r.text.lower()
    assert html.index(">logs<") < html.index(">error<") < html.index(">stack trace<")


async def test_a_held_row_names_the_key_it_waits_on(client, q):
    """HJ-002: "why is this not running" is the only question a held row has to
    answer, and the key is the answer."""
    await q.add("holder", {}, concurrency_key="invoice-42")
    await q.add("behind", {}, concurrency_key="invoice-42")

    r = await client.get(f"/queues/{QUEUE}?state=held", headers=hx())

    assert r.status_code == 200
    assert "invoice-42" in r.text


async def test_rows_without_a_key_say_nothing_about_one(client, seeded):
    r = await client.get(f"/queues/{QUEUE}?state=wait", headers=hx())
    assert "alpha" in r.text  # the rows are there
    assert "on key" not in r.text  # and none of them claims a key


async def test_active_rows_offer_to_stop_the_job(client, q):
    """A running job cannot be retried or promoted, so cancel is the action its row
    has to carry."""
    job = await q.add("running", {})
    await q.redis.zrem(q.keys.prioritized, job.id)
    await q.redis.rpush(q.keys.active, job.id)
    await q.redis.hset(q.keys.job(job.id), "state", "active")

    r = await client.get(f"/queues/{QUEUE}?state=active", headers=hx())

    assert r.status_code == 200
    assert "Stop this job" in r.text


async def test_waiting_rows_do_not_offer_to_stop(client, seeded):
    r = await client.get(f"/queues/{QUEUE}?state=wait", headers=hx())
    assert "Stop this job" not in r.text  # nothing to stop: remove it instead
