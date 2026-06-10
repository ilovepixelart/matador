"""Action outcomes are announced to assistive tech: a polite live region is
pre-declared in the shell (the attribute must exist BEFORE content changes),
and bulk actions swap their result message INTO it out-of-band.
"""

from .conftest import QUEUE, hx


async def test_the_live_region_is_predeclared_and_empty(client, q):
    r = await client.get("/")
    assert 'aria-live="polite"' in r.text
    assert 'id="announce"' in r.text


async def test_retry_all_announces_its_outcome(client, seeded):
    r = await client.post(f"/queues/{QUEUE}/retry-all", headers=hx())
    assert r.status_code == 200
    assert 'hx-swap-oob="innerHTML:#announce"' in r.text
    assert "queued for retry" in r.text


async def test_clean_announces_its_outcome(client, seeded):
    r = await client.post(f"/queues/{QUEUE}/clean?state=completed", headers=hx())
    assert 'hx-swap-oob="innerHTML:#announce"' in r.text
    assert "removed" in r.text


async def test_bulk_remove_announces_the_actual_count(client, seeded):
    ids = "nope-1,nope-2"  # jobs that don't exist
    r = await client.post(f"/queues/{QUEUE}/jobs/bulk-remove", data={"ids": ids}, headers=hx())
    assert "0 jobs removed" in r.text  # actual count, not submitted count
