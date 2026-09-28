# Architecture

matador is a **hypermedia** application: the server responds with HTML, the
browser swaps it in via HTMX, and there is no client-side state to keep in sync.

## The URL is the state

Every queue / tab / page is a real URL:

```bash
/queues/<name>?state=<tab>&page=<n>
```

Navigation (tabs, pagination, picking a queue) is an `hx-get` to that URL with
`hx-push-url`, so the address bar always reflects what you're looking at. Reload,
back/forward, and bookmarks all work, and the server never has to reconcile a
duplicate client-side model. There is no JSON; the server renders the active tab
and active queue directly (no client-side class-toggling).

## One route, two responses: full page vs fragment

The same route serves both a full document and a fragment, decided by the
`HX-Request` header:

- **Direct navigation or history restore** → the **whole page** (the layout, the
  sidebar, the panel). This is required: HTMX pushes URLs into history, and a
  pushed URL *must* return a full page when visited directly or restored.
- **An HTMX swap** → just the **panel fragment**, the minimal HTML for the target.

The decision lives in `wants_fragment(request)`:

```python
def wants_fragment(request):
    return (
        request.headers.get("hx-request") == "true"
        and request.headers.get("hx-history-restore-request") != "true"
    )
```

Note the second clause: a history-restore re-requests the pushed URL *with*
`HX-Request`, but it needs the **whole** page, so it is deliberately excluded.

The sidebar's active-queue highlight is derived server-side from the
`HX-Current-URL` header (`_selected_from_url`), so even a fragment response knows
which queue you're on.

## Out-of-band updates

A single response can update more than its primary target using HTMX
out-of-band swaps (`hx-swap-oob`). For example, acting on a queue re-renders the
panel *and* ships an OOB sidebar fragment so the counts/highlight refresh in the
same round trip. Live updates over SSE use the same idea. See
[Live updates](live-updates.md).

## Request → HTML, end to end

1. The browser issues `hx-get` / `hx-post` to a real URL.
2. **Middleware** runs: the same-origin CSRF check (on unless turned off), the
   read-only guard when `can_mutate=` is set, and always-on security headers
   (registered last, so the headers wrap even a blocked response). See
   [Security](security.md).
3. A **route** handler (in the views router or one of the actions routers) calls the **`Service`**,
   which reads/writes Redis through toro's async API and returns plain dicts.
4. The handler renders a **Jinja template** to HTML - a full page or a fragment,
   per `wants_fragment`.
5. HTMX swaps the returned HTML into the target (`hx-target` / `hx-swap`), plus
   any OOB pieces.

## Server-side shape

- **Routers** - `create_app` includes a *views* router (read: pages and
  fragments) and two *actions* routers: `_actions_router` for pause/resume and
  the single-job mutations (retry, remove, cancel, promote), `_queue_actions_router`
  for the queue-wide ones (bulk remove, retry-all, clean, schedulers). All
  rendering goes through a shared `Service`.
- **Templates** - organized as `layouts/` (the page skeleton), `pages/` (full
  documents that extend the layout), and `partials/` (the HTMX swap fragments),
  with reusable bits in `macros.html`. See [Templates](templates.md).
- **Static** - htmx + extensions, the built Tailwind CSS, fonts, and small
  behavior scripts are served from a mounted `/static`, self-hosted (no CDN).

Because it's hypermedia, the **behavior lives on the elements** (`hx-*`
attributes) rather than in a separate JS layer (Locality of Behavior). The client
JavaScript is just htmx plus a few vendored extensions (idiomorph for morphing,
SSE, response-targets, loading-states) and a handful of small
progressive-enhancement behaviors: theme, tooltips, bulk-select, confirm dialogs,
toasts, the live-table pause, the row-toggle guards, the connection status, the
phone sidebar drawer, and keyboard shortcuts.
