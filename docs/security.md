# Security

matador's posture in one line: **it ships no auth and assumes you put your own
in front** — everything else is defense in depth that's on by default.

## Authentication: bring your own

The dashboard can pause queues and delete jobs, so who reaches it matters.
matador deliberately doesn't invent a login: you gate it with your app's
existing auth via `dependencies=[Depends(...)]`, which applies to **every**
route — pages, fragments, actions, and the SSE stream. The one carve-out is
the `/static` sub-app (wrap the whole mount if the assets must be private too).
Wiring details: [Integration](integration.md).

## CSRF: `require_same_origin`

Off by default — with no auth there's no ambient credential to ride. Turn it on
when you put **cookie-based** auth in front: it rejects state-changing methods
(anything but GET/HEAD/OPTIONS) whose `Origin` header doesn't match the request
host, a stateless same-origin check. Requests without an `Origin` (curl,
server-to-server) pass — the defense targets browsers, where the cookie is.
Behind a proxy, forward the real host (`--proxy-headers`) or legitimate
requests get blocked.

## Always-on response headers

Every response — including error and blocked ones, since the header middleware
wraps the others — carries:

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

- `selfRequestsOnly` — htmx will refuse to issue requests to another origin,
  even if an attribute somehow said so.
- `historyCacheSize: 0` — htmx normally snapshots swapped HTML into
  sessionStorage for instant back-navigation; that would persist job payloads
  and stack traces in the browser. Disabled.

## Untrusted input, bounded output

Job data is arbitrary user content, and the dashboard renders it:

- **Escaping** — everything goes through Jinja autoescaping; job data is
  rendered as highlighted JSON, never interpreted as HTML.
- **Output bounded** — the `pretty` JSON filter truncates at 20k characters, so
  a multi-megabyte payload can't hang the highlighter or the page.
- **No regex on attacker-controlled strings** — the sidebar derives the active
  queue from the client-supplied `HX-Current-URL` header with `rfind` + slicing
  rather than a backtracking regex (a ReDoS fix: the old pattern was quadratic
  on long non-matching URLs).
- **Inputs coerced, not trusted** — an unknown `state` value falls back to
  `active`; bulk-remove is capped at 1000 ids per request; search runs toro's
  bounded scan ([Views](views.md)).

## Information exposure

Stack traces are genuinely useful on a jobs dashboard and genuinely leaky
(paths, versions, the odd secret in an exception message). They're shown by
default; pass `show_stacktraces=False` when the audience is wider than the
people who own the code.

The same reasoning applies to the dashboard as a whole: counts, payloads, and
worker hostnames are operational intelligence. Treat the mount as an admin
surface, not a public page.
