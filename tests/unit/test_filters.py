"""Unit: the Jinja filters + schedule label — pure formatting helpers."""

import re

import pytest

from matador import app
from matador.app import _TEMPLATES, _compact, _dur, _schedule_label, _uptime


def test_schedule_label_cron():
    assert _schedule_label({"cron": "0 0 * * *"}) == "cron 0 0 * * *"


def test_schedule_label_every_seconds():
    assert _schedule_label({"every": 30000}) == "every 30s"


def test_schedule_label_every_minutes():
    assert _schedule_label({"every": 120000}) == "every 2m"


def test_clock_filter():
    clock = _TEMPLATES.env.filters["clock"]
    assert clock(0) == "—"  # falsy → em dash, not "00:00:00"
    assert clock(None) == "—"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", clock(1700000000000))


def test_comma_filter():
    comma = _TEMPLATES.env.filters["comma"]
    assert comma(1234567) == "1,234,567"
    assert comma(0) == "0"


def test_uptime(monkeypatch):
    # Freeze the clock so the elapsed-since-start arithmetic is deterministic.
    monkeypatch.setattr(app.time, "time", lambda: 10_000.0)
    assert _uptime(0) == "—"  # never started
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
        (None, "—"),
        (0, "—"),
        (-5, "—"),  # nonsense input → dash
        (500, "500ms"),
        (1500, "1.5s"),
        (65_000, "1m 5s"),
        (125_000, "2m 5s"),
    ],
)
def test_dur(ms, expected):
    assert _dur(ms) == expected
