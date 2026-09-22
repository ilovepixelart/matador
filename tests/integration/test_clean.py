"""Integration: the Clean action drains a state in batches, not just one 1000-cap call."""

from matador.service import Service

from .conftest import PREFIX, QUEUE, hx


async def test_clean_drains_in_batches(q, monkeypatch):
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    target = next(iter(svc.queues.values()))
    batches = iter([1000, 1000, 250])  # two full batches then a partial → drained
    calls = {"n": 0}

    async def fake_clean(state, limit=1000):
        calls["n"] += 1
        return next(batches, 0)

    monkeypatch.setattr(target, "clean", fake_clean)
    total = await svc.clean(QUEUE, "failed")
    assert total == 2250  # summed across every batch, not capped at 1000
    assert calls["n"] == 3  # looped until a partial (drained) batch
    await svc.close()


async def test_held_jobs_can_be_cleaned(client, q):
    """HJ-004: a held job is removable like any other, and clean is how a tab full of
    them is drained."""
    await q.add("holder", {}, concurrency_key="k")
    await q.add("behind", {}, concurrency_key="k")

    r = await client.post(f"/queues/{QUEUE}/clean?state=held", headers=hx())

    assert r.status_code == 200
    assert (await q.counts())["held"] == 0
