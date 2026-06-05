"""matador — a modern, server-rendered dashboard for toro queues.

HTMX architecture (HATEOAS, URL-driven):
  * The URL is the state: `/queues/<name>?state=<tab>&page=<n>`. Every queue/tab/
    page navigation is an `hx-get` to that URL with `hx-push-url`, so reload,
    back/forward and bookmarks all work, and there's no client-side state to sync.
  * One route serves both: a full page on direct navigation / history-restore, or
    just the panel fragment for an HTMX swap (decided by the `HX-Request` header).
  * The server renders the active tab and active queue (no JS class-toggling).
    The sidebar reads `HX-Current-URL` to know which queue to highlight.

    from matador import create_app
    app = create_app(["emails", "billing"], url="redis://localhost:6379")
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import unquote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pygments import highlight

# Pygments exports formatters/lexers dynamically, so a static checker can't see them.
from pygments.formatters import HtmlFormatter  # ty: ignore[unresolved-import]
from pygments.lexers import JsonLexer  # ty: ignore[unresolved-import]

from .service import STATES, Service, UnknownQueueError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import params
    from redis.asyncio import Redis

_JSON_LEXER = JsonLexer()
_JSON_FMT = HtmlFormatter(nowrap=True)  # token <span>s only; we wrap + style ourselves


def _pretty_json(obj: object) -> Markup:
    """Server-side pretty-print + syntax-highlight (Pygments) — no client JS."""
    # Pygments emits escaped, safe HTML, so wrapping it in Markup is intentional.
    rendered = highlight(json.dumps(obj, indent=2, default=str), _JSON_LEXER, _JSON_FMT)
    return Markup(rendered)  # noqa: S704


def _page_window(page: int, pages: int, span: int = 2) -> list[int | None]:
    """Page numbers to show: first, last, and `span` either side of current,
    with None marking an ellipsis gap. e.g. [1, None, 4, 5, 6, None, 20].
    """
    if pages <= 7:
        return list(range(1, pages + 1))
    nums = {1, pages, page}
    for d in range(1, span + 1):
        nums.add(max(1, page - d))
        nums.add(min(pages, page + d))
    out: list[int | None] = []
    prev = 0
    for p in sorted(nums):
        if p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


def wants_fragment(request: Request) -> bool:
    """Return True for an HTMX swap that should get a fragment — but False for a
    history-restore, which re-requests the pushed URL and needs the whole page.
    """
    return (
        request.headers.get("hx-request") == "true"
        and request.headers.get("hx-history-restore-request") != "true"
    )


_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))
PER_PAGE = 20
WORKERS_SEL = "__workers__"  # sidebar highlight sentinel for the Workers view
SCAN_LIMIT = 500  # how many recent jobs a text search scans within a state


def _schedule_label(s: dict) -> str:
    if s.get("cron"):
        return f"cron {s['cron']}"
    every = s.get("every") or 0
    return f"every {every / 1000:g}s" if every < 60000 else f"every {every / 60000:g}m"


_TEMPLATES.env.filters["clock"] = lambda ms: (
    # local time on purpose — the dashboard shows timestamps in the viewer's zone
    datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S") if ms else "—"  # noqa: DTZ006
)
def _uptime(started_ms: int) -> str:
    if not started_ms:
        return "—"
    secs = max(0, int(time.time() - started_ms / 1000))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    hours, mins = divmod(secs // 60, 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _compact(n: int) -> str:
    """Abbreviate big counts following the CLDR / ECMA-402 `compact` convention for
    en (K/M/B/T, ~2 significant digits) so fixed-width number boxes can't overflow.
    Kept exact up to 9999 — a deliberate dashboard choice (operators want the precise
    figure where it still fits); the exact value is exposed via the badge `title`.
    """
    n = int(n)
    if n < 10_000:
        return str(n)
    for div, suffix in (
        (1_000_000_000_000, "T"),
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ):
        if n >= div:
            value = n / div
            shown = f"{value:.1f}".rstrip("0").rstrip(".") if value < 10 else str(round(value))
            return f"{shown}{suffix}"
    return str(n)  # unreachable (n >= 10_000 always hits the K branch)


_TEMPLATES.env.filters["schedule"] = _schedule_label
_TEMPLATES.env.filters["comma"] = lambda n: f"{n:,}"
_TEMPLATES.env.filters["compact"] = _compact
_TEMPLATES.env.filters["pretty"] = _pretty_json
_TEMPLATES.env.filters["uptime"] = _uptime


def _asset_version() -> int:
    # Cache-bust the stylesheet by its build mtime so a rebuilt app.css is always
    # picked up (browsers otherwise serve the cached one).
    try:
        return int((_HERE / "static" / "app.css").stat().st_mtime)
    except OSError:
        return 0


_TEMPLATES.env.globals["css_v"] = _asset_version()  # ty: ignore[invalid-assignment]


def create_app(  # noqa: C901, PLR0915  — an app factory that wires every route is inherently long
    names: list[str],
    *,
    url: str = "redis://localhost:6379",
    prefix: str = "toro",
    connection: Redis | None = None,
    dependencies: Sequence[params.Depends] | None = None,
) -> FastAPI:
    """Build the matador FastAPI app watching the given queue `names`.

    Pass `connection` (a ``redis.asyncio.Redis``) to share the host app's pool
    instead of opening a new one from `url`; matador never closes a connection it
    didn't create. (Note: a mounted sub-app's lifespan doesn't run, so sharing the
    host's connection is the right way to embed — the host owns the lifecycle.)

    Pass `dependencies` (e.g. ``[Depends(require_admin)]``) to protect a mounted
    dashboard with the host app's own auth — they run before every route. (The
    ``/static`` mount is a sub-app and isn't covered; wrap the whole mount if the
    assets themselves need protecting.)
    """
    svc = Service(names, url=url, prefix=prefix, connection=connection)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await svc.close()

    app = FastAPI(title="matador", lifespan=lifespan, dependencies=list(dependencies or []))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.exception_handler(UnknownQueueError)
    async def _unknown_queue(request: Request, exc: UnknownQueueError):
        return _TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {"title": "Queue not found", "message": f"There is no queue named '{exc.args[0]}'."},
            status_code=404,
        )

    def render(request: Request, template: str, **ctx) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(request, template, ctx)

    def toast(request: Request, title: str, message: str, status: int = 404) -> HTMLResponse:
        # A 4xx/5xx body that response-targets routes into #toast (hx-target-error),
        # so a failed action surfaces instead of silently doing nothing.
        return _TEMPLATES.TemplateResponse(
            request, "toast.html", {"title": title, "message": message}, status_code=status
        )

    def full_page(request: Request, **ctx) -> HTMLResponse:
        return render(request, "index.html", **ctx)

    def render_str(request: Request, template: str, **ctx) -> str:
        # `request` is passed so templates can use `url_for` (root_path-aware, which
        # is what makes the dashboard work mounted at any sub-path).
        return _TEMPLATES.get_template(template).render(request=request, **ctx)

    async def panel_with_sidebar(request: Request, name: str, ctx: dict) -> HTMLResponse:
        # Panel + an out-of-band sidebar refresh, so the active-queue highlight
        # updates in the SAME response (no lag, no second request).
        panel = render_str(request, "queue.html", **ctx)
        side = render_str(request, "sidebar_oob.html", queues=await svc.overview(), selected=name)
        return HTMLResponse(panel + side)

    async def panel_ctx(name: str, state: str, page: int) -> dict:
        if state not in STATES:
            state = "active"
        view = await svc.queue_view(name)
        total = view["counts"].get(state, 0)
        pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = max(1, min(page, pages))
        jobs = await svc.jobs(name, state, page, PER_PAGE)
        return {
            "q": view,
            "states": STATES,
            "state": state,
            "jobs": jobs,
            "page": page,
            "pages": pages,
            "total": total,
            "nav": _page_window(page, pages),
        }

    # ---- pages & fragments (reads) ----------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        queues = await svc.overview()
        if not queues:
            return full_page(request, queues=[], selected=None, q=None)
        name = queues[0]["name"]
        ctx = await panel_ctx(name, "active", 1)
        return full_page(request, queues=queues, selected=name, **ctx)

    @app.get("/redis", response_class=HTMLResponse)
    async def redis_bar(request: Request):
        return render(request, "redis.html", s=await svc.redis_stats())

    @app.get("/stream")
    async def stream():
        return StreamingResponse(
            svc.event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/queues/{name}", response_class=HTMLResponse)
    async def queue_view(request: Request, name: str, state: str = "active", page: int = 1):
        ctx = await panel_ctx(name, state, page)
        if wants_fragment(request):
            return await panel_with_sidebar(request, name, ctx)
        # Full page: direct nav, reload, or history restore of a pushed URL.
        return full_page(request, queues=await svc.overview(), selected=name, **ctx)

    @app.get("/workers", response_class=HTMLResponse)
    async def workers_view(request: Request):
        workers = await svc.workers()
        departed = await svc.departed_workers()
        multi = len(svc.queues) > 1
        if wants_fragment(request):
            panel = render_str(
                request, "workers.html", workers=workers, departed=departed, multi=multi
            )
            side = render_str(
                request, "sidebar_oob.html", queues=await svc.overview(), selected=WORKERS_SEL
            )
            return HTMLResponse(panel + side)
        return full_page(
            request, queues=await svc.overview(), selected=WORKERS_SEL, q=None,
            workers=workers, departed=departed, multi=multi,
        )

    @app.get("/workers/list", response_class=HTMLResponse)
    async def workers_fragment(request: Request):
        return render(
            request, "workers_list.html",
            workers=await svc.workers(), departed=await svc.departed_workers(),
            multi=len(svc.queues) > 1,
        )

    @app.get("/sidebar", response_class=HTMLResponse)
    async def sidebar(request: Request):
        # Highlight whatever the browser is currently on. Greedy `.*` so the LAST
        # /queues/<name> wins — correct even mounted at a sub-path like /admin/queues
        # (where the URL is …/admin/queues/queues/<name>).
        url = request.headers.get("hx-current-url", "")
        if re.search(r"/workers(/|\?|#|$)", url):
            selected = WORKERS_SEL
        elif m := re.search(r".*/queues/([^/?#]+)", url):
            selected = unquote(m.group(1))
        else:
            selected = None
        overview = await svc.overview()
        html = render_str(request, "sidebar.html", queues=overview, selected=selected)
        # Fold the selected queue's tab counts into the SAME response (out-of-band),
        # so the sidebar badges and the state-tab numbers update together in one
        # request — the tabs get no listener of their own (no stagger), and the
        # swap is just a few id'd spans (cheap, no reflow thanks to tabular-nums).
        counts = next((qq["counts"] for qq in overview if qq["name"] == selected), None)
        if counts is not None:
            html += render_str(request, "tab_counts_oob.html", states=STATES, counts=counts)
        return HTMLResponse(html)

    @app.get("/queues/{name}/jobs", response_class=HTMLResponse)
    async def jobs_fragment(
        request: Request, name: str, state: str = "active", page: int = 1, query: str = ""
    ):
        query = query.strip()
        if query:
            # Exact id lookup is O(1) and works for auto AND custom string ids;
            # the bounded substring scan covers name/data within the state.
            exact = await svc.job(name, query)
            matches = await svc.search(name, state, query, SCAN_LIMIT)
            seen = {exact["id"]} if exact else set()
            jobs = ([exact] if exact else []) + [m for m in matches if m["id"] not in seen]
            return render(
                request,
                "search_results.html",
                name=name,
                state=state,
                jobs=jobs,
                query=query,
                exact=bool(exact),
                scan_limit=SCAN_LIMIT,
            )
        # No query → the normal paginated list (also used by the active-tab refresh).
        ctx = await panel_ctx(name, state, page)
        return render(request, "jobs.html", name=name, **ctx)

    @app.get("/queues/{name}/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(request: Request, name: str, job_id: str):
        return render(request, "job_detail.html", name=name, job=await svc.job(name, job_id))

    # ---- actions (writes) — all re-render the panel for the current view ----

    async def _panel(request: Request, name: str, state: str, page: int) -> HTMLResponse:
        return render(request, "queue.html", **await panel_ctx(name, state, page))

    @app.post("/queues/{name}/pause", response_class=HTMLResponse)
    async def pause(request: Request, name: str, state: str = "active", page: int = 1):
        await svc.pause(name)
        # Re-render the panel AND OOB-refresh the sidebar so its `paused` badge
        # updates at once (pausing emits no job event, so SSE wouldn't catch it).
        return await panel_with_sidebar(request, name, await panel_ctx(name, state, page))

    @app.post("/queues/{name}/resume", response_class=HTMLResponse)
    async def resume(request: Request, name: str, state: str = "active", page: int = 1):
        await svc.resume(name)
        return await panel_with_sidebar(request, name, await panel_ctx(name, state, page))

    @app.post("/queues/{name}/jobs/{job_id}/retry", response_class=HTMLResponse)
    async def retry(request: Request, name: str, job_id: str, state: str = "failed", page: int = 1):
        if not await svc.retry(name, job_id):
            return toast(request, "Couldn't retry", f"Job #{job_id} is no longer here.")
        return await _panel(request, name, state, page)

    @app.delete("/queues/{name}/jobs/{job_id}", response_class=HTMLResponse)
    async def remove(
        request: Request, name: str, job_id: str, state: str = "active", page: int = 1
    ):
        if not await svc.remove(name, job_id):
            return toast(request, "Couldn't remove", f"Job #{job_id} is no longer here.")
        return await _panel(request, name, state, page)

    @app.post("/queues/{name}/jobs/{job_id}/promote", response_class=HTMLResponse)
    async def promote(request: Request, name: str, job_id: str, page: int = 1):
        if not await svc.promote(name, job_id):
            return toast(request, "Couldn't promote", f"Job #{job_id} is no longer here.")
        return await _panel(request, name, "delayed", page)

    @app.post("/queues/{name}/jobs/bulk-remove", response_class=HTMLResponse)
    async def bulk_remove(
        request: Request,
        name: str,
        state: str = "active",
        page: int = 1,
        ids: Annotated[str, Form()] = "",
    ):
        # `ids` is a comma-joined set submitted by the client (persists across pages).
        await svc.remove_many(name, [i for i in ids.split(",") if i])
        return await _panel(request, name, state, page)

    @app.post("/queues/{name}/retry-all", response_class=HTMLResponse)
    async def retry_all(request: Request, name: str):
        await svc.retry_all(name)
        return await _panel(request, name, "failed", 1)

    @app.post("/queues/{name}/clean", response_class=HTMLResponse)
    async def clean(request: Request, name: str, state: str = "completed"):
        await svc.clean(name, state)
        return await _panel(request, name, state, 1)

    @app.post("/queues/{name}/schedulers/{scheduler_id}/trigger", response_class=HTMLResponse)
    async def trigger(request: Request, name: str, scheduler_id: str):
        await svc.trigger_scheduler(name, scheduler_id)
        return render(request, "schedulers.html", name=name, schedulers=await svc.schedulers(name))

    @app.delete("/queues/{name}/schedulers/{scheduler_id}", response_class=HTMLResponse)
    async def remove_scheduler(request: Request, name: str, scheduler_id: str):
        await svc.remove_scheduler(name, scheduler_id)
        return render(request, "schedulers.html", name=name, schedulers=await svc.schedulers(name))

    return app
