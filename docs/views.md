# Views

Every page, fragment, and action the dashboard serves. The full-page vs
fragment mechanics behind these routes are covered in
[Architecture](architecture.md); this is the map.

## Pages and fragments (the views router)

| Route | Shows |
|---|---|
| `/` | The hub: redirects you into the first queue, or an empty state when no queues are configured. |
| `/queues/{name}?state=<tab>&page=<n>&query=<q>` | The queue panel: state tabs with counts, the job list (paginated), schedulers, pause/resume. The canonical URL - tabs and pagination push it into history. |
| `/queues/{name}/jobs` | Just the job table + tab-count OOB pieces; what the [SSE refresh](live-updates.md) re-fetches. |
| `/queues/{name}/jobs/{job_id}/detail` | The lazy accordion body for one row: data, options, result, logs, stack trace. Loaded only when a row is opened. |
| `/queues/{name}/jobs/{job_id}` | A standalone, bookmarkable page for one job (where a job-id chip links). |
| `/workers` | Live workers (from their heartbeats) and the departed-workers history. |
| `/workers/list` | Just the worker table, for the periodic refresh. |
| `/sidebar` | The queue nav with counts; usually delivered out-of-band rather than fetched directly. |
| `/redis` | The Redis health bar: version, memory, clients, ops/s, eviction policy. |
| `/stream` | The SSE endpoint ([Live updates](live-updates.md)). |

Job tabs cover toro's five states: `active`, `wait`, `delayed`, `completed`,
`failed`. A bad `state` query value is coerced to `active`, never an error.

## Search

The search box does two things in one query:

- **Exact id lookup first** - pasting a job id finds it across *all* states in
  O(1), badged as an exact match.
- **Bounded substring scan** - otherwise the query matches against job `name`
  and `data` within the most recent **500** jobs of the current state (toro's
  `search()` is a scan, not an index), and the UI says so rather than implying
  it searched everything.

## Pagination

20 jobs per page. The pager renders a window - first, last, current ±2, with
ellipses - and every page link is a real URL (`hx-push-url`), so deep pages
survive reload and back/forward.

## Actions (the actions router)

Mutations are `POST`/`DELETE` routes; each re-renders the affected panel, and
queue-level actions also ship the sidebar out-of-band so counts update in the
same round trip. Failures (4xx/5xx) render into a toast instead of failing
silently.

| Route | Does |
|---|---|
| `POST /queues/{name}/pause` · `/resume` | Pause / resume the queue (in-flight jobs finish). |
| `POST /queues/{name}/jobs/{job_id}/retry` | Retry one failed job. |
| `POST /queues/{name}/jobs/{job_id}/promote` | Run a delayed job now. |
| `DELETE /queues/{name}/jobs/{job_id}` | Remove one job. |
| `POST /queues/{name}/jobs/bulk-remove` | Remove the checkbox-selected jobs - capped at 1000 per request so one click can't fan out unboundedly. |
| `POST /queues/{name}/retry-all` | Re-queue every failed job. |
| `POST /queues/{name}/clean` | Remove every job in the current state. |
| `POST /queues/{name}/schedulers/{id}/trigger` | Run one occurrence of a schedule now. |
| `DELETE /queues/{name}/schedulers/{id}` | Remove a schedule. |

Every action maps to ordinary public toro API (`retry_job`, `promote_job`,
`clean`, `trigger_scheduler`, …) through the shared `Service` - the dashboard
has no privileged backdoor into the queue. Who may call these routes is your
auth's decision: see [Integration](integration.md) and [Security](security.md).
