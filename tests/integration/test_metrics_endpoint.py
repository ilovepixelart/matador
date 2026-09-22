"""Integration: the scrape endpoint the mount serves (toro docs/specs/operate.md)."""

from .conftest import QUEUE


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


async def test_metrics_needs_no_htmx_and_no_session(client, seeded):
    """A scraper is not a browser: it sends no HX headers and follows no redirect."""
    r = await client.get("/metrics")

    assert r.status_code == 200
    assert "<html" not in r.text
