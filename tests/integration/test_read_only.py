"""Integration: read-only mode (toro docs/specs/operate.md).

A dashboard that can pause a queue and delete jobs cannot be shared with people who
should only look at it. The guard is a predicate the mounting app supplies, because
matador has no identity of its own: the raw request is where the host app's identity
arrives.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from toro import FlowChild, Worker

from matador import create_app

from .conftest import PREFIX, QUEUE, hx


def _mutating_routes(app) -> list[tuple[str, str]]:
    """Every route that changes something, derived from the app itself.

    A hand-kept list is a list someone forgets: a route added later would be
    unguarded and nothing here would notice.
    """
    out: list[tuple[str, str]] = []

    def walk(routes) -> None:
        for route in routes:
            if type(route).__name__ == "Mount":  # /static is a sub-app, not our routes
                continue
            original = getattr(route, "original_router", None)  # an included router
            nested = getattr(route, "routes", None) or getattr(original, "routes", None)
            if nested:
                walk(nested)
                continue
            methods = getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}
            out.extend((method, route.path) for method in methods)

    walk(app.routes)
    return out


# Every way the page offers to change something. A control is an htmx verb or a
# posting form; reading the response for these finds a control nobody listed.
MUTATING_MARKUP = (
    "hx-post",
    "hx-delete",
    "hx-put",
    "hx-patch",
    'method="post"',
    # a checkbox is a control too: it selects rows for a bulk action, and drawing it
    # where nothing can be done with it is the invitation this feature removes
    'type="checkbox"',
)


class _Params(dict):
    """Fill any route parameter, including one this test has never heard of."""

    def __missing__(self, key: str) -> str:
        return "x"


def _readable_routes(app) -> list[str]:
    """Every GET route, derived from the app itself, so a page added later is read
    without being listed. `/stream` is excluded by hand: it is a stream, not a page,
    and it never returns.
    """
    out: list[str] = []

    def walk(routes) -> None:
        for route in routes:
            if type(route).__name__ == "Mount":  # /static is a sub-app, not our routes
                continue
            original = getattr(route, "original_router", None)
            nested = getattr(route, "routes", None) or getattr(original, "routes", None)
            if nested:
                walk(nested)
                continue
            if "GET" in getattr(route, "methods", set()) and route.path != "/stream":
                out.append(route.path)

    walk(app.routes)
    return out


async def _controls_drawn(client, app, params: _Params) -> dict[str, list[str]]:
    """What each readable page offers to change."""
    found = {}
    for path in _readable_routes(app):
        url = path.format_map(params)
        # every page, and the search variant of the ones that take a query: search
        # results render from a template of their own, which is how a set of bulk
        # checkboxes stayed visible in a dashboard that draws no other control
        urls = [url, f"{url}?query=j"] if "{name}" in path and "job_id" not in path else [url]
        markers: list[str] = []
        for one in urls:
            r = await client.get(one, headers=hx(), follow_redirects=True)
            assert r.status_code == 200, f"{one} -> {r.status_code}"
            markers += [marker for marker in MUTATING_MARKUP if marker in r.text]
        found[path] = sorted(set(markers))
    return found


@pytest.fixture
async def failed_flow(q):
    """A flow with a failed child: its tree draws a per-node retry and the parent a
    whole-flow retry, controls that appear on no other page."""
    parent = await q.add_flow("publish", {}, children=[FlowChild("ok", {}), FlowChild("bad", {})])

    async def proc(job):
        if job.name == "bad":
            raise RuntimeError("boom")
        return 1

    worker = Worker(QUEUE, proc, prefix=PREFIX, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(200):
        job = await q.get_job(parent.id)
        if job and job.state == "failed":
            break
        await asyncio.sleep(0.02)
    await worker.stop(grace_period=0)
    task.cancel()
    return parent


@pytest.fixture
async def unlocked(q):
    """The default dashboard: no predicate, so everything is allowed."""
    app = create_app([QUEUE], prefix=PREFIX)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, app


@pytest.fixture
async def locked(q):
    app = create_app([QUEUE], prefix=PREFIX, can_mutate=lambda request: False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, app


async def test_every_mutating_route_refuses(locked, seeded):
    """OP-007: derived from the route table, so a route added later is covered by
    construction rather than by someone remembering to list it."""
    client, app = locked
    routes = _mutating_routes(app)
    assert routes, "no mutating routes found: the derivation is broken"

    for method, path in routes:
        url = path.format(name=QUEUE, job_id=seeded["completed"], scheduler_id="nightly")
        r = await client.request(method, url, headers=hx())
        assert r.status_code == 403, f"{method} {path} was allowed in read-only mode"


async def test_reading_still_works(locked, seeded):
    client, _ = locked
    r = await client.get(f"/queues/{QUEUE}", headers=hx())
    assert r.status_code == 200


async def test_controls_are_not_drawn(locked, seeded):
    """OP-008: a button that exists and refuses is worse than one that is not there:
    it invites the click and reports a failure that was never a failure."""
    client, _ = locked

    r = await client.get(f"/queues/{QUEUE}?state=failed", headers=hx())

    assert "Remove this job" not in r.text
    assert "retry all" not in r.text.lower()


async def test_the_parked_flow_control_is_not_drawn_either(locked, q, seeded):
    """The bulk cancel for parked flows sits outside the row controls, on the active
    tab, which is the tab a read-only viewer lands on: a control drawn there is drawn
    where it is most likely to be clicked."""
    client, _ = locked
    await q.add_flow("publish", {}, children=[FlowChild("a", {}, delay=60_000)])
    assert (await q.counts())["waiting-children"] == 1

    r = await client.get(f"/queues/{QUEUE}?state=active", headers=hx())

    assert "cancel parked flows" not in r.text


async def test_no_page_a_viewer_can_reach_draws_a_control(locked, seeded, failed_flow, q):
    """OP-008, derived rather than listed: every page is fetched from the app's own
    route table and read for a control. A hand-listed set of strings only ever finds
    the controls somebody remembered to list."""
    client, app = locked
    params = _Params(name=QUEUE, job_id=failed_flow.id, scheduler_id="nightly")

    drawn = await _controls_drawn(client, app, params)

    offenders = {path: markers for path, markers in drawn.items() if markers}
    assert not offenders, f"read-only pages still draw controls: {offenders}"


async def test_a_dashboard_that_may_mutate_draws_them_all(unlocked, seeded, failed_flow, q):
    """The mirror, and the reason the crawl above means anything: the same pages,
    read the same way, with mutating allowed. A page that draws no control either way
    would let the read-only assertion pass while proving nothing. Read-only is opt-in,
    so the default dashboard is the mutating one."""
    client, app = unlocked
    await q.add_flow("parked", {}, children=[FlowChild("later", {}, delay=60_000)])
    params = _Params(name=QUEUE, job_id=failed_flow.id, scheduler_id="nightly")

    drawn = await _controls_drawn(client, app, params)

    # the pages the read-only crawl found controls on, before they were guarded
    for path in ("/queues/{name}", "/workers", "/queues/{name}/jobs/{job_id}"):
        assert drawn[path], f"{path} draws no control at all, so its absence proves nothing"
    active = await client.get(f"/queues/{QUEUE}?state=active", headers=hx())
    assert "cancel parked flows" in active.text


async def test_a_predicate_that_raises_leaves_a_dashboard_that_reads(q, seeded, caplog):
    """The predicate is the host app's code and runs on every request, reads included.
    When it cannot answer, the answer is no: a dashboard nobody can change beats one
    nobody can open, and the exception is reported rather than swallowed."""

    def boom(request):
        raise RuntimeError("the session store is down")

    app = create_app([QUEUE], prefix=PREFIX, can_mutate=boom)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        page = await c.get(f"/queues/{QUEUE}", headers=hx())
        mutating = await c.post(f"/queues/{QUEUE}/pause", headers=hx())

    assert page.status_code == 200, "a broken predicate took the whole dashboard down"
    assert "hx-post" not in page.text  # and left it read-only, not half-usable
    assert mutating.status_code == 403
    assert "can_mutate" in caplog.text


async def test_the_predicate_sees_the_request(q, seeded):
    """OP-009: the host app owns identity, so the predicate is handed the request
    rather than a boolean decided at mount time."""
    seen = []

    def can_mutate(request):
        seen.append(request.headers.get("x-role"))
        return request.headers.get("x-role") == "admin"

    app = create_app([QUEUE], prefix=PREFIX, can_mutate=can_mutate)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        denied = await c.post(f"/queues/{QUEUE}/pause", headers=hx(**{"x-role": "viewer"}))
        allowed = await c.post(f"/queues/{QUEUE}/pause", headers=hx(**{"x-role": "admin"}))

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert seen == ["viewer", "admin"]
