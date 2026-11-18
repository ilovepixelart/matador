"""The installed-wheel scenarios: matador run exactly as a user runs it.

Run by tests/wheel/test_wheel.py with the Python of a fresh virtual environment that
holds nothing but an installed matador-dashboard and the dependencies its metadata
names (toro-queue from PyPI among them), started with `-I` from a directory outside
the repository, so `import matador` can only resolve to what was installed. The
dashboard is started through the `matador` command the wheel installs, and every
route is called over real HTTP against a real toro queue on the Redis REDIS_URL names.
It is not a pytest module: the interpreter running it has no pytest.

    python -I scenarios.py --out results.json [--only name ...]

Each scenario returns what it observed, as plain JSON, deterministic across runs (no
generated job ids, no timestamps, no ports): test_wheel.py compares the candidate's
observations against the published release's. A scenario that fails its own
expectations records the error instead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import redis
import toro
import uvicorn
from fastapi import Depends, HTTPException, Request
from toro import FlowChild, Queue, Worker

import matador
from matador import create_app

URL = os.environ.get("REDIS_URL", "")
RUN = uuid.uuid4().hex[:8]
SCENARIO_TIMEOUT = 90.0
HTTP_TIMEOUT = 15.0
QUEUES = ("emails", "reports")
SCENARIOS: dict[str, Callable[[Ctx], Awaitable[dict[str, Any]]]] = {}


def scenario(fn: Callable[[Ctx], Awaitable[dict[str, Any]]]) -> Callable[..., Any]:
    SCENARIOS[fn.__name__] = fn
    return fn


def expect(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def free_port() -> int:
    """A local port with nothing listening on it (until someone binds it)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def with_db(url: str, db: int) -> str:
    """`url` on database `db`: CLIENT LIST reports each client's database, which is how
    the connections one app opened are told from everyone else's."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


def clients_on(db: int) -> int:
    client = redis.Redis.from_url(URL)
    try:
        return sum(1 for c in client.client_list() if int(c.get("db", -1)) == db)
    finally:
        client.close()


# ---- HTTP, from the standard library only ---------------------------------------------


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: str

    @property
    def kind(self) -> str:
        return self.headers.get("content-type", "").split(";")[0]


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Reply:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as res:
            return Reply(
                res.status, {k.lower(): v for k, v in res.headers.items()}, res.read().decode()
            )
    except urllib.error.HTTPError as err:
        return Reply(err.code, {k.lower(): v for k, v in err.headers.items()}, err.read().decode())


async def http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
) -> Reply:
    """One request, off the event loop: the loop keeps serving the proxy and the app."""
    body = None
    hdrs = dict(headers or {})
    if form is not None:
        body = "&".join(f"{k}={quote(v)}" for k, v in form.items()).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    return await asyncio.to_thread(_http, method, url, hdrs, body)


def _first_bytes(url: str, n: int) -> str:
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as res:
        return res.read(n).decode()


async def ready(base: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            if (await http("GET", f"{base}/static/favicon.svg")).status == 200:
                return True
        await asyncio.sleep(0.1)
    return False


# ---- the dashboard, started from the installed entry point ----------------------------


@dataclass
class Served:
    base: str
    exit_code: int | None = None
    log: str = ""

    @property
    def stopped_cleanly(self) -> bool:
        """SIGTERM ran the lifespan shutdown to the end. The exit status is uvicorn's
        to choose: 0, or the signal raised again once shutdown completes (newer
        uvicorn does that, so a supervisor sees why the process ended). A request
        that failed before the signal (Redis down) may have logged a traceback; the
        shutdown itself may not."""
        _, stopping, shutdown = self.log.partition("Shutting down")
        return (
            self.exit_code in (0, -signal.SIGTERM)
            and bool(stopping)
            and "Application shutdown complete" in shutdown
            and "Traceback" not in shutdown
        )


def matador_command() -> Path:
    """The console script the wheel installed beside this interpreter."""
    return Path(sys.executable).parent / "matador"


def _spawn(redis_url: str, prefix: str, queues: str, port: int, log: Any) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(matador_command()),
            "serve",
            "--queues",
            queues,
            "--redis",
            redis_url,
            "--prefix",
            prefix,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )


@contextlib.asynccontextmanager
async def serve(
    redis_url: str, prefix: str, queues: str = ",".join(QUEUES)
) -> AsyncIterator[Served]:
    port = free_port()
    served = Served(f"http://127.0.0.1:{port}")
    with tempfile.TemporaryFile() as log:
        proc = _spawn(redis_url, prefix, queues, port, log)
        try:
            if not await ready(served.base):
                log.seek(0)
                raise AssertionError(f"`matador serve` never answered:\n{log.read().decode()}")
            yield served
        finally:
            # Our own child, never pid 0 or 1: those signal a process group or everyone.
            if proc.pid > 1 and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            try:
                served.exit_code = await asyncio.to_thread(proc.wait, 15)
            except subprocess.TimeoutExpired:
                proc.kill()
                served.exit_code = await asyncio.to_thread(proc.wait)
            log.seek(0)
            served.log = log.read().decode()


# ---- a real toro queue to look at -----------------------------------------------------


@dataclass
class Seeded:
    ids: dict[str, str] = field(default_factory=dict)


class Ctx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.prefix = f"e2e-{RUN}-{name}"
        self.queues: list[Queue] = []

    def queue(self, name: str, url: str = "") -> Queue:
        q = Queue(name, url=url or URL, prefix=self.prefix)
        self.queues.append(q)
        return q

    async def cleanup(self) -> None:
        for q in self.queues:
            with contextlib.suppress(Exception):
                await q.close()
        client = redis.Redis.from_url(URL)
        try:
            keys = client.keys(f"{self.prefix}:*")
            if keys:
                client.delete(*keys)
        finally:
            client.close()


async def until(predicate: Callable[[], Any], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = predicate()
        if asyncio.iscoroutine(res):
            res = await res
        if res:
            return True
        await asyncio.sleep(0.05)
    return False


async def seed(ctx: Ctx) -> Seeded:
    """One job in every state a dashboard draws, a schedule, and a finished flow."""
    emails, reports = ctx.queue("emails"), ctx.queue("reports")
    out = Seeded()

    async def process(j):
        if j.name == "bounce":
            raise RuntimeError("mailbox full")
        if j.name == "fetch":
            return j.data["shard"]
        if j.name == "report":
            return sum((await j.children_results()).values())
        return {"sent": j.data}

    workers = [
        Worker(name, process, url=URL, prefix=ctx.prefix, stalled_interval=0) for name in QUEUES
    ]
    tasks = [asyncio.create_task(w.run()) for w in workers]
    try:
        out.ids["completed"] = (await emails.add("welcome", {"to": "ada@example.com"})).id
        out.ids["failed"] = (await emails.add("bounce", {"to": "nobody@example.com"})).id
        flow = await reports.add_flow(
            "report", {"q": 1}, children=[FlowChild("fetch", {"shard": i}) for i in range(3)]
        )
        out.ids["flow"] = flow.id
        await emails.result(out.ids["completed"], timeout=10)
        with contextlib.suppress(toro.JobFailedError):
            await emails.result(out.ids["failed"], timeout=10)
        await reports.result(flow.id, timeout=10)
    finally:
        for w in workers:
            await w.stop(grace_period=2)
        await asyncio.gather(*tasks, return_exceptions=True)
    out.ids["delayed"] = (await emails.add("reminder", {"in": "1h"}, delay=3_600_000)).id
    out.ids["waiting"] = (await emails.add("digest", {"week": 47})).id
    out.ids["waiting2"] = (await emails.add("digest", {"week": 48})).id
    await emails.add_scheduler("nightly", every=86_400_000, name="rollup")
    return out


# ---- the public surface ---------------------------------------------------------------


def installed() -> tuple[Path, bool, list[str]]:
    """Where `matador` was imported from, whether that is inside this environment, and
    every file under it that is not Python (the templates and static assets)."""
    home = Path(matador.__file__).resolve().parent
    inside = home.is_relative_to(Path(sys.prefix).resolve())
    assets = sorted(
        str(p.relative_to(home))
        for p in home.rglob("*")
        if p.is_file() and p.suffix not in (".py", ".pyc")
    )
    return home, inside, assets


@scenario
async def import_surface(_: Ctx) -> dict[str, Any]:
    home, inside, assets = installed()
    expect(inside, f"matador imported from {home}, not from the environment {sys.prefix}")
    expect("py.typed" in assets, "the wheel does not ship py.typed")
    expect(matador.__version__ == metadata.version("matador-dashboard"), "__version__ disagrees")
    params = inspect.signature(create_app).parameters
    return {
        "all": sorted(matador.__all__),
        "version": matador.__version__,
        "create_app": [
            f"{p.name}:{p.kind.name}:{p.default!r}" if p.default is not p.empty else p.name
            for p in params.values()
        ],
        "assets": assets,
    }


@scenario
async def distribution_metadata(_: Ctx) -> dict[str, Any]:
    dist = metadata.distribution("matador-dashboard")
    meta = dist.metadata
    top = sorted({Path(str(f)).parts[0] for f in dist.files or []})
    return {
        "name": meta["Name"],
        "requires_python": meta["Requires-Python"],
        "requires_dist": sorted(dist.requires or []),
        "classifiers": sorted(meta.get_all("Classifier") or []),
        "entry_points": sorted(f"{e.group}:{e.name}={e.value}" for e in dist.entry_points),
        "top_level": [t for t in top if not t.endswith(".dist-info") and t != ".."],
    }


@scenario
async def toro_it_runs_with_satisfies_the_declared_requirement(_: Ctx) -> dict[str, Any]:
    wanted = next(r for r in metadata.requires("matador-dashboard") or [] if r.startswith("toro"))
    have = tuple(int(x) for x in toro.__version__.split(".")[:3])
    need = tuple(int(x) for x in toro_floor().split("."))
    expect(have >= need, f"toro {toro.__version__} is below the declared {wanted}")
    return {"requirement": wanted}


@scenario
async def cli_usage_and_errors(_: Ctx) -> dict[str, Any]:
    def run(*args: str) -> tuple[int, str]:
        proc = subprocess.run(
            [str(matador_command()), *args], capture_output=True, text=True, check=False, timeout=30
        )
        return proc.returncode, proc.stdout + proc.stderr

    help_code, help_text = await asyncio.to_thread(run, "--help")
    bare_code, bare_text = await asyncio.to_thread(run)
    serve_code, serve_text = await asyncio.to_thread(run, "serve")
    _, serve_help = await asyncio.to_thread(run, "serve", "--help")
    expect(help_code == 0 and "serve" in help_text, "`matador --help` does not offer serve")
    expect(bare_code == 2, f"`matador` with no command exited {bare_code}")
    expect(serve_code == 2 and "--queues" in serve_text, "`matador serve` without --queues ran")
    options = sorted(set(re.findall(r"--[a-z-]+", serve_help)))
    return {
        "help": help_code,
        "bare": [bare_code, "required" in bare_text],
        "serve_without_queues": [serve_code, "--queues" in serve_text],
        "serve_options": options,
    }


# ---- every route, over HTTP, from `matador serve` -------------------------------------

HX = {"HX-Request": "true"}


def summary(reply: Reply, *needles: str) -> dict[str, Any]:
    """The status, the media type, and which of `needles` the body carries."""
    return {"status": reply.status, "type": reply.kind, "has": [n in reply.body for n in needles]}


@scenario
async def read_routes(ctx: Ctx) -> dict[str, Any]:
    seeded = await seed(ctx)
    ids = seeded.ids
    out: dict[str, Any] = {}
    async with serve(URL, ctx.prefix) as app:
        base = app.base
        root = await http("GET", f"{base}/")
        out["/"] = summary(root, "emails", "reports", "<!doctype html>")
        out["/ security headers"] = {
            h: root.headers.get(h)
            for h in ("x-frame-options", "x-content-type-options", "referrer-policy")
        }
        reads = {
            "/redis": ("GET", "/redis", {}, ("redis",)),
            "/queues/emails": ("GET", "/queues/emails", {}, ("welcome", "<!doctype html>")),
            "/queues/emails?state=failed": ("GET", "/queues/emails?state=failed", {}, ("bounce",)),
            "/queues/emails (fragment)": ("GET", "/queues/emails", HX, ("<!doctype html>",)),
            "/queues/emails/metrics": ("GET", "/queues/emails/metrics", {}, ()),
            "/queues/reports/flow-metrics": ("GET", "/queues/reports/flow-metrics", {}, ()),
            "/workers": ("GET", "/workers", {}, ("<!doctype html>",)),
            "/workers (fragment)": ("GET", "/workers", HX, ("<!doctype html>",)),
            "/workers/list": ("GET", "/workers/list", {}, ()),
            "/sidebar": ("GET", "/sidebar", {}, ("emails", "reports")),
            "/queues/emails/jobs?state=completed": (
                "GET",
                "/queues/emails/jobs?state=completed",
                {},
                ("welcome", "ada@example.com"),
            ),
            "/queues/emails/jobs?state=failed&query=bounce": (
                "GET",
                "/queues/emails/jobs?state=failed&query=bounce",
                {},
                ("bounce",),
            ),
            "/queues/emails/jobs?state=delayed": (
                "GET",
                "/queues/emails/jobs?state=delayed",
                {},
                ("reminder",),
            ),
            "/queues/emails/jobs/{failed}/detail": (
                "GET",
                f"/queues/emails/jobs/{ids['failed']}/detail",
                {},
                ("mailbox full", "Traceback"),
            ),
            "/queues/reports/jobs/{flow}/flow": (
                "GET",
                f"/queues/reports/jobs/{ids['flow']}/flow",
                {},
                ("fetch",),
            ),
            "/queues/emails/jobs/{completed}": (
                "GET",
                f"/queues/emails/jobs/{ids['completed']}",
                {},
                ("welcome", "<!doctype html>"),
            ),
            "/queues/emails/jobs/{missing}": ("GET", "/queues/emails/jobs/no-such-job", {}, ()),
            "/queues/nope": ("GET", "/queues/nope", {}, ("Queue not found",)),
            "/queues/nope/jobs": ("GET", "/queues/nope/jobs", {}, ()),
            "/static/app.css": ("GET", "/static/app.css", {}, ()),
            "/static/js/app.js": ("GET", "/static/js/app.js", {}, ()),
            "/docs": ("GET", "/docs", {}, ()),
            "/openapi.json": ("GET", "/openapi.json", {}, ()),
        }
        for label, (method, path, headers, needles) in reads.items():
            reply = await http(method, base + path, headers=headers)
            out[label] = summary(reply, *needles)
        css = await http("GET", f"{base}/static/app.css")
        out["/static cache-control"] = css.headers.get("cache-control")
        metrics = await http("GET", f"{base}/metrics")
        out["/metrics"] = {
            "status": metrics.status,
            "type": metrics.headers.get("content-type"),
            "eof": metrics.body.endswith("# EOF\n"),
            "queues": sorted(set(re.findall(r'queue="([^"]+)"', metrics.body))),
        }
        out["/stream"] = await asyncio.to_thread(_first_bytes, f"{base}/stream", 13)
    failing = {k: v for k, v in out.items() if isinstance(v, dict) and v.get("status", 200) >= 500}
    expect(not failing, f"server errors: {failing}")
    expect(
        out["/"]["status"] == 200 and all(out["/"]["has"]), "the index does not list both queues"
    )
    expect(out["/queues/emails (fragment)"]["has"] == [False], "an HTMX swap got a full page")
    expect(out["/queues/nope"]["status"] == 404, "an unknown queue is not a 404")
    expect(all(out["/queues/emails/jobs/{failed}/detail"]["has"]), "the failure is not shown")
    expect(out["/metrics"]["queues"] == sorted(QUEUES), "the metrics do not cover both queues")
    expect(out["/stream"].startswith("retry: 3000"), f"the stream opened with {out['/stream']!r}")
    expect(app.stopped_cleanly, f"`matador serve` did not stop cleanly:\n{app.log}")
    return out


async def _job_state(q: Queue, job_id: str) -> str | None:
    job = await q.get_job(job_id)
    return None if job is None else job.state


@scenario
async def action_routes(ctx: Ctx) -> dict[str, Any]:  # noqa: PLR0915 - one call per route
    ids = (await seed(ctx)).ids
    emails, reports = ctx.queues[0], ctx.queues[1]
    out: dict[str, Any] = {}
    async with serve(URL, ctx.prefix) as app:
        base = f"{app.base}/queues/emails"

        async def act(label: str, method: str, url: str, **kw: Any) -> Reply:
            reply = await http(method, url, **kw)
            out[label] = reply.status
            return reply

        await act("pause", "POST", f"{base}/pause")
        out["paused"] = await emails.is_paused()
        await act("resume", "POST", f"{base}/resume")
        out["resumed"] = not await emails.is_paused()
        await act("retry", "POST", f"{base}/jobs/{ids['failed']}/retry")
        out["retried state"] = await _job_state(emails, ids["failed"])
        missing = await act("retry missing", "POST", f"{base}/jobs/no-such-job/retry")
        out["retry missing toast"] = "is no longer here" in missing.body
        await act("promote", "POST", f"{base}/jobs/{ids['delayed']}/promote")
        out["promoted state"] = await _job_state(emails, ids["delayed"])
        await act("cancel", "POST", f"{base}/jobs/{ids['waiting']}/cancel")
        out["cancelled state"] = await _job_state(emails, ids["waiting"])
        await act("remove", "DELETE", f"{base}/jobs/{ids['waiting2']}")
        out["removed"] = await emails.get_job(ids["waiting2"]) is None
        await act("remove missing", "DELETE", f"{base}/jobs/no-such-job")
        await act(
            "bulk-remove",
            "POST",
            f"{base}/jobs/bulk-remove",
            form={"ids": json.dumps([ids["completed"]])},
        )
        out["bulk removed"] = await emails.get_job(ids["completed"]) is None
        await act(
            "bulk-remove unreadable", "POST", f"{base}/jobs/bulk-remove", form={"ids": "not json"}
        )
        too_many = json.dumps([f"j{i}" for i in range(1001)])
        await act(
            "bulk-remove too many", "POST", f"{base}/jobs/bulk-remove", form={"ids": too_many}
        )
        await act("retry-all", "POST", f"{base}/retry-all")
        await act("clean completed", "POST", f"{base}/clean?state=completed")
        await act("clean active refused", "POST", f"{base}/clean?state=active")
        await act("flows clean", "POST", f"{app.base}/queues/reports/flows/clean")
        await act("retry-flow", "POST", f"{app.base}/queues/reports/jobs/{ids['flow']}/retry-flow")
        child = await reports.get_job(ids["flow"])
        first_child = child.children_ids[0] if child and child.children_ids else "none"
        await act(
            "retry-node",
            "POST",
            f"{app.base}/queues/reports/jobs/{first_child}/retry-node?parent={ids['flow']}",
        )
        await act("trigger scheduler", "POST", f"{base}/schedulers/nightly/trigger")
        await act("trigger missing scheduler", "POST", f"{base}/schedulers/gone/trigger")
        await act("remove scheduler", "DELETE", f"{base}/schedulers/nightly")
        out["schedulers left"] = await emails.schedulers()
        await act("clear departed", "POST", f"{app.base}/workers/departed/clear")
        await act("unknown queue action", "POST", f"{app.base}/queues/nope/pause")
        blocked = await act(
            "cross-origin post", "POST", f"{base}/pause", headers={"Origin": "http://evil.example"}
        )
        out["cross-origin body"] = blocked.body
        out["cross-origin paused"] = await emails.is_paused()
        await act("same-origin post", "POST", f"{base}/resume", headers={"Origin": app.base})
    expect(out["paused"] and out["resumed"], "pause/resume did not reach the queue")
    expect(out["retried state"] == "wait", f"a retried job is {out['retried state']}")
    expect(out["promoted state"] == "wait", f"a promoted job is {out['promoted state']}")
    expect(out["cancelled state"] == "cancelled", f"a cancelled job is {out['cancelled state']}")
    expect(out["removed"] and out["bulk removed"], "a removal did not reach the queue")
    expect(out["cross-origin post"] == 403 and not out["cross-origin paused"], "CSRF guard is off")
    expect(out["schedulers left"] == [], "the scheduler survived its removal")
    expect(out["bulk-remove unreadable"] == 400, "an unreadable selection was not a 400")
    expect(out["bulk-remove too many"] == 413, "an oversized selection was not a 413")
    expect(out["clean active refused"] == 400, "cleaning the active state was not refused")
    errors = {
        k: v for k, v in out.items() if isinstance(v, int) and not isinstance(v, bool) and v >= 500
    }
    expect(not errors, f"server errors: {errors}")
    expect(app.stopped_cleanly, f"`matador serve` did not stop cleanly:\n{app.log}")
    return out


@scenario
async def incompatible_data_model_is_a_409(ctx: Ctx) -> dict[str, Any]:
    emails = ctx.queue("emails")
    await emails.add("stamp")
    await emails.redis.hset(emails.keys.meta, "model", toro.DATA_MODEL_VERSION + 1)
    async with serve(URL, ctx.prefix, queues="emails") as app:
        reply = await http("GET", f"{app.base}/queues/emails")
    expect(reply.status == 409, f"a newer data model answered {reply.status}")
    return summary(reply, "Newer data model")


# ---- create_app, the embedding API, in-process ----------------------------------------


@contextlib.asynccontextmanager
async def in_process(app: Any) -> AsyncIterator[str]:
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    base = f"http://127.0.0.1:{port}"
    try:
        expect(await ready(base), "the embedded app never answered")
        yield base
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)


@scenario
async def create_app_options(ctx: Ctx) -> dict[str, Any]:
    await seed(ctx)

    def require_token(request: Request) -> None:
        if request.headers.get("x-token") != "s3cret":
            raise HTTPException(status_code=401, detail="no token")

    shared = redis.asyncio.Redis.from_url(URL, decode_responses=True)
    app = create_app(
        list(QUEUES),
        prefix=ctx.prefix,
        connection=shared,
        dependencies=[Depends(require_token)],
        show_stacktraces=False,
        can_mutate=lambda _request: False,
    )
    token = {"x-token": "s3cret"}
    emails = ctx.queues[0]
    failed = [j.id for j in await emails.get_jobs("failed")]
    async with in_process(app) as base:
        anon = await http("GET", f"{base}/")
        authed = await http("GET", f"{base}/", headers=token)
        detail = await http("GET", f"{base}/queues/emails/jobs/{failed[0]}/detail", headers=token)
        write = await http("POST", f"{base}/queues/emails/pause", headers=token)
        static = await http("GET", f"{base}/static/app.css")
    shared_alive = await shared.ping()
    await shared.aclose()
    try:
        create_app(["a/b"], url=URL)
        slash = None
    except ValueError as exc:
        slash = str(exc)
    out = {
        "anonymous": anon.status,
        "authenticated": authed.status,
        "read-only draws no pause control": "/pause" not in authed.body,
        "stack trace hidden": [detail.status, "Traceback" not in detail.body],
        "write refused": [write.status, write.body],
        "paused": await emails.is_paused(),
        "static outside dependencies": static.status,
        "shared connection left open": shared_alive,
        "slash in a queue name": slash,
    }
    expect(anon.status == 401 and authed.status == 200, "dependencies= did not guard the routes")
    expect(out["stack trace hidden"] == [200, True], "show_stacktraces=False showed a trace")
    expect(write.status == 403 and not out["paused"], "can_mutate=False allowed a write")
    expect(shared_alive, "matador closed a connection the host owns")
    expect(slash is not None, "a queue name with '/' was accepted")
    return out


async def asgi_get(app: Any, path: str) -> int:
    """One GET straight through the ASGI interface, as a host's own server would."""
    sent: list[dict[str, Any]] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _host_fails() -> None:
    raise RuntimeError("host failed")


