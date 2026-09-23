# Integration

matador is built by one factory, `create_app`, which returns a FastAPI app. You
either run it standalone or mount it into an existing service.

```python
def create_app(
    names: list[str],
    *,
    url: str = "redis://localhost:6379",
    prefix: str = "toro",
    connection: Redis | None = None,
    dependencies: Sequence[params.Depends] | None = None,
    require_same_origin: bool | None = None,   # None: on
    show_stacktraces: bool = True,
    can_mutate: Callable[[Request], bool] | None = None,
) -> FastAPI: ...
```

`names` is the list of queue names to watch. They must use the **same `prefix`**
as the toro `Queue`/`Worker` that produced them (default `toro`), because that
prefix is how matador computes the Redis keys it reads. A matador watching a queue
under the wrong prefix sees an empty queue.

## Standalone

The no-extras case: open a connection from `url` and serve.

```python
from matador import create_app

app = create_app(["emails", "billing"], url="redis://localhost:6379")
# uv run uvicorn run:app --reload   →   http://localhost:8000
```

It serves **HTML** (an HTMX UI), not a JSON API - point a browser at it.

## Mount into an existing app

matador is an ASGI app, so `mount` it at any path. URLs are `root_path`-aware
(Starlette `url_for`), so a sub-path mount just works - links, static assets, and
the SSE stream all carry the prefix.

```python
from fastapi import Depends
from matador import create_app

app.mount(
    "/toro",
    create_app(
        ["emails", "billing"],
        connection=redis,                       # share your existing pool
        dependencies=[Depends(require_admin)],  # gate it with your auth
    ),
)
```

### `connection=` - share your Redis pool

Pass your `redis.asyncio.Redis` and matador uses it instead of opening its own.
**matador never closes a connection it didn't create.** This is also the *correct*
way to embed: a mounted sub-app's lifespan does not run, so the host app must own
the connection's lifecycle. Omit `connection` (standalone) and matador opens one
from `url` and closes it on shutdown.

### `dependencies=` - protect it with your auth

A `Sequence[Depends]` applied to every route, so your app's auth gates the whole
dashboard. One caveat: the `/static` mount is itself a sub-app and is **not**
covered by these dependencies; if the assets themselves must be protected, wrap
the entire mount. (FastAPI's `/docs`, `/redoc` and `/openapi.json` would be a second
caveat, since `dependencies=` does not cover them either: matador does not serve
them.)

### `require_same_origin=` - CSRF defense

**On by default.** It rejects state-changing requests (POST/DELETE/…) whose `Origin`
header doesn't match the request host: a stateless CSRF defense. The credential such
an attack rides is the host app's, whatever matador itself requires, so the default
does not wait to be told that auth exists. Requests with no `Origin` (curl,
server-to-server) pass, because the defense is aimed at browsers.
`require_same_origin=False` turns it off. Behind a reverse proxy, make sure the forwarded Host is correct
(uvicorn `--proxy-headers`) so legitimate same-origin requests aren't blocked.
See [Security](security.md).

### `show_stacktraces=` - hide job stack traces

`True` by default. Set `False` to omit job stack traces from the UI; they can leak
source paths, dependency versions, and occasionally secrets from exception
messages, which matters when the dashboard is reachable by people who shouldn't
see internals.

### `can_mutate=` - a read-only dashboard

A predicate, not a flag, and it receives the raw request: matador has no identity
of its own, so whatever the host app authenticates with is what arrives here. The
same dashboard can be read-only for some callers and not others.

```python
def can_mutate(request) -> bool:
    return getattr(request.state, "role", None) == "admin"  # whatever your auth sets

app.mount("/toro", create_app(["emails"], can_mutate=can_mutate))
```

Every state-changing method (anything but GET/HEAD/OPTIONS) is refused with 403,
and the controls are **not drawn**: a button that exists and refuses invites the
click and reports a failure that was never one. The guard keys on the method
rather than on a list of routes, so a route added later is covered by
construction.

It runs on **every** request, reads included, because the templates ask the same
question. It is your code, so it can break: a predicate that raises is logged and the
request is treated as read-only, rather than failing the page. Reach for the request's
own attributes, not Starlette's `request.user`, which raises unless the host installed
`AuthenticationMiddleware`.

Omit it and the dashboard mutates, which is what it does when nobody has said
otherwise. It is not authentication: `dependencies=` decides who reaches the
dashboard at all, `can_mutate` decides what they can do once there. Nor does it stop
matador's own housekeeping: loading a page still prunes workers whose heartbeat has
expired, which is bookkeeping rather than an operator action.

## Metrics

`GET /metrics` renders every watched queue as one OpenMetrics exposition, in the
content type a scraper parses by
(`application/openmetrics-text; version=1.0.0; charset=utf-8`). toro does the
rendering, and matador adds no numbers of its own. The gauges are **not** the tab
badges: a badge counts flow roots and folds parked parents into active, while
`toro_queue_depth` counts every job, children included, in the state it is in. The
counters are lifetime, where the panel's strip is the last hour. The families and what
they mean: toro's
[operating page](https://github.com/ilovepixelart/toro/blob/main/docs/operating.md).

A scrape reads Redis, so an unreachable Redis is a 500, which is what a scraper needs
to see.

It is a route like any other, so `dependencies=` gates it too. A scraper that
cannot carry your auth needs its own path to it (a separate mount, or an allow
rule on the proxy).

## Other stacks

matador is Python/ASGI. For Django, Flask, or non-Python services, run matador
standalone and reverse-proxy to it.
