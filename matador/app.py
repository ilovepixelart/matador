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
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, FastAPI, Form, Request, params
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pygments import highlight

# Pygments exports formatters/lexers dynamically, so a static checker can't see them.
from pygments.formatters import HtmlFormatter  # ty: ignore[unresolved-import]
from pygments.lexers import JsonLexer  # ty: ignore[unresolved-import]
from redis.asyncio import Redis
from starlette.responses import Response

from .service import STATES, JobState, Service, UnknownQueueError

_JSON_LEXER = JsonLexer()
_JSON_FMT = HtmlFormatter(nowrap=True)  # token <span>s only; we wrap + style ourselves
_MAX_JSON_CHARS = 20_000  # job data is user-controlled + unbounded; cap what we lex


def _pretty_json(obj: object) -> Markup:
    """Server-side pretty-print + syntax-highlight (Pygments) — no client JS."""
    text = json.dumps(obj, indent=2, default=str)
    if len(text) > _MAX_JSON_CHARS:
        # lexing a multi-MB payload on every render is a DoS vector — truncate first
        text = text[:_MAX_JSON_CHARS] + f"\n… truncated ({len(text):,} chars total)"
    # Pygments emits escaped, safe HTML, so wrapping it in Markup is intentional.
    rendered = highlight(text, _JSON_LEXER, _JSON_FMT)
    return Markup(rendered)  # noqa: S704


def _coerce_state(state: str) -> JobState:
    """Clamp an arbitrary query-string state to a valid one (bad input → active tab)."""
    return cast("JobState", state) if state in STATES else "active"


def _default_state(counts: dict[str, int]) -> JobState:
    """Pick the tab with the most signal when the URL doesn't say: running work
    if any, else problems, else what's queued — never an empty `active` list on
    a healthy idle queue.
    """
    for s in ("active", "failed", "wait", "delayed"):
        if counts.get(s):
            return cast("JobState", s)
    return "completed"


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
# Drop the newline after a block tag and leading whitespace before one, so the
# rendered HTML stays clean without manual {%- -%} trims sprinkled everywhere.
_TEMPLATES.env.trim_blocks = True
_TEMPLATES.env.lstrip_blocks = True
PER_PAGE = 20
WORKERS_SEL = "__workers__"  # sidebar highlight sentinel for the Workers view
SCAN_LIMIT = 500  # how many recent jobs a text search scans within a state
MAX_BULK_REMOVE = 1000  # cap a single bulk-remove so one request can't fan out unboundedly

# OOB sidebar refresh fragment — re-rendered alongside a panel so the active-queue
# highlight + badges update in the same response.
_SIDEBAR_OOB = "partials/sidebar_oob.html"


def _schedule_label(s: dict[str, Any]) -> str:
    if s.get("cron"):
        return f"cron {s['cron']}"
    every = s.get("every") or 0
    return f"every {every / 1000:g}s" if every < 60000 else f"every {every / 60000:g}m"


_TEMPLATES.env.filters["clock"] = lambda ms: (
    # local time on purpose — the dashboard shows timestamps in the viewer's zone
    datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S") if ms else "—"  # noqa: DTZ006
)
_TEMPLATES.env.filters["clockms"] = (
    lambda ms: (  # millisecond precision (job timings)
        datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S.%f")[:-3] if ms else "—"  # noqa: DTZ006
    )
)


