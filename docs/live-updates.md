# Live updates

The dashboard stays current without polling JSON or holding client state: the
server emits a tiny **Server-Sent Events** signal, and the affected HTML asks to
be re-rendered.

## One signal, not a firehose

A single **broadcaster** per dashboard holds ONE pub/sub subscription across
every watched queue's toro events channel and fans a wakeup out to each
connected stream - so N open tabs cost one Redis connection, not N, and idle
tabs can't starve the action routes' pool. `/stream` does **not** forward job
events to the browser. Whatever arrives - one finish or a thousand - each
stream emits the same coalesced signal:

```
event: changed
data: 1
```

The coalescing works like this (`Service.event_stream`):

1. A job event arrives → the broadcaster wakes every stream, which emits
   `changed` immediately (no added latency for the common, quiet case).
2. Whatever else lands in the next **200ms** rides that same repaint.
3. Repeat.

So under a storm of job events the browser sees at most ~5 `changed`/s, and the
cost of a refresh is bounded by the HTML render, not by queue throughput. When
the queues are quiet, the same `changed` signal is emitted after 8 seconds of
silence anyway - a heartbeat that keeps the connection alive through proxies
(and is why idle regions still refresh occasionally). The stream advertises
`retry: 3000`, so a dropped connection reconnects on its own - including when
Redis itself goes away: the stream ends cleanly and the browser's reconnect
loop picks things back up once the broadcaster can subscribe again.

## How the page reacts

The SSE connection lives on `<body>` (the htmx `sse` extension):

```html
<body hx-ext="sse,morph,loading-states,response-targets"
      sse-connect="{{ url_for('stream').path }}">
```

Each live region listens for the signal with its own throttle, and re-fetches
*its own* fragment - the server stays the single source of what HTML looks like:

| Region | Trigger |
|---|---|
| Job list | `sse:changed throttle:1s` + `hx-sync="this:drop"` |
| Sidebar (counts, highlights) | `sse:changed throttle:400ms` |
| Workers list | `every 2s, sse:changed throttle:1s` |
| Redis bar | `load, sse:changed throttle:1s, every 8s` |

`throttle` caps each region's refresh rate; `hx-sync="this:drop"` drops a
refresh that arrives while one is already in flight. Swaps use **morph**
(idiomorph), which patches the DOM in place instead of replacing it - open
accordions, focus, and scroll positions survive a refresh.

Tab counts ride along as out-of-band fragments (`tab_counts_oob.html`) on the
list refresh, so one response updates the table *and* the numbers on the tabs.

### Every live region targets its own stable id (never `hx-target="this"`)

`#queue-panel` carries an inherited `hx-target="#queue-panel"` so navigation
controls (tabs, pagination, pause/resume, toolbar) re-render the whole panel by
default. That inheritance is a trap for live regions. htmx does **not** reliably
tear down an `sse:`/`every` listener the instant an *ancestor* `innerHTML`-swaps
the region away ([htmx#1350](https://github.com/bigskysoftware/htmx/issues/1350)),
so a region can fire once *after* you've navigated the panel elsewhere. If that
region used `hx-target="this"`, htmx - now unable to resolve `this` on the
detached node - re-roots to the inherited `#queue-panel` and swaps its small
fragment over the whole panel. That was the "open a job, the body disappears on
the next event" bug.

The rule, per htmx's own guidance on
[targets](https://htmx.org/attributes/hx-target/) and
[inheritance](https://htmx.org/docs/#inheritance): **a self-refreshing region
sets `hx-target` to its own stable id** (`#metrics-live`, `#flow-section`,
`#flow-metrics`, `#workers-list`, `#jobs`), not `this`. When such a region fires
after navigation, its id is gone, so htmx raises `htmx:targetError` and
*aborts* - it can never clobber the panel. A unit test
(`test_no_live_region_targets_this`) fails the build if any template reintroduces
`hx-target="this"`.

## The live table pauses while you read

Refreshing a job list while you have a row expanded would yank the detail out
from under you. A small behavior (`jobs-live.js`) checks for an open
`<details>` in the table on every refresh attempt and skips the swap while one
is open; a "live updates paused" notice appears (pure CSS, a `:has()` rule) and
updates resume the moment you close the row.

## Why SSE and not WebSockets

The data only flows one way - the browser never pushes over the stream
(actions are ordinary `hx-post`s). SSE is plain HTTP: it works through the same
auth `dependencies`, proxies, and mounts as every other route
([Integration](integration.md)), reconnects natively, and needs no protocol
upgrade. The whole client side is the stock htmx SSE extension.
