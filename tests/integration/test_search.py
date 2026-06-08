"""Integration: in-state search — by name/data substring and by exact job id."""

from .conftest import QUEUE, hx


async def test_search_narrows_by_substring(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait&query=alph", headers=hx())
    assert r.status_code == 200
    assert "alpha" in r.text
    assert "beta" not in r.text and "gamma" not in r.text


async def test_search_by_exact_id(client, seeded):
    jid = seeded["waits"][1]  # beta's id
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait&query={jid}", headers=hx())
    assert r.status_code == 200
    assert "beta" in r.text
    assert "exact id match" in r.text  # the exact-hit badge renders


async def test_substring_search_has_no_exact_badge(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait&query=alph", headers=hx())
    assert r.status_code == 200
    assert "exact id match" not in r.text  # substring hit, not an exact id


async def test_search_no_match_returns_empty(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait&query=zzznope", headers=hx())
    assert r.status_code == 200
    assert "alpha" not in r.text and "beta" not in r.text and "gamma" not in r.text
