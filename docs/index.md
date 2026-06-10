# matador documentation

Reference docs for how matador works. The [README](../README.md) is the quick start.

matador is a server-rendered dashboard for [toro](https://github.com/ilovepixelart/toro)
queues: FastAPI + Jinja on the server, HTMX + Tailwind on the page, reading
straight from Redis through toro's async API. No JSON API, no SPA, no client state.

## Pages

- **[Architecture](architecture.md)** — the HTMX hypermedia model: the URL as
  state, full-page vs fragment responses, and how a request becomes HTML.
- **[Integration](integration.md)** — `create_app` and every option: mounting
  into an existing app, sharing a Redis pool, auth, CSRF, stack-trace control.
- **[Live updates](live-updates.md)** — the SSE stream, change events, the
  live-updating tables, and out-of-band swaps.
- **[Views](views.md)** — each screen explained: queues, job tabs, job detail,
  search, workers, the Redis health bar, schedulers.
- **[Templates](templates.md)** — how the templates are organized
  (layouts / pages / partials) and the macro hub.
- **[Security](security.md)** — same-origin CSRF, the always-on security headers,
  and the bounded JSON rendering.
