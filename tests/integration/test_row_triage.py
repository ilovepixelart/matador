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
