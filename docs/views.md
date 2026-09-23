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
| `/queues/{name}/jobs/{job_id}/detail` | The lazy accordion body for one row: data, options, result, logs, stack trace - and, for flow jobs, the flow tree, fan-in progress and children results/failures. Loaded only when a row is opened. |
| `/queues/{name}/jobs/{job_id}` | A standalone, bookmarkable page for one job (where a job-id chip links). |
| `/queues/{name}/jobs/{job_id}/flow` | Just the flow body, for the self-refreshing live region on a flow detail. |
| `/queues/{name}/metrics` | The queue's health strip: latency, then the global concurrency cap when its workers set one (how full it is, how many jobs wait on it, and a warning when workers disagree on the value), then completed and failed over the last hour and duration percentiles. |
| `/queues/{name}/flow-metrics` | The active tab's flow-throughput strip: whole flows completed/failed over the last hour with end-to-end flow-duration percentiles. |
| `/workers` | Live workers (from their heartbeats) and the departed-workers history. |
| `/workers/list` | Just the worker table, for the periodic refresh. |
| `/sidebar` | The queue nav with counts; usually delivered out-of-band rather than fetched directly. |
| `/redis` | The Redis health bar: version, memory, clients, ops/s, eviction policy. |
| `/stream` | The SSE endpoint ([Live updates](live-updates.md)). |
| `/metrics` | OpenMetrics for every watched queue, in the scraper's content type. Rendered by toro. Its depth gauges count every job in the state it is in, which is not what the tab badges count (roots only, parked parents folded into active). |

Seven job tabs: `active`, `wait`, `held`, `delayed`, `completed`, `failed`,
`cancelled`. Flows
are shown root-first - the lists hold flow roots and standalone jobs, while flow
children (a job with a parentId) are hidden, surfaced only in the parent's tree
on the detail. toro's `waiting-children` (a parked flow parent) has no tab of
its own: it folds into `active` as in-flight, and the active badge counts
`active + waiting-children`. `held` does not fold: a held job waits on its
`concurrency_key`, not on a worker, so counting it as backlog would read as work
a worker could take. Nor does `cancelled` fold into `failed`: a job stopped on
purpose did not fail, and counting it as one corrupts the failure share. The same
holds inside a flow: a child somebody stopped is counted as stopped, drawn in the
muted band of the fan-in bar rather than the red one, and read back from
`cancelled_children()` rather than `failed_children()`. A bad `state` query value (including the retired
`waiting-children`) is coerced to `active`, never an error.

Tab badges are exact root-only counts, off toro's children index: the number on
a tab is what that tab pages through, children included in neither.

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
| `POST /queues/{name}/jobs/{job_id}/cancel` | Stop a job. On a RUNNING job the worker cancels its processor where it awaits, so the work actually ends: removing it would leave the processor running. |
| `DELETE /queues/{name}/jobs/{job_id}` | Remove one job. Flow-aware: removing a flow parent removes its whole subtree (the confirm dialog says so). |
| `POST /queues/{name}/jobs/bulk-remove` | Remove the checkbox-selected jobs - capped at 1000 per request so one click can't fan out unboundedly. |
| `POST /queues/{name}/retry-all` | Re-queue every failed job. |
| `POST /queues/{name}/clean` | Remove every job in the current state. A flow root cleaned this way takes its whole subtree with it. |
| `POST /queues/{name}/flows/clean` | Cancel every parked flow (waiting-children) and its subtree - the bulk action for parked roots, which fold into the non-selectable active tab. |
| `POST /queues/{name}/jobs/{job_id}/retry-flow` | Re-drive a whole failed flow: retry every failed job in the subtree. |
| `POST /queues/{name}/jobs/{job_id}/retry-node` | Retry one node of a flow in place (re-joins its parent's barrier). |
| `POST /queues/{name}/schedulers/{id}/trigger` | Run one occurrence of a schedule now. |
| `POST /workers/departed/clear` | Clear the stopped-worker history. |
| `DELETE /queues/{name}/schedulers/{id}` | Remove a schedule. |

In a read-only dashboard (`can_mutate`) none of these are drawn, and all of them
refuse. Every action maps to ordinary public toro API (`retry_job`, `promote_job`,
`clean`, `trigger_scheduler`, …) through the shared `Service` - the dashboard
has no privileged backdoor into the queue. Who may call these routes is your
auth's decision: see [Integration](integration.md) and [Security](security.md).
