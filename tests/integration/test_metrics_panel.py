"""Integration: the per-queue metrics strip (chart + headline chips)."""

from .conftest import QUEUE, hx


async def test_metrics_partial_renders_chips(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert r.status_code == 200
    assert 'id="metrics-strip"' in r.text
    # seeded ran 1 completed + 1 failed through a real worker this minute
    assert "done" in r.text and "failed" in r.text
    assert "latency" in r.text
    assert 'role="img"' not in r.text  # the chart lives in the sidebar, not the panel


async def test_metrics_counts_reflect_seeded_jobs(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    # both headline counts carry a data hook so tests (and users' own probes)
    # don't have to scrape formatted text
    assert 'data-done="1"' in r.text
    assert 'data-failed="1"' in r.text


async def test_metrics_latency_is_live_with_waiting_jobs(client, seeded):
    # seeded leaves 3 jobs waiting, so latency must be a real number, not the dash
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert 'data-latency="0"' not in r.text


async def test_metrics_unknown_queue_404s(client, seeded):
    r = await client.get("/queues/nope/metrics", headers=hx())
    assert r.status_code == 404


async def test_queue_panel_renders_metrics_inline(client, seeded):
    r = await client.get(f"/queues/{QUEUE}")
    assert r.status_code == 200
    assert 'id="metrics-strip"' in r.text  # inline with the panel, no pop-in
    assert f"/queues/{QUEUE}/metrics" in r.text  # live refresh still wired


async def test_sidebar_shows_a_sparkline_for_every_queue(client, seeded):
    # the sidebar sparkline is THE chart for a queue — selected or not
    r = await client.get(f"/queues/{QUEUE}")
    assert r.status_code == 200
    assert 'class="q-spark' in r.text
