# Live updates that land the last change

## Problem and outcome

After a burst of job events the dashboard can show a state that is no longer
true until the 8 s heartbeat corrects it: the last job of a batch still listed
as active, a count one short. Measured on the real stream: a job completing
100 ms after another went unannounced for 8.10 s.

The last change of a burst is lost in three places, each of which fires on the
first event of a window and discards the rest:

1. **The server coalescer.** `Service.event_stream` emits `changed`, sleeps
   200 ms, then clears the wake flag, which discards every event that arrived
   during the sleep. The refresh the first emit triggered was fetched at the
   start of that window, so those events are in nobody's repaint.
2. **The client throttle.** htmx's `throttle` fires on the first event and
   returns early for the rest of the window, with no trailing request (the 2.0.10
   source; the documentation's "will trigger at the end of the delay" does not
   match it). Throttle state is per element, so a second trigger on the same
   element cannot recover it.
3. **`hx-sync="this:drop"`.** A refresh that arrives while one is in flight is
   dropped. Five regions set it. (An element with no `hx-sync` queues the last
   one; `drop` is the default of an `hx-sync` that names no strategy.)

Outcome: the last change of any burst reaches every live region within that
region's own cadence, with the rate limits the dashboard has today.

## Design

- **One state machine decides the cadence; the stream only performs it.**
  `matador/cadence.py`: a `Rate(interval, heartbeat)` emits at once when a change
  finds its window open and closes the window for `interval`; a change that
  finds it closed is owed, and an owed window emits once when it reopens. A
  heartbeat is a change nobody published, so it is owed the same way. `Cadence`
  holds the stream's rates and answers two questions: what is due now, and when to
  look again. Both take the clock as an argument, so every timing rule is tested
  exactly, without sleeping, including under random traffic and on a clock that
  ticks in milliseconds. The async loop reports facts (the clock, whether a
  change arrived, whether its wait ran its course) and makes no decision.
- **Cadence moves from the templates to the stream.** Regions refresh at three
  rates today (400 ms, 1 s, 5 s), set by client throttles that cannot be given a
  trailing edge. The stream emits one event per rate, and the templates listen
  with no `throttle`:

  | Event | Interval | Regions |
  |---|---|---|
  | `changed-fast` | 400 ms | sidebar |
  | `changed` | 1 s | job list, workers, flow section, Redis bar |
  | `changed-slow` | 5 s | the queue's health strips |

- **`hx-sync="this:queue last"`** on those regions: a refresh arriving while one
  is in flight is kept, one deep, instead of dropped.
- **The heartbeat stays at 8 s, counted per rate.** A rate that has sent nothing
  for 8 seconds emits, as `changed` does today. It keeps the connection alive, refreshes relative times,
  and covers the transitions toro does not publish.
- **A stream's first heartbeat is due at once.** Pub/sub has no replay: a change
  published between the page's render and the subscription, or during a
  reconnect, reaches nobody. Every rate emits once when the stream is
  subscribed, like any beat.
- **Per-stream state** is one `Cadence` and one wake flag. The shared
  broadcaster and its single subscription are untouched.

## Acceptance clauses

| ID | Behavior | Check |
|---|---|---|
| LT-001 | A change in an open window emits at once. Changes in a closed window produce exactly one emit, when the window reopens, however many there were. A window left clean emits nothing until its heartbeat. | `tests/unit/test_cadence.py` (fake clock) |
| LT-002 | Under sustained changes a rate emits at most once per interval, and one last time after the final change. For any traffic: no change is lost, no rate stays silent past its heartbeat, and the stream neither spins nor wakes once per event, on an ideal clock and on one that ticks in milliseconds. | `tests/unit/test_cadence.py::test_storm_is_capped_and_lands_its_tail`, `::test_invariants_hold_for_any_traffic`, `::test_a_millisecond_clock_cannot_make_the_stream_spin` |
| LT-003 | On the real stream, a job event arriving 100 ms after another is announced within each rate's interval, not by the heartbeat. | `tests/integration/test_stream.py::test_second_event_in_a_window_is_announced` (per rate) |
| LT-004 | A rate that has sent nothing for 8 seconds emits, whatever the other rates sent since, and the stream still starts with its `retry` directive, shares one subscription, stops on disconnect, and ends cleanly when the subscription dies. | the existing `tests/integration/test_stream.py`, extended to the three events |
| LT-005 | No live region carries a `throttle` on an `sse:` trigger, and every region that listens to the stream syncs with `queue last`. | `tests/integration/test_markup.py::test_live_regions_use_server_cadence` |
| LT-006 | In a real browser, a job that finishes after the page has repainted for the previous finish, so inside the closed window, leaves the active list within 2 s, and the sidebar agrees. | `tests/e2e/test_live.py::test_last_finish_of_a_burst_lands` |
| LT-007 | A region still targets its own stable id and the panel survives a live refresh that fires after navigation. | the existing e2e guards, unchanged and green |
| LT-008 | A stream announces every rate as soon as it is subscribed, with nothing published, so a page catches up on connect and reconnect. | `tests/integration/test_stream.py::test_a_stream_beats_as_soon_as_it_is_subscribed` |

## Out of scope

- **Transitions toro does not publish.** toro publishes `added`, `completed`,
  `failed` and `progress`. A claim, a retry, a delayed job's promotion and a
  stalled job's recovery publish nothing, so they reach the dashboard only with
  the heartbeat: measured, a claim went unannounced for 7.18 s. No cadence can
  announce an event that was never sent. This belongs to toro, as a published
  `active` event with a measured cost on the claim path.
- Keyboard access to tips, and a console-error guard for the browser suite.

## Risks

- **The stream's contract changes.** Two new event names, and `changed` slows
  from a 200 ms to a 1 s cadence. Anything outside the dashboard that reads
  `/stream` sees that. Nothing in the repository does.
- **More refreshes at the end of a burst.** Every burst now ends with one extra
  refresh per region, which is the point. Sustained load is unchanged: one
  refresh per interval per region.
- **`queue last` under a slow server.** A region whose refresh takes longer than
  its interval always has one request queued. It cannot grow past one.
- **Morph and the jobs table.** The job list's live refresh is the most delicate
  swap in the dashboard. Its trigger changes; its target, swap and sync element
  do not. LT-007 is the guard.

## Open questions

1. **Names.** `changed-fast` / `changed` / `changed-slow`, or names that state
   the interval.
2. **Should toro's `active` event come first?** It fixes the lag that was
   actually observed on the cap chip. This spec fixes a different, equally real
   one. They are independent and can land in either order.

## Tasks

| # | Clause | Work | Files | Test strategy |
|---|---|---|---|---|
| 1 | LT-001, LT-002 | `Rate` and `Cadence` | `matador/cadence.py` | unit, fake clock, red first; a simulated stream under random traffic |
| 2 | LT-003, LT-004 | `event_stream` performs what the cadence decides | `matador/service.py` | the real stream, red first |
| 3 | LT-005 | Triggers and sync on every live region | templates | a markup test over every `sse:` trigger, red first |
| 4 | LT-006, LT-007 | Browser behavior | tests only | two real finishes, the second released once the first is painted |
| 5 | | Docs: `docs/live-updates.md` | docs | review |
| 6 | | Prove: full suite, mutation audit, the stream measurement rerun, a look at the live dashboard | | evidence captured |
