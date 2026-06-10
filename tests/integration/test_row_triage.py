"""Triage info on job rows: the failed list leads with the failure reason, and
every row carries a relative timestamp (with the absolute time on hover).
"""

from .conftest import QUEUE, hx


async def test_failed_rows_lead_with_the_reason(client, seeded):
    r = await client.get(f"/queues/{QUEUE}?state=failed", headers=hx())
    assert r.status_code == 200
    # The seeded failure raises RuntimeError("boom") — the reason must be
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
    # Seeded queue: 0 active, 1 failed — a bare /queues/{name} should land on
    # failed (the tab with signal), not an empty active list.
    r = await client.get(f"/queues/{QUEUE}", headers=hx())
    assert "badjob" in r.text

    # An explicit state is always respected.
    r = await client.get(f"/queues/{QUEUE}?state=wait", headers=hx())
    assert "alpha" in r.text
