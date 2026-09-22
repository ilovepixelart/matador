"""Integration: the scrape endpoint the mount serves (toro docs/specs/operate.md)."""

from httpx import ASGITransport, AsyncClient
from toro import Queue

from matador import create_app

from .conftest import PREFIX, QUEUE


async def test_metrics_is_served_in_the_scrapers_content_type(client, seeded):
    """OP-006: a scraper decides how to parse by the content type, so text/plain
    would have it guess."""
    r = await client.get("/metrics")

    assert r.status_code == 200
    assert "openmetrics-text" in r.headers["content-type"]
    assert r.text.endswith("# EOF\n")


async def test_metrics_reports_every_queue_once(client, seeded):
    r = await client.get("/metrics")

    types = [line for line in r.text.splitlines() if line.startswith("# TYPE ")]
    assert len(types) == len(set(types)), "a family was declared twice"
    assert f'queue="{QUEUE}"' in r.text
    assert 'toro_jobs_total{queue="' in r.text


async def test_every_watched_queue_is_in_one_exposition(q, seeded):
    """A dashboard watches several queues and a scraper hits one endpoint: a queue is
    a label on the shared families, never an exposition of its own."""
    other = Queue("otherq", prefix=PREFIX)
    try:
        await other.add("j", {})
        app = create_app([QUEUE, other.name], prefix=PREFIX)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/metrics")

        assert f'queue="{QUEUE}"' in r.text
        assert f'queue="{other.name}"' in r.text, "a watched queue was left out"
        types = [line for line in r.text.splitlines() if line.startswith("# TYPE ")]
        assert len(types) == len(set(types)), "a family was declared twice"
    finally:
        keys = await other.redis.keys(other.keys.base + "*")
        if keys:
            await other.redis.delete(*keys)
        await other.close()


async def test_metrics_needs_no_htmx_and_no_session(client, seeded):
    """A scraper is not a browser: it sends no HX headers and follows no redirect."""
    r = await client.get("/metrics")

    assert r.status_code == 200
    assert "<html" not in r.text
