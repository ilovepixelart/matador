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
    require_same_origin: bool = False,
    show_stacktraces: bool = True,
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

It serves **HTML** (an HTMX UI), not a JSON API — point a browser at it.

## Mount into an existing app

matador is an ASGI app, so `mount` it at any path. URLs are `root_path`-aware
(Starlette `url_for`), so a sub-path mount just works — links, static assets, and
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

### `connection=` — share your Redis pool

Pass your `redis.asyncio.Redis` and matador uses it instead of opening its own.
**matador never closes a connection it didn't create.** This is also the *correct*
way to embed: a mounted sub-app's lifespan does not run, so the host app must own
the connection's lifecycle. Omit `connection` (standalone) and matador opens one
from `url` and closes it on shutdown.

### `dependencies=` — protect it with your auth

A `Sequence[Depends]` applied to every route, so your app's auth gates the whole
dashboard. One caveat: the `/static` mount is itself a sub-app and is **not**
covered by these dependencies; if the assets themselves must be protected, wrap
the entire mount.

### `require_same_origin=` — CSRF defense

`False` by default. Set `True` to reject state-changing requests (POST/DELETE/…)
whose `Origin` header doesn't match the request host — a stateless CSRF defense.
matador ships no auth, so CSRF is moot by default; enable this when you put
**cookie-based** auth in front (a cross-site form would otherwise carry the
cookie). Behind a reverse proxy, make sure the forwarded Host is correct
(uvicorn `--proxy-headers`) so legitimate same-origin requests aren't blocked.
See [Security](security.md).

### `show_stacktraces=` — hide job stack traces

`True` by default. Set `False` to omit job stack traces from the UI; they can leak
source paths, dependency versions, and occasionally secrets from exception
messages, which matters when the dashboard is reachable by people who shouldn't
see internals.

## Other stacks

matador is Python/ASGI. For Django, Flask, or non-Python services, run matador
standalone and reverse-proxy to it.
