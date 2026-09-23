# Upgrading

Breaking changes by release, newest first, each with what to do about it.

## 1.0.0

Two defaults change, both because a security review found the old ones unsafe for a
mounted dashboard. Neither changes what the dashboard does once you are past them.

**The same-origin guard is on.** It used to turn itself on only when `dependencies=`
were set, which is one of several ways a host app authenticates: middleware, a
session, an authenticating proxy are at least as common, and each left the host's
cookie ambient for a cross-origin form post. If something legitimate posts to matador
from another origin, pass `require_same_origin=False` and put the check where it
belongs. Requests with no `Origin` header (curl, a scraper) were never affected.

**`/docs`, `/redoc` and `/openapi.json` are gone.** FastAPI registers them as plain
Starlette routes, so `dependencies=` never covered them: behind auth they were the one
unauthenticated page on the mount, and `/docs` loaded a third-party script onto the
host app's origin. A dashboard has no API for a human to explore; nothing else moved.

**What a job carries is clipped before it renders**: a row's payload and failure
reason at 500 characters, a detail's reason and stack trace at 4,000, the log at its
newest 200 lines. A clipped value says how long the whole thing was. Nothing is
truncated in Redis.

**It requires toro 1.0.** The two releases close the same review together: matador
passes a job id straight to `remove_job`, and toro 1.0 is what makes that safe.
