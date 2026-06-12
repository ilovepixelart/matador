"""Integration: matador mounted under a sub-path emits correctly-prefixed URLs.

This is the test that proves "easily integratable" - `host.mount("/admin/queues", …)`
must produce links/assets/SSE under that prefix, with no bare-root URLs left.
"""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from matador import create_app

from .conftest import PREFIX, QUEUE, hx

MOUNT = "/admin/queues"


def _host():
    host = FastAPI()
    host.mount(MOUNT, create_app([QUEUE], url="redis://localhost:6379", prefix=PREFIX))
    return host


async def test_mounted_full_page_prefixes_every_url(seeded):
    transport = ASGITransport(app=_host())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"{MOUNT}/")
        assert r.status_code == 200
        # assets + endpoints all carry the mount prefix ...
        assert f"{MOUNT}/static/app.css" in r.text
        assert f"{MOUNT}/stream" in r.text
        assert f"{MOUNT}/redis" in r.text
        assert f"{MOUNT}/queues/{QUEUE}" in r.text
        # ... and no bare-root URL leaked through
        assert 'href="/static/app.css' not in r.text
        assert 'sse-connect="/stream"' not in r.text
        assert 'hx-get="/redis"' not in r.text


async def test_mounted_fragment_and_actions_work(seeded):
    transport = ASGITransport(app=_host())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # an htmx fragment swap under the mount renders + prefixes its own links
        r = await c.get(f"{MOUNT}/queues/{QUEUE}?state=wait", headers=hx())
        assert r.status_code == 200
        assert "alpha" in r.text
        assert f"{MOUNT}/queues/{QUEUE}" in r.text  # tab/pagination links prefixed
        # the sidebar highlights the active queue from a PREFIXED HX-Current-URL
        r2 = await c.get(
            f"{MOUNT}/sidebar",
            headers=hx(**{"HX-Current-URL": f"http://test{MOUNT}/queues/{QUEUE}"}),
        )
        assert r2.status_code == 200
        assert "q-active" in r2.text  # active highlight resolved


async def test_dependencies_protect_every_route(seeded):
    from fastapi import Depends, Header, HTTPException

    def require_token(x_token: str = Header(default="")):
        if x_token != "secret":
            raise HTTPException(status_code=401)

    app = create_app(
        [QUEUE],
        url="redis://localhost:6379",
        prefix=PREFIX,
        dependencies=[Depends(require_token)],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/")).status_code == 401  # blocked without auth
        ok = await c.get("/", headers={"x-token": "secret"})
        assert ok.status_code == 200  # allowed with auth
        # write actions are gated too, not just reads
        assert (await c.post(f"/queues/{QUEUE}/pause")).status_code == 401


async def test_dashboard_is_self_hosted(client, seeded):
    """Regression guard for two bugs manual browser-debugging surfaced (which the
    HTML-only tests missed): the dashboard must be self-contained - no CDN scripts,
    so it works offline - and its CSS must use a relative font url() so the font
    resolves under a sub-path mount. (Hardcoded-template-URL regressions are caught by
    the mounted-page test above, which checks for bare-root URLs under the prefix.)"""
    from pathlib import Path

    import matador

    html = (await client.get(f"/queues/{QUEUE}")).text
    assert "unpkg" not in html and 'src="https://' not in html  # scripts are vendored

    css = (Path(matador.__file__).parent / "static" / "app.css").read_text()
    assert "url(/" not in css  # relative font url() survives a sub-path mount


async def test_shared_connection_is_not_closed_by_matador(q):
    import redis.asyncio as aioredis

    from matador.service import Service

    conn = aioredis.from_url("redis://localhost:6379", decode_responses=True)
    svc = Service([QUEUE], url="redis://unused", prefix=PREFIX, connection=conn)
    assert await svc.overview() is not None  # reads work over the borrowed client
    await svc.close()  # must be a no-op for a borrowed connection
    assert await conn.ping() is True  # ... so it's still alive
    await conn.aclose()
