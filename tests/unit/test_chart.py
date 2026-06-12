"""The metrics chart geometry: per-minute counts -> stacked SVG bar rects."""

from matador.app import CHART_H, CHART_STEP, SPARK_H, _chart_bars, _sparkbars, _squash


def _pt(ts=0, completed=0, failed=0, ms=0):
    return {"timestamp": ts, "completed": completed, "failed": failed, "ms": ms}


def test_all_zero_points_produce_no_bars():
    bars = _chart_bars([_pt(), _pt(), _pt()])
    assert all(b["dh"] == 0 and b["fh"] == 0 for b in bars)


def test_peak_minute_fills_the_chart_height():
    bars = _chart_bars([_pt(completed=10), _pt(completed=40, failed=10), _pt(completed=5)])
    peak = bars[1]
    assert peak["dh"] + peak["fh"] == CHART_H


def test_failed_stacks_on_top_of_completed():
    (b,) = _chart_bars([_pt(completed=30, failed=10)])
    assert b["y_done"] == CHART_H - b["dh"]
    assert b["y_fail"] == CHART_H - b["dh"] - b["fh"]
    assert b["fh"] > 0


def test_tiny_counts_stay_visible():
    # 1 job next to a 1000-job peak must still paint a visible sliver
    bars = _chart_bars([_pt(completed=1), _pt(completed=1000)])
    assert bars[0]["dh"] >= 1


def test_bars_never_overflow_the_height():
    # min-height slivers on BOTH series can't push the stack past the top
    (b,) = _chart_bars([_pt(completed=1, failed=1)])
    assert b["dh"] + b["fh"] <= CHART_H


def test_x_advances_one_step_per_minute():
    bars = _chart_bars([_pt(), _pt(), _pt()])
    assert [b["x"] for b in bars] == [0, CHART_STEP, 2 * CHART_STEP]


def test_original_point_fields_survive():
    (b,) = _chart_bars([_pt(ts=1_700_000_000_000, completed=2, failed=1)])
    assert b["timestamp"] == 1_700_000_000_000
    assert b["completed"] == 2
    assert b["failed"] == 1


def test_explicit_peak_overrides_local_scaling():
    # small-multiples honesty: all queues share one scale
    (b,) = _chart_bars([_pt(completed=10)], peak=100)
    assert 0 < b["dh"] < CHART_H / 2


def test_squash_sums_consecutive_minutes():
    pts = [_pt(ts=i * 60_000, completed=1, failed=1, ms=10) for i in range(60)]
    out = _squash(pts, 30)
    assert len(out) == 30
    assert all(p["completed"] == 2 and p["failed"] == 2 and p["ms"] == 20 for p in out)
    assert out[0]["timestamp"] == 0
    assert out[1]["timestamp"] == 120_000


def test_sparkbars_share_one_peak():
    queues = [
        {"name": "big", "spark": [_pt(completed=100)]},
        {"name": "small", "spark": [_pt(completed=10)]},
    ]
    sb = _sparkbars(queues)
    assert sb["big"][0]["dh"] == SPARK_H  # the busiest queue fills its cell
    assert sb["small"][0]["dh"] < SPARK_H / 2  # the quiet one stays visibly smaller


def test_sliver_overflow_is_clamped_back_into_the_chart():
    # a near-full bar plus a minimum-height sliver overflows the height; the
    # stack must scale back down instead of poking out of the chart
    (b,) = _chart_bars([_pt(completed=99, failed=1)])
    assert b["dh"] + b["fh"] <= CHART_H
    assert b["fh"] > 0  # the sliver survives the clamp