def _span(secs: int) -> str:
    """ONE duration phrasing for every relative filter (uptime, ago, due) —
    keeping the unit thresholds in a single place so they can't drift apart.
    """
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        hours, mins = divmod(secs // 60, 60)
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{secs // 86400}d"


# NB: these treat a 0/None timestamp as missing ("—"). A literal epoch-0 value
# would be swallowed, but toro always stamps now-milliseconds — accepted.
def _uptime(started_ms: int) -> str:
    if not started_ms:
        return "—"
    return _span(max(0, int(time.time() - started_ms / 1000)))


def _ago(ms: int | None) -> str:
    # Relative past for job rows — same duration phrasing as the workers view.
    return f"{_uptime(ms)} ago" if ms else "—"


def _due(due_ms: int | None) -> str:
    # When a delayed job will run: "due in 3m", or "due now" once promotable.
    if not due_ms:
        return "—"
    secs = int(due_ms / 1000 - time.time())
    return "due now" if secs <= 0 else f"due in {_span(secs)}"


def _at(ms: int | None) -> str:
    # The absolute moment, for hover titles — relative times age, this doesn't.
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%d %b %Y %H:%M:%S")  # noqa: DTZ006


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


def _dur(ms: int | None) -> str:
    # Humanize a millisecond duration (e.g., job started→finished).
    if not ms or ms < 0:
        return "—"
    ms = int(ms)
    if ms < 1000:
        return f"{ms}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins, secs = divmod(int(secs), 60)
    return f"{mins}m {secs}s"


_TEMPLATES.env.filters["schedule"] = _schedule_label
_TEMPLATES.env.filters["comma"] = lambda n: f"{n:,}"
_TEMPLATES.env.filters["compact"] = _compact
_TEMPLATES.env.filters["dur"] = _dur
_TEMPLATES.env.filters["pretty"] = _pretty_json
_TEMPLATES.env.filters["uptime"] = _uptime
_TEMPLATES.env.filters["ago"] = _ago
_TEMPLATES.env.filters["due"] = _due
_TEMPLATES.env.filters["at"] = _at


def _asset_version() -> int:
    # Cache-bust CSS and JS by the newest mtime under static/, so a redeploy (or
    # a dev rebuild) is always picked up — browsers otherwise serve stale assets.
    # Evaluated per full-page render (a handful of stats), NOT at import: a
    # long-running server with assets rebuilt underneath it must not keep
    # handing out the old version forever.
    try:
        static = _HERE / "static"
        return int(max(p.stat().st_mtime for p in static.rglob("*") if p.is_file()))
    except (OSError, ValueError):
        return 0


_TEMPLATES.env.globals["asset_v"] = _asset_version  # ty: ignore[invalid-assignment]


# ---- render helpers (stateless; render through the module-level _TEMPLATES) ----


def _render(request: Request, template: str, **ctx) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(request, template, ctx)


def _toast(request: Request, title: str, message: str, status: int = 404) -> HTMLResponse:
    # A 4xx/5xx body that response-targets routes into #toast (hx-target-error),
    # so a failed action surfaces instead of silently doing nothing.
    return _TEMPLATES.TemplateResponse(
        request, "partials/toast.html", {"title": title, "message": message}, status_code=status
    )


def _full_page(request: Request, **ctx) -> HTMLResponse:
    return _render(request, "pages/index.html", **ctx)


def _render_str(request: Request, template: str, **ctx) -> str:
    # `request` is passed so templates can use `url_for` (root_path-aware, which
    # is what makes the dashboard work mounted at any sub-path).
    return _TEMPLATES.get_template(template).render(request=request, **ctx)


def _selected_from_url(url: str) -> str | None:
    # Which sidebar entry the browser is on, parsed from the client-controlled
    # HX-Current-URL header.
    if re.search(r"/workers(/|\?|#|$)", url):
        return WORKERS_SEL
    # The LAST `/queues/<name>` wins, which keeps a sub-path mount correct. Done
    # with rfind + slicing rather than a greedy `.*` regex, which is O(n^2) on a
    # long no-match input (ReDoS on the client-controlled HX-Current-URL header).
    marker = "/queues/"
    idx = url.rfind(marker)
    if idx == -1:
        return None
    seg = url[idx + len(marker) :]
    ends = [p for p in (seg.find(c) for c in "/?#") if p != -1]
    name = seg[: min(ends)] if ends else seg
    return unquote(name) if name else None


# ---- data helpers (need the Service) ----


async def _search_jobs(
    svc: Service, name: str, state: JobState, query: str
) -> tuple[list[dict[str, Any]], bool]:
    # Exact id lookup is O(1) and cross-state (finds a job wherever it now is);
    # the bounded substring scan covers name/data within `state`. Returns the
    # merged hits plus whether the query was an exact id hit (drives the badge).
    exact = await svc.job(name, query)
    matches = await svc.search(name, state, query, SCAN_LIMIT)
    seen = {exact["id"]} if exact else set()
    jobs = ([exact] if exact else []) + [m for m in matches if m["id"] not in seen]
    return jobs, exact is not None


async def _panel_ctx(
    svc: Service, name: str, state: str, page: int, query: str = ""
) -> dict[str, Any]:
    view = await svc.queue_view(name)
    # No state in the URL → the tab with signal; an explicit one is respected.
    state = _coerce_state(state) if state else _default_state(view["counts"])
    query = query.strip()
    base = {"q": view, "states": STATES, "state": state, "scan_limit": SCAN_LIMIT}
    if query:  # a deep-link or a typed search → render the results, not the list
        jobs, exact = await _search_jobs(svc, name, state, query)
        return {
            **base,
            "jobs": jobs,
            "query": query,
            "exact": exact,
            "page": 1,
            "pages": 1,
            "total": len(jobs),
            "nav": [],
        }
    total = view["counts"].get(state, 0)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, pages))
    jobs = await svc.jobs(name, state, page, PER_PAGE)
    return {
        **base,
        "jobs": jobs,
        "query": "",
        "page": page,
        "pages": pages,
        "total": total,
        "nav": _page_window(page, pages),
    }