async def _lifespan_round(ctx: Ctx, db: int, *, fail: bool) -> dict[str, Any]:
    """A host that runs matador's lifespan inside its own, serves a page, then exits,
    cleanly or with an error. Counts the connections matador left open afterwards."""
    app = create_app(list(QUEUES), url=with_db(URL, db), prefix=ctx.prefix)
    before = clients_on(db)
    status = None
    raised = None
    try:
        async with app.router.lifespan_context(app):
            status = await asgi_get(app, "/")
            during = clients_on(db)
            if fail:
                _host_fails()
    except RuntimeError as exc:
        raised = str(exc)
    await asyncio.sleep(0.2)
    return {
        "status": status,
        "opened": during > before,
        "raised": raised,
        "left_open": clients_on(db) - before,
    }


@scenario
async def lifespan_closes_redis_on_exit(ctx: Ctx) -> dict[str, Any]:
    clean = await _lifespan_round(ctx, 13, fail=False)
    failing = await _lifespan_round(ctx, 14, fail=True)
    expect(clean["opened"] and failing["opened"], "serving a page opened no Redis connection")
    expect(clean["left_open"] == 0, f"a clean exit left {clean['left_open']} connections open")
    expect(
        failing["left_open"] == 0,
        f"an exit with an error left {failing['left_open']} Redis connections open",
    )
    return {"clean": clean, "error": failing}


