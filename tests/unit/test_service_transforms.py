"""Unit: Service._summary / _detail - pure Job→dict shaping for the templates."""

from toro.job import Job, JobOptions

from matador.service import Service


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
    }


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