def _with_announcement(request: Request, panel: HTMLResponse, message: str) -> HTMLResponse:
    # Append an OOB swap INTO the pre-declared #announce live region, so the
    # outcome of a bulk action is announced (politely) by assistive tech.
    oob = _render_str(request, "partials/announce_oob.html", message=message)
    return HTMLResponse(bytes(panel.body) + oob.encode())


async def _panel_with_sidebar(
    svc: Service, request: Request, name: str, ctx: dict[str, Any]
) -> HTMLResponse:
    # Panel + an out-of-band sidebar refresh, so the active-queue highlight
    # updates in the SAME response (no lag, no second request).
    panel = _render_str(request, "partials/queue.html", **ctx)
    side = _render_str(request, _SIDEBAR_OOB, queues=await svc.overview(), selected=name)
    return HTMLResponse(panel + side)


async def _panel(svc: Service, request: Request, name: str, state: str, page: int) -> HTMLResponse:
    return _render(request, "partials/queue.html", **await _panel_ctx(svc, name, state, page))


# ---- cross-cutting middleware + error handling (registered onto the app) ----


async def _same_origin(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # CSRF defense: on unsafe methods, a present Origin must match our host.
    # Absent Origin (non-browser clients) is allowed through.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return PlainTextResponse("cross-origin request blocked", status_code=403)
    return await call_next(request)


async def _security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Secure-by-default hardening on every matador response (only this mounted
    # sub-app's routes). `setdefault` so a host can override. No CSP here — the
    # inline theme script + htmx `js:` eval would need nonces; left to the host.
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")  # clickjacking
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


class _RevalidatedStatic(StaticFiles):
    """StaticFiles with ``Cache-Control: no-cache``: browsers revalidate every
    asset with ETag/If-Modified-Since (cheap 304s) instead of heuristically
    caching it. The entry points carry an ``?v=`` buster, but their module
    imports (``behaviors/*.js``) don't — without this header a warm browser
    pins an old behavior module until its heuristic expiry.
    """

    # typing.override needs 3.12; the floor is 3.10 (and no typing_extensions dep).
    def file_response(self, *args: Any, **kwargs: Any) -> Response:  # ty: ignore[missing-override-decorator]
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _unknown_queue(request: Request, exc: UnknownQueueError) -> Response:
    return _TEMPLATES.TemplateResponse(
        request,
        "pages/error.html",
        {"title": "Queue not found", "message": f"There is no queue named '{exc.args[0]}'."},
        status_code=404,
    )


# ---- route groups ------------------------------------------------------------


def _views_router(svc: Service, *, show_stacktraces: bool) -> APIRouter:  # noqa: C901 — wires N read routes
    """Build the read routes: a full page on direct navigation, a fragment for swaps."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        queues = await svc.overview()
        if not queues:
            return _full_page(request, queues=[], selected=None, q=None)
        name = queues[0]["name"]
        ctx = await _panel_ctx(svc, name, "", 1)  # signal-based default tab
        return _full_page(request, queues=queues, selected=name, **ctx)

    @router.get("/redis", response_class=HTMLResponse)
    async def redis_bar(request: Request):
        return _render(request, "partials/redis.html", s=await svc.redis_stats())

    @router.get("/stream")
    async def stream(request: Request):
        return StreamingResponse(
            svc.event_stream(request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/queues/{name}", response_class=HTMLResponse)
    async def queue_view(
        request: Request, name: str, state: str = "", page: int = 1, query: str = ""
    ):
        ctx = await _panel_ctx(svc, name, state, page, query)
        if wants_fragment(request):
            return await _panel_with_sidebar(svc, request, name, ctx)
        # Full page: direct nav, reload, or history restore of a pushed URL.
        return _full_page(request, queues=await svc.overview(), selected=name, **ctx)

    @router.get("/workers", response_class=HTMLResponse)
    async def workers_view(request: Request):
        workers = await svc.workers()
        departed = await svc.departed_workers()
        multi = len(svc.queues) > 1
        if wants_fragment(request):
            panel = _render_str(
                request, "partials/workers.html", workers=workers, departed=departed, multi=multi
            )
            side = _render_str(
                request, _SIDEBAR_OOB, queues=await svc.overview(), selected=WORKERS_SEL
            )
            return HTMLResponse(panel + side)
        return _full_page(
            request,
            queues=await svc.overview(),
            selected=WORKERS_SEL,
            q=None,
            workers=workers,
            departed=departed,
            multi=multi,
        )

    @router.get("/workers/list", response_class=HTMLResponse)
    async def workers_fragment(request: Request):
        return _render(
            request,
            "partials/workers_list.html",
            workers=await svc.workers(),
            departed=await svc.departed_workers(),
            multi=len(svc.queues) > 1,
        )

    @router.get("/sidebar", response_class=HTMLResponse)
    async def sidebar(request: Request):
        selected = _selected_from_url(request.headers.get("hx-current-url", ""))
        overview = await svc.overview()
        # Only the sidebar queue-list here. The panel state-tab counts are emitted by the
        # #jobs live-refresh (jobs_fragment) from the same snapshot as the table, so the
        # badges and the list stay in lockstep on fast-churning states.
        return HTMLResponse(
            _render_str(request, "partials/sidebar.html", queues=overview, selected=selected)
        )

    @router.get("/queues/{name}/jobs", response_class=HTMLResponse)
    async def jobs_fragment(
        request: Request, name: str, state: str = "active", page: int = 1, query: str = ""
    ):
        query = query.strip()
        if query:
            # Exact id lookup is O(1) and works for auto AND custom string ids;
            # the bounded substring scan covers name/data within the state.
            jobs, exact = await _search_jobs(svc, name, _coerce_state(state), query)
            return _render(
                request,
                "partials/search_results.html",
                name=name,
                state=state,
                jobs=jobs,
                query=query,
                exact=exact,
                scan_limit=SCAN_LIMIT,
            )
        # No query → the normal paginated list (this is the live-refresh path). Emit the
        # tab counts from the SAME snapshot as the table so the badges and the list can't
        # disagree on a fast-churning state — this refresh is their single source.
        ctx = await _panel_ctx(svc, name, state, page)
        html = _render_str(request, "partials/jobs.html", name=name, **ctx)
        html += _render_str(
            request, "partials/tab_counts_oob.html", states=STATES, counts=ctx["q"]["counts"]
        )
        return HTMLResponse(html)

    @router.get("/queues/{name}/jobs/{job_id}/detail", response_class=HTMLResponse)
    async def job_detail(request: Request, name: str, job_id: str):
        # The accordion body (lazy-loaded when a job row is expanded).
        return _render(
            request,
            "partials/job_detail.html",
            name=name,
            job=await svc.job(name, job_id),
            show_stacktraces=show_stacktraces,
        )

    @router.get("/queues/{name}/jobs/{job_id}", response_class=HTMLResponse)
    async def job_page(request: Request, name: str, job_id: str):
        # A standalone, bookmarkable page for one job — the drill-down target for
        # job-id chips. Shows "no longer here" cleanly if the job is already gone.
        job = await svc.job(name, job_id)
        if wants_fragment(request):
            panel = _render_str(
                request,
                "partials/job_page.html",
                name=name,
                job=job,
                job_id=job_id,
                show_stacktraces=show_stacktraces,
            )
            side = _render_str(request, _SIDEBAR_OOB, queues=await svc.overview(), selected=name)
            return HTMLResponse(panel + side)
        return _full_page(
            request,
            queues=await svc.overview(),
            selected=name,
            q=None,
            name=name,
            job=job,
            job_id=job_id,
            job_page=True,
            show_stacktraces=show_stacktraces,
        )

    return router


def _actions_router(svc: Service) -> APIRouter:  # noqa: C901 — wires N write routes
    """Build the write routes: each mutates state, then re-renders the panel."""
    router = APIRouter()

    @router.post("/workers/departed/clear", response_class=HTMLResponse)
    async def clear_departed(request: Request):
        # Dismiss the stopped/lost-worker history; live workers re-appear via heartbeats.
        await svc.clear_departed()
        return _render(
            request,
            "partials/workers_list.html",
            workers=await svc.workers(),
            departed=await svc.departed_workers(),
            multi=len(svc.queues) > 1,
        )

    @router.post("/queues/{name}/pause", response_class=HTMLResponse)
    async def pause(request: Request, name: str, state: str = "active", page: int = 1):
        await svc.pause(name)
        # Re-render the panel AND OOB-refresh the sidebar so its `paused` badge
        # updates at once (pausing emits no job event, so SSE wouldn't catch it).
        ctx = await _panel_ctx(svc, name, state, page)
        return await _panel_with_sidebar(svc, request, name, ctx)

    @router.post("/queues/{name}/resume", response_class=HTMLResponse)
    async def resume(request: Request, name: str, state: str = "active", page: int = 1):
        await svc.resume(name)
        ctx = await _panel_ctx(svc, name, state, page)
        return await _panel_with_sidebar(svc, request, name, ctx)

    @router.post("/queues/{name}/jobs/{job_id}/retry", response_class=HTMLResponse)
    async def retry(request: Request, name: str, job_id: str, state: str = "failed", page: int = 1):
        if not await svc.retry(name, job_id):
            return _toast(request, "Couldn't retry", f"Job #{job_id} is no longer here.")
        return await _panel(svc, request, name, state, page)

    @router.delete("/queues/{name}/jobs/{job_id}", response_class=HTMLResponse)
    async def remove(
        request: Request, name: str, job_id: str, state: str = "active", page: int = 1
    ):
        if not await svc.remove(name, job_id):
            return _toast(request, "Couldn't remove", f"Job #{job_id} is no longer here.")
        return await _panel(svc, request, name, state, page)

    @router.post("/queues/{name}/jobs/{job_id}/promote", response_class=HTMLResponse)
    async def promote(request: Request, name: str, job_id: str, page: int = 1):
        if not await svc.promote(name, job_id):
            return _toast(request, "Couldn't promote", f"Job #{job_id} is no longer here.")
        return await _panel(svc, request, name, "delayed", page)

    @router.post("/queues/{name}/jobs/bulk-remove", response_class=HTMLResponse)
    async def bulk_remove(
        request: Request,
        name: str,
        state: str = "active",
        page: int = 1,
        ids: Annotated[str, Form()] = "",
    ):
        # `ids` is a comma-joined set submitted by the client (persists across pages).
        # Stripped defensively: a hand-crafted " id" must not silently no-op.
        selected = [i.strip() for i in ids.split(",") if i.strip()]
        if len(selected) > MAX_BULK_REMOVE:
            return _toast(
                request,
                "Too many selected",
                f"Remove at most {MAX_BULK_REMOVE:,} jobs at once.",
                status=413,
            )
        removed = await svc.remove_many(name, selected)
        panel = await _panel(svc, request, name, state, page)
        return _with_announcement(request, panel, f"{removed} jobs removed")

    @router.post("/queues/{name}/retry-all", response_class=HTMLResponse)
    async def retry_all(request: Request, name: str):
        count = await svc.retry_all(name)
        panel = await _panel(svc, request, name, "failed", 1)
        return _with_announcement(request, panel, f"{count} jobs queued for retry")

    @router.post("/queues/{name}/clean", response_class=HTMLResponse)
    async def clean(request: Request, name: str, state: str = "completed"):
        cleaned = _coerce_state(state)  # announce what was ACTUALLY cleaned
        count = await svc.clean(name, cleaned)
        panel = await _panel(svc, request, name, cleaned, 1)
        return _with_announcement(request, panel, f"{count} {cleaned} jobs removed")

    @router.post("/queues/{name}/schedulers/{scheduler_id}/trigger", response_class=HTMLResponse)
    async def trigger(request: Request, name: str, scheduler_id: str):
        await svc.trigger_scheduler(name, scheduler_id)
        return _render(
            request, "partials/schedulers.html", name=name, schedulers=await svc.schedulers(name)
        )

    @router.delete("/queues/{name}/schedulers/{scheduler_id}", response_class=HTMLResponse)
    async def remove_scheduler(request: Request, name: str, scheduler_id: str):
        await svc.remove_scheduler(name, scheduler_id)
        return _render(
            request, "partials/schedulers.html", name=name, schedulers=await svc.schedulers(name)
        )

    return router


def create_app(  # noqa: PLR0913 — keyword-only knobs are the public configuration surface
    names: list[str],
    *,
    url: str = "redis://localhost:6379",
    prefix: str = "toro",
    connection: Redis | None = None,
    dependencies: Sequence[params.Depends] | None = None,
    require_same_origin: bool = False,
    show_stacktraces: bool = True,
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

    Set `require_same_origin=True` to reject state-changing requests (POST/DELETE…)
    whose `Origin` doesn't match the request host — a stateless CSRF defense. The
    dashboard ships no auth, so CSRF is moot by default; enable this when you add
    *cookie*-based auth in front (a cross-site form would otherwise carry the cookie).
    Behind a reverse proxy, ensure the forwarded Host is correct (uvicorn
    `--proxy-headers`) so same-origin requests aren't falsely blocked.

    Set `show_stacktraces=False` to omit job stack traces from the UI — they can
    leak source paths, versions, and occasionally secrets from exception messages,
    which matters when the dashboard is reachable by people who shouldn't see them.
    """
    svc = Service(names, url=url, prefix=prefix, connection=connection)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await svc.close()

    app = FastAPI(title="matador", lifespan=lifespan, dependencies=list(dependencies or []))
    app.mount("/static", _RevalidatedStatic(directory=str(_HERE / "static")), name="static")

    # Middleware runs outermost-last-registered, so security_headers wraps same_origin:
    # a blocked cross-origin response still carries the hardening headers.
    if require_same_origin:
        app.middleware("http")(_same_origin)
    app.middleware("http")(_security_headers)
    app.exception_handler(UnknownQueueError)(_unknown_queue)

    app.include_router(_views_router(svc, show_stacktraces=show_stacktraces))
    app.include_router(_actions_router(svc))
    return app
