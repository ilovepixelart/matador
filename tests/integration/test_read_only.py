"""Integration: read-only mode (toro docs/specs/operate.md).

A dashboard that can pause a queue and delete jobs cannot be shared with people who
should only look at it. The guard is a predicate the mounting app supplies, because
matador has no identity of its own: the raw request is where the host app's identity
arrives.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from toro import FlowChild

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


async def test_a_dashboard_that_may_mutate_draws_them_all(client, q, seeded):
    """The mirror of every assertion above: absence proves nothing unless the same
    markup is present when mutating is allowed. Read-only is opt-in."""
    await q.add_flow("publish", {}, children=[FlowChild("a", {}, delay=60_000)])

    failed = await client.get(f"/queues/{QUEUE}?state=failed", headers=hx())
    active = await client.get(f"/queues/{QUEUE}?state=active", headers=hx())

    assert "Remove this job" in failed.text
    assert "retry all" in failed.text.lower()
    assert "cancel parked flows" in active.text


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
