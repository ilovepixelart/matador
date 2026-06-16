"""Unit: Service._summary / _detail - pure Job→dict shaping for the templates."""

from toro.job import Job, JobOptions

from matador.service import STATES, Service, _fold_counts


def _job(**over):
    base = {
        "id": "5",
        "name": "send",
        "data": {"to": "ada@example.com"},
        "attempts_made": 2,
        "state": "completed",
        "failed_reason": None,
        "progress": 100,
        "opts": JobOptions(attempts=3, priority=5),
        "returnvalue": {"ok": 1},
        "timestamp": 1700000000000,
        "processed_on": 1700000000100,
        "finished_on": 1700000000500,
        "stacktrace": None,
    }
    base.update(over)
    return Job(**base)


def test_summary_has_exactly_the_list_fields():
    assert Service._summary(_job()) == {
        "id": "5",
        "name": "send",
        "state": "completed",
        "attempts_made": 2,
        "data": {"to": "ada@example.com"},
        "failed_reason": None,
        "progress": 100,
        # triage times: the row template picks the state-relevant one
        "timestamp": 1700000000000,
        "processed_on": 1700000000100,
        "finished_on": 1700000000500,
        "delay": 0,
        # flow membership: a parent shows its child count, a child its parent link
        "parent_id": None,
        "children_count": 0,
    }


def test_summary_carries_flow_membership():
    parent = Service._summary(_job(state="waiting-children", children_ids=["6", "7"]))
    assert parent["children_count"] == 2
    child = Service._summary(_job(id="6", parent_id="5"))
    assert child["parent_id"] == "5"


def test_detail_extends_summary_with_full_metadata():
    d = Service._detail(_job())
    # carries every summary field ...
    assert d["id"] == "5" and d["name"] == "send" and d["progress"] == 100
    # ... plus the detail-only ones
    assert d["opts"] == {
        "delay": 0,
        "attempts": 3,
        "backoff": None,
        "priority": 5,
        "removeOnComplete": None,
        "removeOnFail": None,
    }
    assert d["returnvalue"] == {"ok": 1}
    assert d["timestamp"] == 1700000000000
    assert d["processed_on"] == 1700000000100
    assert d["finished_on"] == 1700000000500
    assert d["stacktrace"] is None


def test_detail_surfaces_failure_info_on_failed_jobs():
    d = Service._detail(
        _job(state="failed", failed_reason="boom", stacktrace="Traceback...\nRuntimeError: boom")
    )
    assert d["state"] == "failed"
    assert d["failed_reason"] == "boom"
    assert "RuntimeError" in d["stacktrace"]


def test_fold_counts_parks_waiting_children_into_active():
    # toro's root counts; the parked-flow state has no tab and folds into active
    folded = _fold_counts(
        {"wait": 3, "active": 2, "delayed": 0, "completed": 9, "failed": 1, "waiting-children": 4}
    )
    assert folded["active"] == 6  # 2 running + 4 parked roots
    assert folded["wait"] == 3 and folded["completed"] == 9 and folded["failed"] == 1
    # the raw parked count is kept (no tab shows it; the active tab's bulk action uses it)
    assert folded["waiting-children"] == 4
    # every rendered tab has a count to show
    assert all(s in folded for s in STATES)


def test_fold_counts_does_not_mutate_its_input():
    raw = {"active": 1, "waiting-children": 2}
    _fold_counts(raw)
    assert raw == {"active": 1, "waiting-children": 2}