@scenario
async def cli_shutdown_runs_the_lifespan(ctx: Ctx) -> dict[str, Any]:
    async with serve(URL, ctx.prefix) as app:
        status = (await http("GET", f"{app.base}/")).status
        stream = await asyncio.to_thread(_first_bytes, f"{app.base}/stream", 13)
    out = {
        "index": status,
        "stream": stream,
        "stopped_cleanly": app.stopped_cleanly,
    }
    expect(app.stopped_cleanly, f"SIGTERM did not stop `matador serve` cleanly:\n{app.log}")
    return out


# ---- Redis down -----------------------------------------------------------------------


class Outage:
    """A TCP proxy in front of the real Redis that can go down and come back on the
    same port: what the dashboard sees when Redis restarts under it."""

    def __init__(self) -> None:
        self.port = 0
        self.server: asyncio.Server | None = None
        self.links: set[asyncio.StreamWriter] = set()

    async def up(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def down(self) -> None:
        assert self.server is not None
        self.server.close()
        for writer in list(self.links):
            writer.close()
        self.links.clear()
        await self.server.wait_closed()

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        parts = urlsplit(URL)
        try:
            up_reader, up_writer = await asyncio.open_connection(parts.hostname, parts.port or 6379)
        except OSError:
            writer.close()
            return
        self.links.update((writer, up_writer))

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


@scenario
async def redis_down_at_start(ctx: Ctx) -> dict[str, Any]:
    t0 = time.monotonic()
    async with serve(f"redis://127.0.0.1:{free_port()}", ctx.prefix) as app:
        index = await http("GET", f"{app.base}/")
        queue = await http("GET", f"{app.base}/queues/emails")
        redis_bar = await http("GET", f"{app.base}/redis")
        stream = await asyncio.to_thread(_first_bytes, f"{app.base}/stream", 64)
        static = await http("GET", f"{app.base}/static/app.css")
    took = time.monotonic() - t0
    out = {
        "index": index.status,
        "queue": queue.status,
        "redis": redis_bar.status,
        "stream": stream,
        "static": static.status,
        "stopped_cleanly": app.stopped_cleanly,
    }
    expect(took < 60, f"the dashboard took {took:.0f} s to answer with Redis down")
    expect(static.status == 200, "static assets need Redis")
    expect(stream == "retry: 3000\n\n", f"the stream with Redis down sent {stream!r}")
    expect(app.stopped_cleanly, f"`matador serve` did not stop cleanly:\n{app.log}")
    return out


@scenario
async def redis_down_mid_run_and_back(ctx: Ctx) -> dict[str, Any]:
    await seed(ctx)
    outage = Outage()
    await outage.up()
    try:
        async with serve(outage.url, ctx.prefix) as app:
            before = (await http("GET", f"{app.base}/")).status
            await outage.down()
            t0 = time.monotonic()
            during = (await http("GET", f"{app.base}/queues/emails")).status
            during_took = time.monotonic() - t0
            await outage.up()
            statuses: list[int] = []

            async def back() -> bool:
                statuses.append((await http("GET", f"{app.base}/queues/emails")).status)
                return statuses[-1] == 200

            recovered = await until(back, 30)
    finally:
        with contextlib.suppress(Exception):
            await outage.down()
    out = {
        "before": before,
        "during": during,
        "recovered": recovered,
        "stopped_cleanly": app.stopped_cleanly,
    }
    expect(before == 200, f"the index answered {before} with Redis up")
    expect(during_took < HTTP_TIMEOUT, f"a page took {during_took:.1f} s with Redis down")
    expect(recovered, f"the dashboard did not recover within 30 s: {statuses[-5:]}")
    expect(app.stopped_cleanly, f"`matador serve` did not stop cleanly:\n{app.log}")
    return out


# ---- runner ---------------------------------------------------------------------------


async def run_one(name: str) -> dict[str, Any]:
    ctx = Ctx(name)
    try:
        observed = await asyncio.wait_for(SCENARIOS[name](ctx), SCENARIO_TIMEOUT)
        result = {"ok": True, "observed": json.loads(json.dumps(observed, default=str))}
    except Exception as exc:
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(),
        }
    finally:
        await ctx.cleanup()
    return result


async def run_all(names: list[str]) -> dict[str, Any]:
    results: dict[str, Any] = {"_toro": toro.__version__}
    for name in names:
        t0 = time.monotonic()
        results[name] = await run_one(name)
        results[name]["seconds"] = round(time.monotonic() - t0, 2)
        status = "ok" if results[name]["ok"] else "FAIL " + results[name]["error"]
        print(f"{name}: {status}", file=sys.stderr, flush=True)
    return results


def toro_floor() -> str:
    """The oldest toro-queue the installed matador's metadata accepts."""
    wanted = next(r for r in metadata.requires("matador-dashboard") or [] if r.startswith("toro"))
    floor = re.search(r">=\s*([\d.]+)", wanted)
    if floor is None:
        raise SystemExit(f"toro requirement {wanted!r} names no floor")
    return floor.group(1)


def main() -> int:
    if sys.argv[1:] == ["toro-floor"]:
        print(toro_floor())
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    if not URL:
        print("set REDIS_URL to a Redis you own", file=sys.stderr)
        return 2
    results = asyncio.run(run_all(args.only or list(SCENARIOS)))
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
