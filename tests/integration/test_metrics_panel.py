"""Integration: the per-queue metrics strip (chart + headline chips)."""

import asyncio
import time

from toro import Worker

from .conftest import PREFIX, QUEUE, hx


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


async def test_failed_chip_absent_when_nothing_failed(client, q):
    # "0 failed" is non-data ink: the chip must not render at all on a clean queue
    await q.add("okjob", {})

    async def proc(job):
        return "ok"

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        if (await q.counts())["completed"] >= 1:
            break
        await asyncio.sleep(0.02)
    await worker.stop()
    task.cancel()

    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert 'data-done="1"' in r.text
    assert "data-failed" not in r.text  # no failures -> no red ink at all


async def test_failure_share_is_rendered(client, seeded):
    # seeded: 1 completed + 1 failed -> 50.0% failure share next to the count
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert "50.0%" in r.text


async def test_latency_chip_warns_past_threshold(client, q, seeded):
    # age the head-of-line job >30s: the chip must switch to the warning color
    waits = await q.get_jobs("wait", 0, 1)
    aged = str(int(time.time() * 1000) - 40_000)
    await q.redis.hset(q.keys.job(waits[0].id), "timestamp", aged)
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert "text-warning" in r.text  # latency >= 30s renders as a warning


async def test_sidebar_sparkline_dims_the_in_progress_minute(client, seeded):
    # the current minute renders at reduced opacity so it never reads as a crash
    r = await client.get(f"/queues/{QUEUE}")
    assert "opacity-40" in r.text


async def test_chips_show_percentiles_not_average(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert "p95" in r.text  # the tail, not a mean that hides it
    assert "avg" not in r.text


async def test_panel_lists_jobs_by_name_failures_first(client, seeded):
    r = await client.get(f"/queues/{QUEUE}")
    assert 'id="names"' in r.text
    # seeded ran okjob (completed) and badjob (failed) — badjob must lead
    assert r.text.index("badjob") < r.text.index("okjob")
    assert "p95" in r.text


async def test_sparse_percentiles_are_dimmed(client, seeded):
    # seeded completes ONE job — p95 of n=1 is just the slowest job's bucket,
    # so the cell renders dimmed with the sample-size explanation
    r = await client.get(f"/queues/{QUEUE}")
    assert 'data-sparse="p95"' in r.text


async def test_percentile_chip_discloses_estimation(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert "estimate" in r.text.lower()  # we say it's bucketed, like the big tools do
