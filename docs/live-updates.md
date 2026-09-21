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
stream says only that something changed, at three rates:

| Event | At most once per | For |
|---|---|---|
| `changed-fast` | 400 ms | the sidebar |
| `changed` | 1 s | job list, workers, flow section, Redis bar |
| `changed-slow` | 5 s | the queue's health strips, the costly reads |

Each rate has a leading and a trailing edge (`matador/coalescer.py`, driven by
`Service.event_stream`):

1. A job event arrives with the window open: the rate emits immediately, so the
   common, quiet case pays no latency, and the window closes for its interval.
2. Events that arrive while it is closed are remembered, not counted: however
   many there are, the rate owes one emit.
3. When the window reopens owing an emit, the rate emits once more.

Step 3 is the point. The refresh an emit triggers reads the state at that
moment, so a change that lands later in the window is in nobody's repaint. A
limit with only a leading edge drops it: the last job of a batch stays listed as
active until something else happens.

Under a storm of job events the cost of a refresh is bounded by the HTML render,
not by queue throughput, and while every window is closed the stream waits on the
clock rather than waking once per job. A rate that has sent nothing for 8 seconds
emits - a heartbeat, counted per rate, that keeps the connection alive through proxies,
refreshes relative times, and covers the transitions toro does not publish
(below). The stream advertises `retry: 3000`, so a dropped connection reconnects
on its own - including when Redis itself goes away: the stream ends cleanly and
the browser's reconnect loop picks things back up once the broadcaster can
subscribe again.

### What the stream cannot announce

toro publishes `added`, `completed`, `failed` and `progress`. A claim, a retry, a
delayed job's promotion and a stalled job's recovery publish nothing, so they
reach the page with the heartbeat: a job moving from waiting to active can take
up to 8 seconds to show.

## How the page reacts

The SSE connection lives on `<body>` (the htmx `sse` extension):

```html
<body hx-ext="sse,morph,loading-states,response-targets"
      sse-connect="{{ url_for('stream').path }}">
```

Each live region listens to the rate it can afford, and re-fetches
*its own* fragment - the server stays the single source of what HTML looks like:

| Region | Trigger |
|---|---|
| Job list | `sse:changed` |
| Sidebar (counts, highlights) | `sse:changed-fast` |
| Workers list | `every 2s, sse:changed` |
| Redis bar | `load, sse:changed, every 8s` |
| Health strips | `sse:changed-slow, every 30s` |

No region throttles on the client. htmx's `throttle` fires on the first event of
a window and returns early for the rest, with no trailing request (the 2.0.10
source; its documentation reads otherwise), so it would drop exactly the emit the
stream's trailing edge exists to deliver. Every region also carries
`hx-sync="this:queue last"`: a refresh that arrives while one is in flight is kept,
one deep. That is what htmx does for an element with no `hx-sync` at all, but an
`hx-sync` that names no strategy means `drop`, which loses the tail again, so the
strategy is stated on every region. A test reads every template and fails on a client throttle or a missing
`queue last`. Swaps use **morph**
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
