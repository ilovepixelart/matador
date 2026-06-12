"""Unit: the Jinja filters + schedule label - pure formatting helpers."""

import re

import pytest

from matador import app
from matador.app import _TEMPLATES, _ago, _at, _compact, _due, _dur, _schedule_label, _uptime


def test_schedule_label_cron():
    assert _schedule_label({"cron": "0 0 * * *"}) == "cron 0 0 * * *"


def test_schedule_label_every_seconds():
    assert _schedule_label({"every": 30000}) == "every 30s"


def test_schedule_label_every_minutes():
    assert _schedule_label({"every": 120000}) == "every 2m"


def test_clock_filter():
    clock = _TEMPLATES.env.filters["clock"]
    assert clock(0) == "-"  # falsy → em dash, not "00:00:00"
    assert clock(None) == "-"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", clock(1700000000000))


def test_comma_filter():
    comma = _TEMPLATES.env.filters["comma"]
    assert comma(1234567) == "1,234,567"
    assert comma(0) == "0"


def test_uptime(monkeypatch):
    # Freeze the clock so the elapsed-since-start arithmetic is deterministic.
    monkeypatch.setattr(app.time, "time", lambda: 10_000.0)
    assert _uptime(0) == "-"  # never started
    assert _uptime(10_000_000) == "0s"  # started this instant
    assert _uptime((10_000 - 45) * 1000) == "45s"
    assert _uptime((10_000 - 120) * 1000) == "2m"
    assert _uptime((10_000 - 3700) * 1000) == "1h 1m"
    assert _uptime((10_000 - 7200) * 1000) == "2h"  # exact hours drop the minutes


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "0"),
        (9999, "9999"),  # kept exact below 10k
        (10_000, "10K"),
        (12_345, "12K"),
        (1_500_000, "1.5M"),
        (2_000_000_000, "2B"),  # trailing .0 stripped
        (3_000_000_000_000, "3T"),
    ],
)
def test_compact(n, expected):
    assert _compact(n) == expected


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (None, "-"),
        (0, "-"),
        (-5, "-"),  # nonsense input → dash
        (500, "500ms"),
        (1500, "1.5s"),
        (65_000, "1m 5s"),
        (125_000, "2m 5s"),
    ],
)
def test_dur(ms, expected):
    assert _dur(ms) == expected


def test_ago(monkeypatch):
    monkeypatch.setattr(app.time, "time", lambda: 10_000.0)
    assert _ago(None) == "-"
    assert _ago(0) == "-"
    assert _ago((10_000 - 45) * 1000) == "45s ago"
    assert _ago((10_000 - 300) * 1000) == "5m ago"
    assert _ago((10_000 - 7200) * 1000) == "2h ago"


def test_uptime_days(monkeypatch):
    # Row times can be days old; uptime gains a days unit (workers reuse it too).
    monkeypatch.setattr(app.time, "time", lambda: 1_000_000.0)
    assert _uptime((1_000_000 - 2 * 86_400) * 1000) == "2d"


def test_due(monkeypatch):
    monkeypatch.setattr(app.time, "time", lambda: 10_000.0)
    assert _due(None) == "-"
    assert _due((10_000 - 5) * 1000) == "due now"  # already past
    assert _due((10_000 + 30) * 1000) == "due in 30s"
    assert _due((10_000 + 300) * 1000) == "due in 5m"
    assert _due((10_000 + 7200) * 1000) == "due in 2h"
    assert _due((10_000 + 2 * 86_400) * 1000) == "due in 2d"


def test_at():
    assert _at(None) == ""
    assert _at(0) == ""
    # Absolute moment with the date - relative times age, hover titles must not.
    assert re.fullmatch(r"\d\d \w\w\w \d{4} \d\d:\d\d:\d\d", _at(1700000000000))


def test_asset_version_is_evaluated_per_render():
    # A live server with assets rebuilt underneath it (tailwind --watch, deploys
    # without restart) must hand out fresh ?v= values - an import-time int goes
    # stale and pins every browser to cached assets.
    assert callable(_TEMPLATES.env.globals["asset_v"])
    assert _TEMPLATES.env.globals["asset_v"]() > 0
