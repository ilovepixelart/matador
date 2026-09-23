# Security

matador's posture in one line: **it ships no auth and assumes you put your own
in front** - everything else is defense in depth that's on by default.

## Authentication: bring your own

The dashboard can pause queues and delete jobs, so who reaches it matters.
matador deliberately doesn't invent a login: you gate it with your app's
existing auth via `dependencies=[Depends(...)]`, which applies to **every**
route - pages, fragments, actions, and the SSE stream. The one carve-out is
the `/static` sub-app (wrap the whole mount if the assets must be private too).

FastAPI's auto-docs are not served at all. They are registered as plain Starlette
routes, so `dependencies=` never covered them: mounted behind auth they were the one
unauthenticated page on the mount, they published every mutating endpoint and its
parameters, and `/docs` loaded a third-party script onto the host app's origin.
Wiring details: [Integration](integration.md).

## Read-only: `can_mutate`

Gating who reaches the dashboard is `dependencies=`; gating what they can do once
there is `can_mutate(request)`, a predicate the host app supplies. It refuses every
state-changing method with 403 and leaves the controls out of the markup, so a
viewer sees a dashboard rather than a wall of buttons that refuse. Keyed on the
method, not on a list of routes, so nothing is exposed by being forgotten, and a
predicate that raises is logged and refuses rather than taking the page down.

What it does not cover: matador's own housekeeping. Reading a page prunes workers
whose heartbeat has expired, which writes to the presence keys and the stopped-worker
log. No operator action is reachable, but read-only is not "this connection never
writes". Wiring: [Integration](integration.md).

## CSRF: `require_same_origin`

**On by default.** The ambient credential a CSRF attack rides belongs to the host
app, and a host authenticates in more ways than `dependencies=`: its own middleware,
a session, an authenticating proxy. Keying the default on `dependencies` left all of
those open to a plain cross-origin form post, which is what this guard exists for.
It rejects state-changing methods (anything but GET/HEAD/OPTIONS) whose `Origin`
header doesn't match the request host, a stateless same-origin check.
`require_same_origin=False` turns it off. Requests without an `Origin` (curl,
server-to-server) pass - the defense targets browsers, where the cookie is.
Behind a proxy, forward the real host (`--proxy-headers`) or legitimate
requests get blocked.

## Always-on response headers

Every response matador routes - including the blocked and not-found ones, since the
header middleware wraps the others - carries:

```
X-Frame-Options: DENY                              # no embedding/clickjacking
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
```

They're set with `setdefault`, so a host app can override deliberately.

## The client is locked down too

`base.html` pins the htmx config:

```html
<meta name="htmx-config" content='{"selfRequestsOnly": true, "historyCacheSize": 0}' />
```

- `selfRequestsOnly` - htmx will refuse to issue requests to another origin,
  even if an attribute somehow said so.
- `historyCacheSize: 0` - htmx normally snapshots swapped HTML into
  sessionStorage for instant back-navigation; that would persist job payloads
  and stack traces in the browser. Disabled.

## Untrusted input, bounded output

Job data is arbitrary user content, and the dashboard renders it:

- **Escaping** - everything goes through Jinja autoescaping; job data is
  rendered as highlighted JSON, never interpreted as HTML.
- **Output bounded** - everything a job carries is written by whoever enqueued it,
  which is often a web app's users, so the page clips it: a row's payload and failure
  reason at 500 characters, a detail's reason and stack trace at 4,000, the log at the
  newest 200 lines, and the `pretty` JSON filter at 20,000 characters before it
  reaches the highlighter. Without those, one fat payload made a listing 18 MB, and
  the live region re-fetches that listing on every change event.
- **No backtracking regex on attacker-controlled strings** - the sidebar derives the
  active queue from the client-supplied `HX-Current-URL` header with `rfind` +
  slicing rather than a quadratic pattern. (One linear `re.search` remains on that
  header, to tell a workers URL from a queue one.)
- **Inputs coerced, not trusted** - an unknown `state` value falls back to
  `active`; bulk-remove is capped at 1000 ids per request; search runs toro's
  bounded scan ([Views](views.md)).

## Limits worth knowing

- A 500 from an unhandled error is produced outside matador's middleware, so it
  carries none of the headers above. Its body is the bare text `Internal Server
  Error`; a host app that wants headers on it owns that layer.
- `bulk-remove` caps the number of **ids** in one request at 1000, not the number of
  jobs: removing a flow root removes its whole subtree.
- The Redis health bar's key count is the whole logical database, not this queue's
  prefix, so on a shared Redis it reflects co-tenants too.
- A mounted sub-app's lifespan does not run, so `Service.close()` does not either:
  the event broadcaster and its pub/sub connection live until the process ends. That
  is the same reason a mounted dashboard should be given a `connection=` it does not
  own.

## Information exposure

Stack traces are genuinely useful on a jobs dashboard and genuinely leaky (paths,
versions, dependency internals). They're shown by default; pass
`show_stacktraces=False` when the audience is wider than the people who own the code.

**It does not hide the exception message.** The message *is* the failure reason a
dashboard exists to show, and it is rendered on the row, the detail and the job page
whatever this setting says. A secret interpolated into an exception is a secret in
your queue, not something a dashboard flag can take back: keep it out of the message
([toro's security page](https://github.com/ilovepixelart/toro/blob/main/docs/security.md)
says the same about payloads).

The same reasoning applies to the dashboard as a whole: counts, payloads, and
worker hostnames are operational intelligence. Treat the mount as an admin
surface, not a public page.
