# Upgrading

Breaking changes by release, newest first, each with what to do about it.

## 1.0.6

Nothing breaks. Requires `toro-queue` 1.0.3.

**The cap chip reads the queue's own limits.** A queue whose limits were set with
toro's `Queue.set_limits()` shows the cap they name whatever its workers were started
with, and no cap when they set none; the "mixed" warning is left for a queue with no
limits of its own whose workers disagree. The service reads `Queue.limits()`, which
toro added in 1.0.3.

## 1.0.5

Nothing breaks in the API, and one action changes what it does.

**"Cancel parked flows" cancels instead of deleting.** The bulk action on the active
tab deleted every parked flow (a parent in `waiting-children`) and its subtree, so
nothing was left to see what had been stopped. It now calls toro's `cancel_job` on
each parked root: the root and its children land in `cancelled`, kept by each job's
`remove_on_fail` retention, as a cancel from the job page already did. The button,
its confirmation and the announcement say cancel. Anyone relying on the action to
free the space those jobs took should expect them in the cancelled tab until their
retention trims them.

## 1.0.4

Nothing breaks. Two fixes, both in what the flow views show.

**A child's failure reason is clipped in the flow views.** The failures list and each
node of the flow tree rendered a child's exception message in full, so one child failing
with a long message made its parent's detail page hundreds of kilobytes. Both now clip it
at 500 characters, like the job rows.

**The parked-flows bulk action says what it does.** "Cancel parked flows" deleted the
flows and their subtrees, leaving nothing in `cancelled`. The button, its confirm and the
announcement now say remove.

The package is now marked `Development Status :: 5 - Production/Stable`.

## 1.0.3

Nothing breaks. Three fixes, all in a dashboard mounted into a host app or left open
while it refreshes.

**A host route can no longer take over a dashboard link.** matador resolved its links
through the host app's routes, so a host route registered before the mount with the
same name and path parameters won: a host route named `retry` received the per-job
Retry button's post, and a host mount named `static` served the stylesheet. Links now
resolve against matador's own routes under the mount's prefix. A host without
namesakes sees the same links as before.

**A deselect during a live refresh stays deselected.** The table re-applied the
selection only once a refresh settled, about 20ms after the swap. A row unchecked in
that gap came back checked and stayed in the set a bulk action sends. The selection is
now re-applied in the same task as the swap.

**The job page's back link stays inside a mounted dashboard.** Under a prefix, the back
link dropped the tab and page the job was opened from, and could point at a host page
outside the mount. It now returns to the view the job was opened from, or to the
queue page when that view is outside the dashboard.

## 1.0.2

Nothing breaks.

**A queue written by a newer toro is refused, not rendered.** Each queue carries the
data-model version that wrote it. toro checks it where it writes, which is almost
nowhere in a dashboard, so matador read a queue stamped newer than it understands and
rendered that shape field by field. Workers are upgraded before dashboards, which is
the ordinary way to arrive here. Opening such a queue now gives a 409 naming both
version numbers. Opening a queue still never stamps one.

## 1.0.1

Nothing breaks. Two fixes, both in what a long-lived dashboard does over time.

**A live refresh no longer shows a selection as lost.** Selecting rows and then
letting the page refresh itself could redraw the table with every checkbox clear
while the selection was still held, so the next bulk action ran on what you could
no longer see. The table re-applies the selection on the frame after the swap.

**The Redis subscription ends with the last viewer.** A dashboard mounted into a
host app opened one pub/sub connection for its first live viewer and held it,
subscribed, for the life of the process. It is released when the last viewer
disconnects, and a later viewer starts a fresh one.

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
