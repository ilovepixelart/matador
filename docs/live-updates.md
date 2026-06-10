# Live updates

The dashboard stays current without polling JSON or holding client state: the
server emits a tiny **Server-Sent Events** signal, and the affected HTML asks to
be re-rendered.

## One signal, not a firehose

A single **broadcaster** per dashboard holds ONE pub/sub subscription across
every watched queue's toro events channel and fans a wakeup out to each
connected stream — so N open tabs cost one Redis connection, not N, and idle
tabs can't starve the action routes' pool. `/stream` does **not** forward job
events to the browser. Whatever arrives — one finish or a thousand — each
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
silence anyway — a heartbeat that keeps the connection alive through proxies
(and is why idle regions still refresh occasionally). The stream advertises
`retry: 3000`, so a dropped connection reconnects on its own — including when
Redis itself goes away: the stream ends cleanly and the browser's reconnect
loop picks things back up once the broadcaster can subscribe again.

## How the page reacts

The SSE connection lives on `<body>` (the htmx `sse` extension):

```html
<body hx-ext="sse,morph,loading-states,response-targets"
      sse-connect="{{ url_for('stream').path }}">
```

Each live region listens for the signal with its own throttle, and re-fetches
*its own* fragment — the server stays the single source of what HTML looks like:

| Region | Trigger |
|---|---|
| Job list | `sse:changed throttle:1s` + `hx-sync="this:drop"` |
| Sidebar (counts, highlights) | `sse:changed throttle:400ms` |
| Workers list | `every 2s, sse:changed throttle:1s` |
| Redis bar | `load, sse:changed throttle:1s, every 8s` |

`throttle` caps each region's refresh rate; `hx-sync="this:drop"` drops a
refresh that arrives while one is already in flight. Swaps use **morph**
(idiomorph), which patches the DOM in place instead of replacing it — open
accordions, focus, and scroll positions survive a refresh.

Tab counts ride along as out-of-band fragments (`tab_counts_oob.html`) on the
list refresh, so one response updates the table *and* the numbers on the tabs.

## The live table pauses while you read

Refreshing a job list while you have a row expanded would yank the detail out
from under you. A small behavior (`jobs-live.js`) checks for an open
`<details>` in the table on every refresh attempt and skips the swap while one
is open; a "live updates paused" notice appears (pure CSS, a `:has()` rule) and
updates resume the moment you close the row.

## Why SSE and not WebSockets

The data only flows one way — the browser never pushes over the stream
(actions are ordinary `hx-post`s). SSE is plain HTTP: it works through the same
auth `dependencies`, proxies, and mounts as every other route
([Integration](integration.md)), reconnects natively, and needs no protocol
upgrade. The whole client side is the stock htmx SSE extension.
