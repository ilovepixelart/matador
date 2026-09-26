"""Integration: matador mounted under a sub-path emits correctly-prefixed URLs.

This is the test that proves "easily integratable" - `host.mount("/admin/queues", …)`
must produce links/assets/SSE under that prefix, with no bare-root URLs left.
"""

import html
import re

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

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


TRAP = "/host-trap"
# Every attribute that carries a URL the browser will follow, fetch or post to.
_URL_ATTRS = re.compile(
    r'\b(?:href|src|action|hx-get|hx-post|hx-delete|hx-put|hx-patch|sse-connect)="([^"]*)"'
)


def _leaves(routes):
    """Every route in a table, nested routers flattened. FastAPI 0.141 keeps an
    included router's routes behind `original_router`, so walk by shape rather
    than by type: a Mount, or anything with `param_convertors` (every route has
    them, possibly empty), is a leaf."""
    for route in routes:
        if isinstance(route, Mount) or hasattr(route, "param_convertors"):
            yield route
        else:
            nested = getattr(route, "original_router", route)
            yield from _leaves(getattr(nested, "routes", []))


def _namesake() -> dict:
    return {}  # never reached: only its name and parameters matter


def _with_namesakes(order: str, trap_dir) -> tuple[FastAPI, set[str]]:
    """A host app that owns a route under every name matador uses, taking the same
    path parameters, so each of matador's lookups has a namesake it could resolve to.

    Generated from matador's own route table, so a route matador adds later is
    covered without touching this test.
    """
    dashboard = create_app([QUEUE], url="redis://localhost:6379", prefix=PREFIX)
    host = FastAPI()
    names: set[str] = set()

    def namesakes() -> None:
        for route in _leaves(dashboard.routes):
            names.add(route.name)
            if isinstance(route, Mount):
                host.mount(f"{TRAP}/{route.name}", StaticFiles(directory=trap_dir), name=route.name)
                continue
            params = "".join(f"/{{{p}}}" for p in route.param_convertors)
            host.add_api_route(f"{TRAP}/{route.name}{params}", _namesake, name=route.name)

    if order == "before":
        namesakes()
    host.mount(MOUNT, dashboard)
    if order == "after":
        namesakes()
    return host, names


@pytest.mark.parametrize("order", ["before", "after"])
async def test_a_host_route_sharing_a_name_never_takes_a_matador_link(seeded, tmp_path, order):
    """matador resolves its links by route name. A host app is free to name its own
    routes anything, including `static`, `stream` or `retry`, and to register them
    before or after the mount; none of that may move a link out of the dashboard.
    A hijacked Retry button posts to the host's endpoint, not matador's.
    """
    pages = [
        (f"{MOUNT}/", {}),
        (f"{MOUNT}/queues/{QUEUE}?state=failed", {}),  # per-job actions render here
        (f"{MOUNT}/queues/{QUEUE}/jobs?state=failed", hx()),
        (f"{MOUNT}/queues/{QUEUE}/jobs/{seeded['failed']}", {}),  # back link built in Python
        (f"{MOUNT}/sidebar", hx()),
    ]
    host, names = _with_namesakes(order, tmp_path)
    # Without namesakes the test proves nothing, so a table walk that finds no
    # routes (a FastAPI release moving them again) must fail here, not pass.
    assert {"static", "stream", "retry", "remove", "job_page"} <= names, names
    transport = ASGITransport(app=host)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        escaped = []
        for path, headers in pages:
            r = await c.get(path, headers=headers)
            assert r.status_code == 200, path
            escaped += [
                f"{path} -> {url}"
                for url in _URL_ATTRS.findall(r.text)
                if url.startswith("/") and not url.startswith(f"{MOUNT}/")
            ]
    assert escaped == [], "links that left the mount:\n" + "\n".join(escaped)


# The job page's back button: the anchor directly before the job's title.
_BACK = re.compile(r'<a href="([^"]*)"[^>]*>(?:(?!</a>).)*</a>\s*<h1', re.DOTALL)


@pytest.mark.parametrize(
    ("came_from", "back"),
    [
        # the view the reader came from, tab and page included
        (
            f"{MOUNT}/queues/{QUEUE}?state=failed&page=2",
            f"{MOUNT}/queues/{QUEUE}?state=failed&page=2",
        ),
        # the job's own page: back to the queue, never a link to itself
        (f"{MOUNT}/queues/{QUEUE}/jobs/{{job}}", f"{MOUNT}/queues/{QUEUE}"),
        # a host page outside the mount: not matador's to send the reader back to
        (f"/queues/{QUEUE}?state=failed", f"{MOUNT}/queues/{QUEUE}"),
    ],
    ids=["referring-view", "own-page", "outside-the-mount"],
)
async def test_a_mounted_job_page_links_back_to_the_view_it_was_opened_from(
    seeded, came_from, back
):
    job = seeded["failed"]
    transport = ASGITransport(app=_host())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"{MOUNT}/queues/{QUEUE}/jobs/{job}",
            headers=hx(**{"HX-Current-URL": "http://test" + came_from.format(job=job)}),
        )
    assert r.status_code == 200
    found = _BACK.search(r.text)
    assert found, "no back link before the job title"
    assert html.unescape(found.group(1)) == back


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
