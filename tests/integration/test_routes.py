"""Integration: GET routes render the right HTML - full page vs htmx fragment."""

from .conftest import QUEUE, hx


async def test_index_lists_queues(client, seeded):
    r = await client.get("/")
    assert r.status_code == 200
    assert "<header" in r.text  # full chrome
    assert QUEUE in r.text  # the queue shows in the sidebar


async def test_queue_full_page_has_all_tabs(client, seeded):
    r = await client.get(f"/queues/{QUEUE}")
    assert r.status_code == 200
    assert "<header" in r.text  # full page, not a fragment
    for state in ("active", "wait", "delayed", "completed", "failed"):
        assert state in r.text


async def test_queue_htmx_returns_fragment_with_oob_sidebar(client, seeded):
    r = await client.get(f"/queues/{QUEUE}", headers=hx())
    assert r.status_code == 200
    assert "<header" not in r.text  # fragment, no full chrome
    assert 'id="sidebar"' in r.text and "hx-swap-oob" in r.text  # OOB sidebar refresh


async def test_jobs_fragment_lists_waiting_by_name(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())
    assert r.status_code == 200
    for name in ("alpha", "beta", "gamma"):
        assert name in r.text


async def test_jobs_fragment_failed_shows_failed_job(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs?state=failed", headers=hx())
    assert r.status_code == 200
    assert "badjob" in r.text


async def test_job_detail_renders_data_opts_and_result(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs/{seeded['completed']}", headers=hx())
    assert r.status_code == 200
    assert "ada@example.com" in r.text  # the job data is shown
    assert "opts" in r.text  # opts section
    assert "result" in r.text and "done" in r.text  # the return value section


async def test_job_detail_missing_id_renders_not_found(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs/does-not-exist", headers=hx())
    assert r.status_code == 200  # graceful, never a 500
    assert "not found" in r.text.lower()


async def test_job_page_renders_as_full_page(client, seeded):
    # the standalone job page must render as a FULL page (bookmark/reload), not just hx
    r = await client.get(f"/queues/{QUEUE}/jobs/{seeded['completed']}")
    assert r.status_code == 200
    assert "<header" in r.text  # full chrome
    assert "queue" in r.text.lower()  # carries its queue metadata
    # a gone job still renders the page, never a 500
    assert (await client.get(f"/queues/{QUEUE}/jobs/ghost-1")).status_code == 200


async def test_job_accordion_detail_fragment(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs/{seeded['completed']}/detail", headers=hx())
    assert r.status_code == 200
    assert "<header" not in r.text  # just the accordion body, no chrome
    assert "opts" in r.text


async def test_htmx_security_config_present(client, seeded):
    # explicit htmx hardening: self-requests-only + no sensitive-data history cache
    r = await client.get("/")
    assert r.status_code == 200
    assert 'name="htmx-config"' in r.text
    assert '"selfRequestsOnly": true' in r.text
    assert '"historyCacheSize": 0' in r.text


async def test_security_headers_present(client, seeded):
    r = await client.get("/")
    assert r.headers["x-frame-options"] == "DENY"  # clickjacking defense
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "referrer-policy" in r.headers


async def test_jobs_fragment_emits_tab_counts_for_sync(client, seeded):
    # the live table refresh carries the tab counts from the same snapshot, so the
    # state-tab badges and the list can't disagree on a fast-churning state
    r = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())
    assert r.status_code == 200
    assert 'id="tabcount-' in r.text
    assert "hx-swap-oob" in r.text


async def test_stacktrace_shown_by_default(client, seeded):
    r = await client.get(f"/queues/{QUEUE}/jobs/{seeded['failed']}/detail", headers=hx())
    assert "stack trace" in r.text


async def test_sidebar_fragment(client, seeded):
    r = await client.get(
        "/sidebar", headers=hx(**{"HX-Current-URL": f"http://test/queues/{QUEUE}"})
    )
    assert r.status_code == 200
    assert QUEUE in r.text


async def test_redis_bar_shows_stats(client, seeded):
    r = await client.get("/redis", headers=hx())
    assert r.status_code == 200
    assert "mem" in r.text.lower() or "jobs" in r.text.lower()  # renders real stats


async def test_unknown_queue_returns_404(client, seeded):
    r = await client.get("/queues/no-such-queue", headers=hx())
    assert r.status_code == 404  # not a 500
    assert "not found" in r.text.lower()  # styled error page
    assert "no-such-queue" in r.text


async def test_invalid_state_falls_back_to_active(client, seeded):
    r = await client.get(f"/queues/{QUEUE}?state=bogus", headers=hx())
    assert r.status_code == 200
    assert "search active" in r.text  # coerced to the active view


async def test_index_with_no_queues_configured_renders_empty(seeded):
    # a matador watching zero queues should still serve a valid page, not 500
    from httpx import ASGITransport, AsyncClient

    from matador import create_app

    app = create_app([], url="redis://localhost:6379", prefix="matadortest")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "<header" in r.text


async def test_static_assets_revalidate_instead_of_pinning(client):
    # Module imports (behaviors/*.js) carry no ?v= cache-buster, so static
    # responses must say no-cache: browsers revalidate with ETag (cheap 304s)
    # rather than serving a stale behavior module forever.
    r = await client.get("/static/js/behaviors/tooltips.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
