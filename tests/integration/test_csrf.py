"""Integration: the opt-in same-origin (CSRF) guard for state-changing routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from matador import create_app

from .conftest import PREFIX, QUEUE


@pytest.fixture
async def guarded_client(q):
    # `q` gives a clean, isolated queue; require_same_origin turns on the guard
    app = create_app([QUEUE], prefix=PREFIX, require_same_origin=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_cross_origin_mutation_is_blocked(guarded_client):
    r = await guarded_client.post(
        f"/queues/{QUEUE}/pause", headers={"origin": "http://evil.example"}
    )
    assert r.status_code == 403


async def test_same_origin_mutation_is_allowed(guarded_client):
    r = await guarded_client.post(f"/queues/{QUEUE}/pause", headers={"origin": "http://test"})
    assert r.status_code != 403


async def test_mutation_without_origin_is_allowed(guarded_client):
    # non-browser clients omit Origin; don't break them
    r = await guarded_client.post(f"/queues/{QUEUE}/pause")
    assert r.status_code != 403


async def test_cross_origin_get_is_not_blocked(guarded_client):
    # safe methods are never blocked, regardless of Origin
    r = await guarded_client.get("/", headers={"origin": "http://evil.example"})
    assert r.status_code != 403


async def test_guard_off_by_default(client):
    # the default app ships no guard — a cross-origin POST is not 403'd by matador
    r = await client.post(f"/queues/{QUEUE}/pause", headers={"origin": "http://evil.example"})
    assert r.status_code != 403


async def test_stacktrace_hidden_when_disabled(q, seeded):
    # show_stacktraces=False omits the trace (info-disclosure control)
    app = create_app([QUEUE], prefix=PREFIX, show_stacktraces=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/queues/{QUEUE}/jobs/{seeded['failed']}/detail", headers={"HX-Request": "true"}
        )
        assert r.status_code == 200
        assert "stack trace" not in r.text


async def test_guard_auto_enables_when_auth_is_configured(q):
    # cookie-based auth is what makes CSRF real, so configuring dependencies
    # turns the origin guard on unless explicitly overridden
    from fastapi import Depends

    async def allow():
        return True

    app = create_app([QUEUE], prefix=PREFIX, dependencies=[Depends(allow)])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(f"/queues/{QUEUE}/pause", headers={"origin": "http://evil.example"})
        assert r.status_code == 403


async def test_guard_override_wins_over_auto_enable(q):
    from fastapi import Depends

    async def allow():
        return True

    app = create_app(
        [QUEUE], prefix=PREFIX, dependencies=[Depends(allow)], require_same_origin=False
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(f"/queues/{QUEUE}/pause", headers={"origin": "http://evil.example"})
        assert r.status_code != 403


def test_unauthenticated_app_warns_at_startup(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="matador"):
        create_app([QUEUE], prefix=PREFIX)
    assert any("no auth" in r.message for r in caplog.records)


def test_authenticated_app_does_not_warn(caplog):
    import logging

    from fastapi import Depends

    async def allow():
        return True

    with caplog.at_level(logging.WARNING, logger="matador"):
        create_app([QUEUE], prefix=PREFIX, dependencies=[Depends(allow)])
    assert not any("no auth" in r.message for r in caplog.records)
