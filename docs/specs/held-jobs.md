# Held jobs on the dashboard

## Problem and outcome

toro 0.8.0 adds `concurrency_key`: jobs that share a key run one at a time, and a
job waiting for a key sits in a new state, `held`. matador knows five tabs and
folds `waiting-children` into `active`, so a held job is invisible: it is in no
tab, in no badge, and nothing on the page says why a queue with queued work is
not draining.

Outcome: `held` is a tab of its own, with the same badge, paging, search, bulk
select and clean as any other state, and a job's row and detail name the key it
waits on.

## Design

- **A tab, not a fold.** `waiting-children` folds into `active` because a parked
  parent IS in flight. A held job is not waiting for a worker: folding it into
  `wait` would make the waiting badge read as backlog when no worker could take
  any of it, and the wait tab's order (global priority) is not the order held jobs
  will run in. It gets its own tab, after `wait`.
- **Nothing else changes.** `held` is a plain ZSET-backed state in toro, so
  `STATES` is the only list it has to join: paging, search, the bulk bar, clean
  and the live refresh all read `STATES` and `state`.
- **The key is on the row.** A held row shows the key it waits on, and the detail
  shows it for every job that has one: a running job's key is what the held jobs
  behind it are waiting for.
- **toro 0.8.0 is the floor.** `get_jobs_roots("held")` does not exist before it.

## Acceptance clauses

| ID | Behavior | Check |
|---|---|---|
| HJ-001 | `held` is a tab with its own badge, and the badge is the exact number of held roots the tab pages through. | `tests/integration/test_roots_listing.py::test_the_held_tab_pages_its_own_roots`, `tests/integration/test_routes.py::test_held_is_a_tab_of_its_own` |
| HJ-002 | A held row names the key it waits on; a row whose job has no key shows nothing extra. | `tests/integration/test_row_triage.py::test_a_held_row_names_the_key_it_waits_on`, `::test_rows_without_a_key_say_nothing_about_one` |
| HJ-003 | The job detail shows the concurrency key of any job that has one. | `tests/integration/test_routes.py::test_the_detail_shows_a_concurrency_key` |
| HJ-004 | `clean("held")` is allowed and removes every held job; bulk select and delete work on the tab. | `tests/integration/test_clean.py::test_held_jobs_can_be_cleaned` |
| HJ-005 | The held tab renders end to end: a held job is visible in a browser, names its key, and leaves the tab when the key frees and it runs. | `tests/e2e/test_live_table.py::test_a_held_job_appears_and_leaves` |

## Out of scope

- Showing which job holds a key, or how long the queue behind it is. The key is
  named; following it is a search.
- Reordering or promoting a held job. toro has no such operation: a held job runs
  when its key frees.

## Risks

- **A sixth tab is more chrome for the majority who never set a key.** Accepted:
  the tab is always present, like `delayed`, and reads 0.

## Tasks

| # | Clause | Work | Files | Test strategy |
|---|---|---|---|---|
| 1 | HJ-001 | `held` in `STATES`, its token/colour/label | `matador/service.py`, `matador/templates/macros.html` | red first |
| 2 | HJ-002, HJ-003 | The key in the row summary and the detail | `matador/service.py`, `matador/templates/` | red first |
| 3 | HJ-004 | `held` in `CLEANABLE_STATES` | `matador/app.py` | red first |
| 4 | HJ-005 | The browser pass | `tests/e2e/` | e2e |
| 5 | | Docs, the toro floor, the release | `docs/`, `pyproject.toml` | review |
