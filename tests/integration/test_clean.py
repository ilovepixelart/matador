"""Integration: the Clean action drains a state in batches, not just one 1000-cap call."""

from matador.service import Service

from .conftest import PREFIX, QUEUE


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
