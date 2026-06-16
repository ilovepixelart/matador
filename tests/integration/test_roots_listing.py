"""Integration: the root-first listing over real Redis, driving Service against a
seeded toro queue. This is what the toro root index bought us over the old bounded
scan: EXACT tab counts and UNBOUNDED, deep pagination of roots-only, with the badge
and the pager always agreeing - children hidden from both.
"""

import pytest
from toro import FlowChild as c  # noqa: N813 - `c("fetch", ...)` keeps trees readable

from matador.service import Service

PREFIX = "matadortest"
QUEUE = "testq"
PER_PAGE = 20


@pytest.fixture
async def svc(q):
    # share the test queue's connection so Service sees exactly what `q` seeds
    return Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX, connection=q.redis)


async def _seed_completed(q, n, *, prefix="d"):
    """n completed ROOT jobs, oldest first (so newest-first paging is checkable)."""
    pipe = q.redis.pipeline(transaction=False)
    for i in range(n):
        jid = f"{prefix}{i}"
        pipe.hset(q.keys.job(jid), mapping={"id": jid, "name": jid, "state": "completed"})
        pipe.zadd(q.keys.completed, {jid: i})
    await pipe.execute()


async def _seed_active(q, n, *, prefix="a"):
    """n active ROOT jobs in the active LIST, in order a0..a{n-1}."""
    pipe = q.redis.pipeline(transaction=False)
    for i in range(n):
        jid = f"{prefix}{i}"
        pipe.hset(q.keys.job(jid), mapping={"id": jid, "name": jid, "state": "active"})
        pipe.rpush(q.keys.active, jid)
    await pipe.execute()


async def _seed_parked(q, n, *, prefix="w"):
    """n parked flow ROOTS (waiting-children, each with a child list, no parentId)."""
    pipe = q.redis.pipeline(transaction=False)
    for i in range(n):
        jid = f"{prefix}{i}"
        pipe.hset(
            q.keys.job(jid),
            mapping={"id": jid, "name": jid, "state": "waiting-children", "children": '["x"]'},
        )
        pipe.zadd(q.keys.waiting_children, {jid: i})
    await pipe.execute()


# ---- children hidden; badge and pager agree -----------------------------------------


async def test_flow_children_hidden_and_root_shown(svc, q):
    parent = await q.add_flow("report", {}, children=[c("fetch", {}), c("fetch", {})])
    await q.add("solo", {})  # a plain root in wait

    view = await svc.queue_view(QUEUE)
    assert view["counts"]["wait"] == 1  # only `solo`; the 2 children are hidden
    assert view["counts"]["active"] == 1  # the parked flow root folds into active

    rows, total, _ = await svc.jobs(QUEUE, "active", 1, PER_PAGE)
    assert total == view["counts"]["active"] == 1  # badge and pager agree
    assert [r["id"] for r in rows] == [parent.id]
    wait_rows, wait_total, _ = await svc.jobs(QUEUE, "wait", 1, PER_PAGE)
    assert wait_total == 1 and all(r["id"] != parent.id for r in wait_rows)


# ---- the fix: exact counts and unbounded deep paging --------------------------------


async def test_completed_count_is_exact_past_the_old_500_cap(svc, q):
    await _seed_completed(q, 600)  # the old ROOT_SCAN_CAP was 500
    view = await svc.queue_view(QUEUE)
    assert view["counts"]["completed"] == 600  # not clamped at 500

    _, total, _ = await svc.jobs(QUEUE, "completed", 1, PER_PAGE)
    assert total == 600


async def test_deep_page_past_500_still_returns_rows(svc, q):
    await _seed_completed(q, 600)
    # completed pages newest-first: d599..d0. Page 1 = d599..d580.
    page1, _, _ = await svc.jobs(QUEUE, "completed", 1, PER_PAGE)
    assert [r["id"] for r in page1] == [f"d{i}" for i in range(599, 579, -1)]
    # page 30 (rows 580..599 of the listing) is well past the old cap and must resolve
    page30, total, page = await svc.jobs(QUEUE, "completed", 30, PER_PAGE)
    assert total == 600 and page == 30
    assert [r["id"] for r in page30] == [f"d{i}" for i in range(19, -1, -1)]


async def test_page_beyond_the_end_clamps_to_the_last_page(svc, q):
    await _seed_completed(q, 25)  # two pages of roots
    rows, total, page = await svc.jobs(QUEUE, "completed", 99, PER_PAGE)
    assert total == 25 and page == 2  # clamped
    assert len(rows) == 5  # the tail page


# ---- active = active roots ++ parked flow roots, paged as one list ------------------


async def test_active_tab_pages_across_the_active_parked_boundary(svc, q):
    await _seed_active(q, 15)  # a0..a14 in the active list
    await _seed_parked(q, 30)  # w0..w29 parked flow roots

    view = await svc.queue_view(QUEUE)
    assert view["counts"]["active"] == 45  # 15 active + 30 parked, exact

    page1, total, _ = await svc.jobs(QUEUE, "active", 1, PER_PAGE)
    assert total == 45
    # active roots come first, then the parked roots spill into the same page
    assert [r["id"] for r in page1] == [f"a{i}" for i in range(15)] + [f"w{i}" for i in range(5)]

    page2, _, _ = await svc.jobs(QUEUE, "active", 2, PER_PAGE)
    assert [r["id"] for r in page2] == [f"w{i}" for i in range(5, 25)]  # all parked

    page3, _, page = await svc.jobs(QUEUE, "active", 3, PER_PAGE)
    assert page == 3 and [r["id"] for r in page3] == [f"w{i}" for i in range(25, 30)]


async def test_active_tab_clamps_past_the_end(svc, q):
    await _seed_active(q, 3)
    await _seed_parked(q, 4)
    rows, total, page = await svc.jobs(QUEUE, "active", 9, PER_PAGE)
    assert total == 7 and page == 1  # one page only, clamped back
    assert [r["id"] for r in rows] == ["a0", "a1", "a2", "w0", "w1", "w2", "w3"]


async def test_empty_state_lists_nothing(svc, q):
    rows, total, page = await svc.jobs(QUEUE, "failed", 1, PER_PAGE)
    assert rows == [] and total == 0 and page == 1
