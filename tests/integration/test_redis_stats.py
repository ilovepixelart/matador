"""Integration: the Redis health bar degrades instead of 500ing on a transient blip."""

from matador.service import Service

from .conftest import PREFIX, QUEUE


async def test_redis_stats_ok_when_healthy(q):
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    stats = await svc.redis_stats()
    assert stats["ok"] is True
    assert stats["version"] != "?"
    await svc.close()


async def test_redis_stats_degrades_on_error(q, monkeypatch):
    svc = Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    target = next(iter(svc.queues.values())).redis

    async def boom(*_a, **_k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(target, "info", boom)
    stats = await svc.redis_stats()  # must NOT raise - the 8s-polled bar can't 500
    assert stats["ok"] is False
    assert stats["version"] == "?"
    assert stats["ops"] == 0
    await svc.close()
