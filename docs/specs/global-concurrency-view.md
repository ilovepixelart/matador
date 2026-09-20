# Global concurrency in the dashboard

## Problem and outcome

toro 0.6.0 added `Worker(global_concurrency=N)`: one cap on jobs active at once
across every worker of a queue. matador shows nothing about it. Under a full
cap the latency chip climbs and turns amber while every worker looks healthy,
which reads as a stuck queue when it is the cap doing its job.

Outcome: the queue panel says when a cap exists, how full it is, and when jobs
are waiting on it rather than on a worker. Workers that disagree on the cap,
which toro does not reconcile, are flagged.

## Design

- **Source.** toro reports `global_concurrency` per worker in `Queue.workers()`
  (0 when unset). The cap is a worker option, so the queue's cap is whatever its
  live workers agree on. `Service.metrics()` gains a `cap` entry built from the
  live workers, `counts.active`, and `counts.wait`.
- **One chip, in the existing strip.** It sits directly after latency, the number
  it explains. It renders only when some live worker reports a cap, the same way
  the failed chip renders only when something failed.
  - Below the cap: `cap 2/3`, neutral ink.
  - Full with jobs waiting: `at cap 3/3 · 12 waiting`, neutral ink, the numbers in
    `text-fg`. Its tip says these jobs wait on a free slot, not on a worker, and
    that latency grows by design.
  - Workers disagree: `cap mixed 3, 5`, `text-warning`. This is the one state
    that is a problem: each worker enforces its own value. A worker with no cap
    beside capped ones counts as disagreement and shows as `none`.
- **Color keeps its meaning.** Being at the cap is intended behavior, so it
  stays neutral. Amber is spent only on mixed caps. The latency chip keeps its
  own threshold: the backlog is real either way, and the cap chip beside it
  carries the explanation.
- **Live.** The chip lives inside `partials/metrics.html`, already swapped into
  `#metrics-live`, which targets its own id and refreshes on `sse:changed` and a
  30 s tick. No new live region.
- **Workers list.** Each worker row shows its cap when set, so a mixed fleet can
  be traced to the worker.
- **Cost.** One `Queue.workers()` read per strip refresh (pipelined, throttled
  to 5 s by the existing trigger).
- **Dependency.** `toro-queue>=0.6.0`.

## Acceptance clauses

| ID | Behavior | Check |
|---|---|---|
| CV-001 | `Service.metrics()` reports the cap: `None` with no capped live worker, the value when all live workers agree, and the sorted distinct values when they disagree (an uncapped worker among capped ones included). | `tests/integration/test_cap_view.py::test_service_reports_cap_states` |
| CV-002 | No live worker reports a cap: the strip renders no cap chip. | `::test_cap_chip_absent_without_a_cap` |
| CV-003 | Below the cap the chip reads active over cap in neutral ink. | `::test_cap_chip_shows_occupancy` |
| CV-004 | Full with jobs waiting: the chip says so with the waiting count, stays neutral, and its tip names the cap as the cause. | `::test_cap_chip_names_the_cap_as_the_wait` |
| CV-005 | Workers disagree: the chip lists the values in `text-warning`. | `::test_cap_chip_warns_on_mixed_caps` |
| CV-006 | The workers list shows a worker's cap when set and nothing when unset. | `::test_workers_list_shows_the_cap` |
| CV-007 | In a real browser the chip appears once a capped worker is live and the queue fills, without a reload, and the panel stays intact after the live swap. | `tests/e2e/test_cap_view.py::test_cap_chip_goes_live` |
| CV-008 | The chip meets the strip's accessibility bar: the tip is reachable by keyboard and the state is conveyed in text, not by color alone. | `tests/e2e/test_cap_view.py::test_cap_chip_is_accessible` |

## Out of scope

- Setting or changing the cap from the dashboard. toro has no runtime cap.
- A per-job "held by the cap" marker in the jobs table. The cap holds back
  whatever is next in line, not particular jobs.
- The view for jobs held on a `concurrency_key`. toro does not have it yet.

## Risks

- **Stale workers.** `Queue.workers()` prunes records with no heartbeat for 30 s,
  so a crashed capped worker can keep the chip up for that long.
- **No live worker.** With every worker down the cap is unknown and the chip
  disappears, while jobs still wait. The workers view already shows that state.

## Open questions

1. **Wording.** `at cap 3/3 · 12 waiting` versus something shorter.
2. **Latency color at the cap.** Recommended: leave the latency chip's amber
   threshold alone. Alternative: suppress amber while the queue is at its cap.

## Tasks

| # | Clause | Work | Files | Test strategy |
|---|---|---|---|---|
| 1 | CV-001 | Cap summary in `Service.metrics()` | `matador/service.py` | integration, live capped workers, red first |
| 2 | CV-002, CV-003 | The chip, below the cap and absent | `matador/templates/partials/metrics.html` | render the partial, red first |
| 3 | CV-004 | The full state and its tip | same | hold the slots by hand, red first |
| 4 | CV-005 | Mixed caps | same, `matador/service.py` | two workers, two caps, red first |
| 5 | CV-006 | Cap in the workers list | `matador/templates/partials/workers_list.html` | integration, red first |
| 6 | CV-007, CV-008 | Browser behavior | tests only | Playwright: live swap, keyboard, computed style |
| 7 | | `toro-queue>=0.6.0`, docs (`docs/views.md`), rebuilt CSS if a utility is new | `pyproject.toml`, docs | the CSS freshness gate |
| 8 | | Prove: full suite, mutation audit, a live demo against a capped fleet | | evidence captured |
